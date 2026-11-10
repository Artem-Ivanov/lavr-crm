import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from .common import ApiError, new_id, utcnow
from .config import settings
from .crypto import lookup_digest, open_json, open_text, seal_json, seal_text
from .db import Base, get_db

router = APIRouter(prefix='/api')
password_hasher = PasswordHasher()
unknown_password_hash = password_hasher.hash(secrets.token_urlsafe(24))


class User(Base):
    __tablename__ = 'users'
    __table_args__ = (CheckConstraint("role IN ('partner', 'operator', 'admin')", name='users_role_check'),)
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    login_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    login_lookup: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    name_ciphertext: Mapped[str] = mapped_column(Text, default='')
    role: Mapped[str] = mapped_column(String(20), default='partner')
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    auth_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    email_lookup: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    phone_lookup: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    profile_ciphertext: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    def _context(self, field):
        if not self.id:
            self.id = new_id()
        return {'table': 'users', 'record_id': self.id, 'field': field}

    @property
    def login(self):
        return open_text(self.login_ciphertext, **self._context('login')) if self.login_ciphertext is not None else None

    @login.setter
    def login(self, value):
        self.login_ciphertext = seal_text(value, **self._context('login')) if value is not None else None
        self.login_lookup = lookup_digest('legacy_login', value) if value is not None else None

    @property
    def name(self):
        return open_text(self.name_ciphertext, **self._context('name')) if self.name_ciphertext else ''

    @name.setter
    def name(self, value):
        self.name_ciphertext = seal_text(value, **self._context('name'))

    @property
    def profile(self):
        return open_json(self.profile_ciphertext, **self._context('profile')) if self.profile_ciphertext is not None else {}

    @profile.setter
    def profile(self, value):
        self.profile_ciphertext = seal_json(value, **self._context('profile'))
        self.email_lookup = lookup_digest('email', value['email']) if value.get('email') else None
        self.phone_lookup = lookup_digest('phone', value['phone']) if value.get('phone') else None


class UserSession(Base):
    __tablename__ = 'user_sessions'
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    auth_version: Mapped[int] = mapped_column(Integer)


class LoginThrottle(Base):
    __tablename__ = 'login_throttles'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEntry(Base):
    __tablename__ = 'audit_entries'
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    staff_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    action: Mapped[str] = mapped_column(String(128))
    entity_id: Mapped[str] = mapped_column(String(128))
    version: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def record_audit(db, staff, action, entity_id, version=None, details=None):
    db.add(AuditEntry(id=new_id(), staff_id=staff.id, action=action, entity_id=str(entity_id), version=version, details=details))


def hash_password(password):
    return password_hasher.hash(password)


def csrf_token(session):
    return hmac.new(settings.session_secret.encode(), (session.id + session.token_hash).encode(), hashlib.sha256).hexdigest()


def check_origin(request):
    if request.headers.get('origin') not in settings.allowed_origins:
        raise ApiError(403, 'INVALID_ORIGIN', 'Недопустимый источник запроса.')


def _current_identity(request, db):
    token = request.cookies.get('lavr_session', '')
    if not 32 <= len(token) <= 256:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    session = db.scalar(select(UserSession).where(UserSession.token_hash == digest, UserSession.expires_at > utcnow()))
    user = db.get(User, session.user_id) if session else None
    if not user or not user.is_active or session.auth_version != user.auth_version:
        return None
    request.state.user_session = request.state.staff_session = session
    return user


def get_current_user(request: Request, db: Session = Depends(get_db)):
    user = _current_identity(request, db)
    if user is None:
        raise ApiError(401, 'UNAUTHORIZED', 'Сессия завершена. Войдите снова.')
    return user


def get_current_staff(user: User = Depends(get_current_user)):
    if user.role not in ('operator', 'admin'):
        raise ApiError(403, 'FORBIDDEN', 'Недостаточно прав для этого раздела.')
    return user


def require_admin(user: User = Depends(get_current_user)):
    if user.role != 'admin':
        raise ApiError(403, 'FORBIDDEN', 'Нужны права администратора.')
    return user


def _check_mutation(request):
    check_origin(request)
    supplied = request.headers.get('x-csrf-token', '')
    if not hmac.compare_digest(supplied.encode(), csrf_token(request.state.user_session).encode()):
        raise ApiError(403, 'INVALID_CSRF', 'Повторите вход в кабинет.')


def require_user_mutation(request: Request, user: User = Depends(get_current_user)):
    _check_mutation(request)
    return user


def require_mutation(request: Request, user: User = Depends(get_current_staff)):
    _check_mutation(request)
    return user


def require_admin_mutation(request: Request, user: User = Depends(require_admin)):
    _check_mutation(request)
    return user


def complete_profile(user):
    profile = user.profile
    return bool(user.email_verified_at and profile.get('email') and profile.get('phone'))


def get_current_customer(user: User = Depends(get_current_user)):
    if not complete_profile(user):
        raise ApiError(409, 'PROFILE_REQUIRED', 'Подтвердите email и заполните профиль.')
    return user


def require_customer_mutation(request: Request, user: User = Depends(get_current_customer)):
    _check_mutation(request)
    return user


def guest_only(request: Request, db: Session = Depends(get_db)):
    check_origin(request)
    if 'lavr_session' not in request.cookies:
        return
    get_current_user(request, db)
    raise ApiError(409, 'CUSTOMER_FLOW_REQUIRED', 'Используйте личный кабинет для отправки.')


def normalize_email(value):
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(r'[^\s@\x00-\x1f\x7f]+@[^\s@\x00-\x1f\x7f]+\.[^\s@\x00-\x1f\x7f]+', value):
        raise ValueError('Проверьте email.')
    return value


class LoginInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    login: Annotated[str, Field(min_length=1, max_length=254)]
    password: Annotated[str, Field(min_length=1, max_length=256)]

    @field_validator('login')
    @classmethod
    def normalize_email(cls, value):
        return normalize_email(value)

    @field_validator('password')
    @classmethod
    def database_text(cls, value):
        if '\x00' in value:
            raise ValueError('Недопустимый символ в тексте.')
        return value


class EmailLoginInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    email: Annotated[str, Field(min_length=1, max_length=254)]
    password: Annotated[str, Field(min_length=1, max_length=256)]

    @field_validator('email')
    @classmethod
    def normalize_email(cls, value):
        return normalize_email(value)

    @field_validator('password')
    @classmethod
    def database_text(cls, value):
        if '\x00' in value:
            raise ValueError('Недопустимый символ в тексте.')
        return value


def session_response(user, session):
    profile = user.profile
    return {'user': {'id': user.id, 'name': user.name, 'email': profile.get('email'), 'phone': profile.get('phone'),
                     'emailVerified': user.email_verified_at is not None, 'phoneVerified': False,
                     'profileComplete': complete_profile(user)},
            'capabilities': {'operate': user.role in ('operator', 'admin'), 'editSite': user.role == 'admin', 'manageUsers': user.role == 'admin'},
            'csrfToken': csrf_token(session), 'expiresAt': session.expires_at.isoformat()}


def create_session(db, user, request, response):
    db.flush()
    db.refresh(user, with_for_update=True)
    if not user.is_active:
        raise ApiError(401, 'INVALID_CREDENTIALS', 'Неверный логин или пароль.')
    old_token = request.cookies.get('lavr_session')
    if old_token and len(old_token) <= 256:
        previous = db.scalar(select(UserSession).where(UserSession.token_hash == hashlib.sha256(old_token.encode()).hexdigest()))
        if previous:
            db.delete(previous)
    token = secrets.token_urlsafe(32)
    session = UserSession(id=new_id(), user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                          expires_at=utcnow() + timedelta(hours=settings.session_hours), auth_version=user.auth_version)
    db.add(session)
    response.set_cookie('lavr_session', token, httponly=True, secure=settings.cookie_secure, samesite='strict', path='/api', max_age=settings.session_hours * 3600)
    return session


def _login(email, password, request, response, db):
    check_origin(request)
    digest = lookup_digest('email', email)
    now = utcnow()
    db.execute(insert(LoginThrottle).values(key=digest, failures=0, window_start=now).on_conflict_do_nothing())
    throttle = db.scalar(select(LoginThrottle).where(LoginThrottle.key == digest).with_for_update())
    if throttle.window_start < now - timedelta(minutes=15):
        throttle.failures, throttle.window_start = 0, now
    if throttle.failures >= 5:
        raise ApiError(429, 'LOGIN_RATE_LIMIT', 'Слишком много попыток. Повторите позже.', headers={'Retry-After': '900'})
    query = select(User).where(User.is_active.is_(True), User.email_lookup == digest, User.email_verified_at.is_not(None))
    user = db.scalar(query.with_for_update())
    try:
        verified = password_hasher.verify(user.password_hash if user else unknown_password_hash, password)
    except VerificationError:
        verified = False
    if not user or not verified:
        throttle.failures += 1
        db.commit()
        raise ApiError(401, 'INVALID_CREDENTIALS', 'Неверный логин или пароль.')
    throttle.failures = 0
    if password_hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    session = create_session(db, user, request, response)
    record_audit(db, user, 'login', user.id)
    db.commit()
    return session_response(user, session)


@router.post('/manager/session', operation_id='loginManager')
def login_manager(body: LoginInput, request: Request, response: Response, db: Session = Depends(get_db)):
    return _login(body.login, body.password, request, response, db)


@router.post('/customer/session', operation_id='loginUser')
def login_user(body: EmailLoginInput, request: Request, response: Response, db: Session = Depends(get_db)):
    return _login(body.email, body.password, request, response, db)


@router.get('/manager/session', operation_id='getManagerSession')
@router.get('/customer/session', operation_id='getUserSession')
def get_user_session(request: Request, user: User = Depends(get_current_user)):
    return session_response(user, request.state.user_session)


@router.delete('/manager/session', status_code=204, operation_id='logoutManager')
@router.delete('/customer/session', status_code=204, operation_id='logoutUser')
def logout_user(request: Request, response: Response, db: Session = Depends(get_db)):
    check_origin(request)
    user = _current_identity(request, db)
    if user:
        _check_mutation(request)
        db.delete(request.state.user_session)
        record_audit(db, user, 'logout', user.id)
        db.commit()
    response.delete_cookie('lavr_session', path='/api', secure=settings.cookie_secure, httponly=True, samesite='strict')
