import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from .common import ApiError, new_id, utcnow
from .config import settings
from .db import Base, get_db

router = APIRouter(prefix='/api')
password_hasher = PasswordHasher()
unknown_password_hash = password_hasher.hash(secrets.token_urlsafe(24))


class Staff(Base):
    __tablename__ = 'staff'
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    login: Mapped[str] = mapped_column(String(120), unique=True)
    name: Mapped[str] = mapped_column(String(240))
    role: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class StaffSession(Base):
    __tablename__ = 'staff_sessions'
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    staff_id: Mapped[str] = mapped_column(ForeignKey('staff.id'), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), index=True)


class LoginThrottle(Base):
    __tablename__ = 'login_throttles'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[object] = mapped_column(DateTime(timezone=True))


class AuditEntry(Base):
    __tablename__ = 'audit_entries'
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    staff_id: Mapped[str] = mapped_column(ForeignKey('staff.id'), index=True)
    action: Mapped[str] = mapped_column(String(128))
    entity_id: Mapped[str] = mapped_column(String(128))
    version: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)


def record_audit(db, staff, action, entity_id, version=None):
    db.add(AuditEntry(id=new_id(), staff_id=staff.id, action=action, entity_id=str(entity_id), version=version))


def hash_password(password):
    return password_hasher.hash(password)


def csrf_token(session):
    return hmac.new(settings.session_secret.encode(), (session.id + session.token_hash).encode(), hashlib.sha256).hexdigest()


def check_origin(request):
    if request.headers.get('origin') not in settings.allowed_origins:
        raise ApiError(403, 'INVALID_ORIGIN', 'Недопустимый источник запроса.')


def get_current_staff(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get('lavr_session', '')
    if not 32 <= len(token) <= 256:
        raise ApiError(401, 'UNAUTHORIZED', 'Войдите в кабинет.')
    digest = hashlib.sha256(token.encode()).hexdigest()
    session = db.scalar(select(StaffSession).where(StaffSession.token_hash == digest, StaffSession.expires_at > utcnow()))
    staff = db.get(Staff, session.staff_id) if session else None
    if not staff or not staff.is_active or staff.role not in ('admin', 'manager'):
        raise ApiError(401, 'UNAUTHORIZED', 'Сессия завершена. Войдите снова.')
    request.state.staff_session = session
    return staff


def require_admin(staff: Staff = Depends(get_current_staff)):
    if staff.role != 'admin':
        raise ApiError(403, 'FORBIDDEN', 'Нужны права администратора.')
    return staff


def require_mutation(request: Request, staff: Staff = Depends(get_current_staff)):
    check_origin(request)
    supplied = request.headers.get('x-csrf-token', '')
    if not hmac.compare_digest(supplied.encode(), csrf_token(request.state.staff_session).encode()):
        raise ApiError(403, 'INVALID_CSRF', 'Повторите вход в кабинет.')
    return staff


def require_admin_mutation(staff: Staff = Depends(require_mutation)):
    if staff.role != 'admin':
        raise ApiError(403, 'FORBIDDEN', 'Нужны права администратора.')
    return staff


class LoginInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    login: Annotated[str, Field(min_length=1, max_length=120)]
    password: Annotated[str, Field(min_length=1, max_length=256)]

    @field_validator('*')
    @classmethod
    def database_text(cls, value):
        if '\x00' in value:
            raise ValueError('Недопустимый символ в тексте.')
        return value


def session_response(staff, session):
    return {'user': {'id': staff.id, 'name': staff.name, 'role': staff.role},
            'csrfToken': csrf_token(session), 'expiresAt': session.expires_at.isoformat()}


@router.post('/manager/session', operation_id='loginManager')
def login_manager(body: LoginInput, request: Request, response: Response, db: Session = Depends(get_db)):
    check_origin(request)
    login = body.login.strip()
    key = hashlib.sha256(login.casefold().encode()).hexdigest()
    now = utcnow()
    db.execute(insert(LoginThrottle).values(key=key, failures=0, window_start=now).on_conflict_do_nothing())
    throttle = db.scalar(select(LoginThrottle).where(LoginThrottle.key == key).with_for_update())
    if throttle.window_start < now - timedelta(minutes=15):
        throttle.failures, throttle.window_start = 0, now
    if throttle.failures >= 5:
        raise ApiError(429, 'LOGIN_RATE_LIMIT', 'Слишком много попыток. Повторите позже.', headers={'Retry-After': '900'})
    staff = db.scalar(select(Staff).where(Staff.login == login, Staff.is_active.is_(True)))
    try:
        verified = password_hasher.verify(staff.password_hash if staff else unknown_password_hash, body.password)
    except VerificationError:
        verified = False
    if not staff or not verified:
        throttle.failures += 1
        db.commit()
        raise ApiError(401, 'INVALID_CREDENTIALS', 'Неверный логин или пароль.')
    throttle.failures = 0
    if password_hasher.check_needs_rehash(staff.password_hash):
        staff.password_hash = hash_password(body.password)
    old_token = request.cookies.get('lavr_session')
    if old_token:
        previous = db.scalar(select(StaffSession).where(StaffSession.token_hash == hashlib.sha256(old_token.encode()).hexdigest()))
        if previous:
            db.delete(previous)
    token = secrets.token_urlsafe(32)
    session = StaffSession(id=new_id(), staff_id=staff.id, token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=now + timedelta(hours=settings.session_hours))
    db.add(session)
    record_audit(db, staff, 'login', staff.id)
    db.commit()
    response.set_cookie('lavr_session', token, httponly=True, secure=settings.cookie_secure, samesite='strict', path='/api', max_age=settings.session_hours * 3600)
    return session_response(staff, session)


@router.get('/manager/session', operation_id='getManagerSession')
def get_manager_session(request: Request, staff: Staff = Depends(get_current_staff)):
    return session_response(staff, request.state.staff_session)


@router.delete('/manager/session', status_code=204, operation_id='logoutManager')
def logout_manager(request: Request, response: Response, staff: Staff = Depends(require_mutation), db: Session = Depends(get_db)):
    db.delete(request.state.staff_session)
    record_audit(db, staff, 'logout', staff.id)
    db.commit()
    response.delete_cookie('lavr_session', path='/api', secure=settings.cookie_secure, httponly=True, samesite='strict')
