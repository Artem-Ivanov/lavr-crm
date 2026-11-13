"""Email challenges share the existing User identity and transactional outbox."""
import hashlib
import hmac
import math
import secrets
from datetime import datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import CheckConstraint, DateTime, Integer, String, delete, select, text
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from .auth import EmailLoginInput, User, UserSession, check_origin, complete_profile, create_session, hash_password, require_user_mutation, session_response
from .common import ApiError, new_id, utcnow
from .config import settings
from .crypto import lookup_digest, open_json, seal_json
from .db import Base, get_db
from .feedback import client_ip
from .integration import Outbox
from .orders import CustomerInput

router = APIRouter(prefix='/api/customer')
FLOW_LIFETIME = timedelta(minutes=30)
CODE_LIFETIME = timedelta(minutes=10)


class UserChallenge(Base):
    __tablename__ = 'user_challenges'
    __table_args__ = (CheckConstraint("purpose IN ('signup','password_reset','profile_completion')", name='user_challenges_purpose_check'),
                      CheckConstraint('attempts >= 0 AND generation >= 1', name='user_challenges_counters_check'))
    flow_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(30))
    continuation_hash: Mapped[str] = mapped_column(String(64))
    email_lookup: Mapped[str] = mapped_column(String(64), index=True)
    code_hmac: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    encrypted_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def payload(self):
        return open_json(self.encrypted_payload, table=self.__tablename__, record_id=self.flow_id, field='payload') if self.encrypted_payload is not None else None

    @payload.setter
    def payload(self, value):
        self.encrypted_payload = seal_json(value, table=self.__tablename__, record_id=self.flow_id, field='payload') if value is not None else None


class ChallengeThrottle(Base):
    __tablename__ = 'challenge_throttles'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EmailInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    email: Annotated[str, Field(min_length=1, max_length=254)]
    _email = field_validator('email')(EmailLoginInput.normalize_email.__func__)


class ProfileInput(EmailInput):
    phone: Annotated[str, Field(min_length=10, max_length=30)]
    _phone = field_validator('phone')(CustomerInput.normalize_phone.__func__)


class SignupInput(ProfileInput):
    password: Annotated[str, Field(min_length=12, max_length=256)]

    @field_validator('password')
    @classmethod
    def password_text(cls, value):
        if '\x00' in value:
            raise ValueError('Недопустимый символ в пароле.')
        return value


class FlowInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    flowId: Annotated[str, Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')]


class ConfirmInput(FlowInput):
    code: Annotated[str, Field(pattern=r'^[0-9]{6}$')]


class ResetInput(FlowInput):
    token: Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{43}$')]
    newPassword: Annotated[str, Field(min_length=12, max_length=256)]
    confirmPassword: Annotated[str, Field(min_length=12, max_length=256)]
    _password = field_validator('newPassword', 'confirmPassword')(SignupInput.password_text.__func__)

    @model_validator(mode='after')
    def matching_passwords(self):
        if self.newPassword != self.confirmPassword:
            raise ValueError('Пароли не совпадают.')
        return self


def cookie_name(flow_id):
    return 'lavr_flow_' + flow_id.replace('-', '')


def code_digest(flow, code):
    value = f'{flow.purpose}\0{flow.flow_id}\0{flow.generation}\0{code}'
    return hmac.new(settings.challenge_secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def _rate_error(db, seconds=3600):
    db.commit()
    raise ApiError(429, 'CHALLENGE_RATE_LIMIT', 'Слишком много попыток. Повторите позже.', headers={'Retry-After': str(max(1, seconds))})


def _limits(db, request, email_lookup, action, now, *, increment=True):
    scopes = [('ip', client_ip(request), 20 if action == 'mail' else 60)]
    if email_lookup:
        scopes.append(('email', email_lookup, 5 if action == 'mail' else 20))
    values = sorted((lookup_digest('challenge_throttle', f'{action}:{kind}:{value}'), limit) for kind, value, limit in scopes)
    rows = []
    for key, limit in values:
        db.execute(insert(ChallengeThrottle).values(key=key, count=0, window_start=now).on_conflict_do_nothing())
        row = db.scalar(select(ChallengeThrottle).where(ChallengeThrottle.key == key).with_for_update())
        if row.window_start <= now - timedelta(hours=1):
            row.count, row.window_start = 0, now
        if row.count >= limit:
            _rate_error(db, math.ceil((row.window_start + timedelta(hours=1) - now).total_seconds()))
        rows.append(row)
    if increment:
        for row in rows:
            row.count += 1
    return rows


def _require_mail(*, signup=False):
    from .email_worker import smtp_configured
    if (signup and not settings.customer_registration_enabled) or not smtp_configured():
        raise ApiError(503, 'EMAIL_NOT_CONFIGURED', 'Подтверждение по email временно недоступно.')


def _reset_origin():
    from .telegram_api import site_origin

    origin = site_origin()
    if not origin or not origin.startswith('https://') or '\\' in origin:
        raise ApiError(503, 'EMAIL_NOT_CONFIGURED', 'Восстановление пароля по email временно недоступно.')
    return origin


def _queue(db, flow, now):
    origin = _reset_origin() if flow.purpose == 'password_reset' else None
    flow.expires_at = min(now + CODE_LIFETIME, flow.created_at + FLOW_LIFETIME)
    flow.last_sent_at = now
    if flow.encrypted_payload is None:
        return
    code = secrets.token_urlsafe(32) if origin else f'{secrets.randbelow(1000000):06d}'
    flow.code_hmac = code_digest(flow, code)
    subject, message = 'Код подтверждения MIRZAP67', f'Ваш код подтверждения MIRZAP67: {code}. Код действует 10 минут.'
    if origin:
        link = origin + '/#password-reset?' + urlencode({'flow': flow.flow_id, 'token': code})
        subject = 'Восстановление пароля MIRZAP67'
        message = f'Для восстановления пароля MIRZAP67 откройте ссылку:\n{link}\nСсылка действует 10 минут. Если вы не запрашивали восстановление, проигнорируйте письмо.'
    db.add(Outbox(id=new_id(), event_type='customer_verification_email', state='queued', attempts=0,
                  payload={'flowId': flow.flow_id, 'generation': flow.generation, 'to': flow.payload['email'], 'code': code,
                           'subject': subject, 'text': message}))


def _begin(db, request, response, purpose, email, payload, *, limits_reserved=False):
    now = utcnow()
    digest = lookup_digest('email', email)
    if not limits_reserved:
        _limits(db, request, digest, 'mail', now)
    continuation = secrets.token_urlsafe(32)
    flow = UserChallenge(flow_id=new_id(), purpose=purpose, continuation_hash=hashlib.sha256(continuation.encode()).hexdigest(),
                         email_lookup=digest, generation=1, created_at=now, expires_at=now + CODE_LIFETIME,
                         last_sent_at=now, attempts=0)
    flow.payload = payload
    db.add(flow)
    if payload is not None:
        _queue(db, flow, now)
    db.commit()
    response.set_cookie(cookie_name(flow.flow_id), continuation, max_age=1800, httponly=True,
                        secure=settings.cookie_secure, samesite='strict', path='/api/customer')
    return {'flowId': flow.flow_id, 'resendAfterSeconds': 60}


def _valid(flow, request, now):
    token = request.cookies.get(cookie_name(flow.flow_id), '') if flow else ''
    return bool(flow and 32 <= len(token) <= 256 and hmac.compare_digest(flow.continuation_hash, hashlib.sha256(token.encode()).hexdigest())
                and flow.consumed_at is None and flow.created_at + FLOW_LIFETIME > now)


def _invalid(db, *, reset=False):
    db.commit()
    message = 'Ссылка недействительна или срок действия истёк. Запросите новую ссылку.' if reset else 'Код недействителен. Начните подтверждение заново.'
    raise ApiError(400, 'INVALID_CHALLENGE', message)


def _locked_flow(db, request, flow_id, action, now, *, increment=True):
    preview = db.get(UserChallenge, flow_id)
    _limits(db, request, preview.email_lookup if preview else None, action, now, increment=increment)
    return db.scalar(select(UserChallenge).where(UserChallenge.flow_id == flow_id).with_for_update().execution_options(populate_existing=True))


def _verify(db, request, body, purpose, now):
    flow = _locked_flow(db, request, body.flowId, 'check', now)
    valid = _valid(flow, request, now) if purpose != 'password_reset' else bool(
        flow and flow.consumed_at is None and flow.created_at + FLOW_LIFETIME > now)
    if not valid or flow.purpose != purpose or flow.expires_at <= now:
        _invalid(db, reset=purpose == 'password_reset')
    if flow.attempts >= 5:
        _rate_error(db, math.ceil((flow.created_at + FLOW_LIFETIME - now).total_seconds()))
    proof = body.token if purpose == 'password_reset' else body.code
    if flow.encrypted_payload is None or not flow.code_hmac or not hmac.compare_digest(flow.code_hmac, code_digest(flow, proof)):
        flow.attempts += 1
        _invalid(db, reset=purpose == 'password_reset')
    return flow


def _email_lock(db, digest):
    key = int.from_bytes(bytes.fromhex(digest)[:8], 'big', signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})


def _consume(flow, response, now):
    flow.consumed_at, flow.code_hmac, flow.payload = now, None, None
    response.delete_cookie(cookie_name(flow.flow_id), httponly=True, secure=settings.cookie_secure, samesite='strict', path='/api/customer')


@router.post('/auth/signup', status_code=202)
def signup(body: SignupInput, request: Request, response: Response, db: Session = Depends(get_db)):
    check_origin(request)
    _require_mail(signup=True)
    _limits(db, request, lookup_digest('email', body.email), 'mail', utcnow())
    payload = {'email': body.email, 'phone': body.phone, 'password_hash': hash_password(body.password)}
    if db.scalar(select(User.id).where(User.email_lookup == lookup_digest('email', body.email))) is not None:
        payload = None
    return _begin(db, request, response, 'signup', body.email, payload, limits_reserved=True)


@router.post('/auth/activate')
def activate(body: ConfirmInput, request: Request, response: Response, db: Session = Depends(get_db)):
    check_origin(request)
    now = utcnow()
    flow = _verify(db, request, body, 'signup', now)
    payload = flow.payload
    _email_lock(db, flow.email_lookup)
    if db.scalar(select(User.id).where(User.email_lookup == flow.email_lookup)) is not None:
        _consume(flow, response, now)
        _invalid(db)
    user = User(id=new_id(), role='partner', password_hash=payload['password_hash'], is_active=True, email_verified_at=now)
    user.profile = {'email': payload['email'], 'phone': payload['phone']}
    db.add(user)
    _consume(flow, response, now)
    session = create_session(db, user, request, response)
    db.commit()
    return session_response(user, session)


@router.post('/auth/resend', status_code=202)
def resend(body: FlowInput, request: Request, db: Session = Depends(get_db)):
    check_origin(request)
    _require_mail()
    now = utcnow()
    preview = db.get(UserChallenge, body.flowId)
    rows = _limits(db, request, preview.email_lookup if preview else None, 'mail', now, increment=False)
    flow = db.scalar(select(UserChallenge).where(UserChallenge.flow_id == body.flowId).with_for_update().execution_options(populate_existing=True))
    if _valid(flow, request, now):
        if flow.attempts >= 5:
            _rate_error(db, math.ceil((flow.created_at + FLOW_LIFETIME - now).total_seconds()))
        wait = math.ceil((flow.last_sent_at + timedelta(seconds=60) - now).total_seconds())
        if wait > 0:
            _rate_error(db, wait)
        for row in rows:
            row.count += 1
        flow.generation += 1
        _queue(db, flow, now)
    db.commit()
    return {'flowId': body.flowId, 'resendAfterSeconds': 60}


@router.post('/auth/password-reset', status_code=202)
def password_reset(body: EmailInput, request: Request, response: Response, db: Session = Depends(get_db)):
    check_origin(request)
    _require_mail()
    _reset_origin()
    user = db.scalar(select(User).where(User.email_lookup == lookup_digest('email', body.email), User.email_verified_at.is_not(None), User.is_active.is_(True)))
    payload = {'email': body.email, 'user_id': user.id, 'auth_version': user.auth_version} if user else None
    return _begin(db, request, response, 'password_reset', body.email, payload)


@router.post('/auth/password-reset/confirm', status_code=204)
def confirm_password_reset(body: ResetInput, request: Request, response: Response, db: Session = Depends(get_db)):
    check_origin(request)
    now = utcnow()
    flow = _verify(db, request, body, 'password_reset', now)
    payload = flow.payload
    user = db.scalar(select(User).where(User.id == payload['user_id']).with_for_update().execution_options(populate_existing=True))
    if not user or not user.is_active or user.auth_version != payload['auth_version'] or user.email_lookup != flow.email_lookup or user.email_verified_at is None:
        _invalid(db, reset=True)
    user.password_hash = hash_password(body.newPassword)
    user.auth_version += 1
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    _consume(flow, response, now)
    db.commit()
    response.delete_cookie('lavr_session', path='/api', httponly=True, secure=settings.cookie_secure, samesite='strict')


@router.post('/profile/activation', status_code=202)
def profile_activation(body: ProfileInput, request: Request, response: Response, db: Session = Depends(get_db), user: User = Depends(require_user_mutation)):
    _require_mail()
    if complete_profile(user):
        raise ApiError(409, 'PROFILE_COMPLETE', 'Профиль уже подтверждён.')
    taken = db.scalar(select(User.id).where(User.email_lookup == lookup_digest('email', body.email), User.id != user.id))
    payload = {'email': body.email, 'phone': body.phone, 'user_id': user.id, 'auth_version': user.auth_version} if taken is None else None
    return _begin(db, request, response, 'profile_completion', body.email, payload)


@router.post('/profile/activation/confirm')
def confirm_profile_activation(body: ConfirmInput, request: Request, response: Response, db: Session = Depends(get_db), user: User = Depends(require_user_mutation)):
    now = utcnow()
    flow = _verify(db, request, body, 'profile_completion', now)
    payload = flow.payload
    if payload['user_id'] != user.id:
        _invalid(db)
    _email_lock(db, flow.email_lookup)
    db.refresh(user, with_for_update=True)
    if not user.is_active or user.auth_version != payload['auth_version'] or complete_profile(user):
        _invalid(db)
    if db.scalar(select(User.id).where(User.email_lookup == flow.email_lookup, User.id != user.id)) is not None:
        _invalid(db)
    user.profile = {'email': payload['email'], 'phone': payload['phone']}
    user.email_verified_at = now
    _consume(flow, response, now)
    session = create_session(db, user, request, response)
    db.commit()
    return session_response(user, session)
