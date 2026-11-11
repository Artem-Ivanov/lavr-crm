from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .accounts import _email_lock
from .auth import EmailLoginInput, User, hash_password, record_audit, require_admin, require_admin_mutation
from .common import ApiError, new_id, page_response, utcnow, version_header
from .crypto import lookup_digest
from .db import get_db
from .orders import CustomerInput

router = APIRouter(prefix='/api/admin/users')
USER_PERMISSIONS_LOCK = 0x4D49525A55534552
UserId = Annotated[str, Path(min_length=1, max_length=128, pattern=r'^[^\x00-\x1f\x7f]+$')]


class PermissionsInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    role: Literal['partner', 'operator', 'admin']


class StatusInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    isActive: bool


class CreateUserInput(EmailLoginInput):
    password: Annotated[str, Field(min_length=12, max_length=256)]
    role: Literal['partner', 'operator', 'admin'] = 'partner'
    name: str = Field(default='', max_length=240, pattern=r'^[^\x00-\x1f\x7f]*$')
    phone: str = Field(default='', max_length=30)

    @field_validator('name')
    @classmethod
    def strip_name(cls, value):
        return value.strip()

    @field_validator('phone')
    @classmethod
    def normalize_phone(cls, value):
        return CustomerInput.normalize_phone(value) if value else ''


def user_response(user):
    profile = user.profile
    return {'id': user.id, 'name': user.name, 'login': profile.get('email'), 'email': profile.get('email'),
            'phone': profile.get('phone'), 'emailVerified': user.email_verified_at is not None,
            'phoneVerified': False, 'isActive': user.is_active, 'role': user.role, 'version': user.auth_version}


def _user(db, user_id, *, lock=False):
    query = select(User).where(User.id == user_id)
    user = db.scalar(query.with_for_update() if lock else query)
    if user is None:
        raise ApiError(404, 'USER_NOT_FOUND', 'Пользователь не найден.')
    return user


@router.get('', operation_id='listUsers')
def list_users(q: Annotated[str, Query(max_length=254, pattern=r'^[^\x00-\x1f\x7f]*$')] = '',
               offset: Annotated[int, Query(ge=0, le=9007199254740991)] = 0,
               limit: Annotated[int, Query(ge=1, le=100)] = 24,
               db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    query = select(User)
    if q.strip():
        value = q.strip()
        phone = ''.join(char for char in value if char not in '+ ()-')
        phone = '+7' + phone[1:] if len(phone) == 11 and phone[0] in '78' and phone.isascii() and phone.isdigit() else value
        query = query.where(or_(User.email_lookup == lookup_digest('email', value.lower()),
                                User.login_lookup == lookup_digest('legacy_login', value),
                                User.phone_lookup == lookup_digest('phone', phone)))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.order_by(User.created_at, User.id).offset(offset).limit(limit)).all()
    return page_response([user_response(user) for user in items], total, offset, limit)


@router.get('/{user_id}', operation_id='getUser')
def get_user(user_id: UserId, response: Response, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = _user(db, user_id)
    response.headers['ETag'] = f'"{user.auth_version}"'
    return user_response(user)


def _lock_admin(db, admin, request):
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': USER_PERMISSIONS_LOCK})
    db.refresh(admin)
    if not admin.is_active or admin.role != 'admin' or admin.auth_version != request.state.user_session.auth_version:
        raise ApiError(401, 'UNAUTHORIZED', 'Сессия завершена. Войдите снова.')


@router.post('', status_code=201, operation_id='createUser')
def create_user(body: CreateUserInput, request: Request, response: Response,
                db: Session = Depends(get_db), admin: User = Depends(require_admin_mutation)):
    _lock_admin(db, admin, request)
    digest = lookup_digest('email', body.email)
    _email_lock(db, digest)
    if db.scalar(select(User.id).where(User.email_lookup == digest)) is not None:
        raise ApiError(409, 'EMAIL_ALREADY_EXISTS', 'Пользователь с таким email уже существует.')
    user = User(id=new_id(), name=body.name, role=body.role, password_hash=hash_password(body.password),
                is_active=True, email_verified_at=utcnow())
    user.profile = {'email': body.email, **({'phone': body.phone} if body.phone else {})}
    db.add(user)
    record_audit(db, admin, 'user.created', user.id, 1, {'role': body.role, 'isActive': True})
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if getattr(getattr(error.orig, 'diag', None), 'constraint_name', None) == 'users_email_lookup_key':
            raise ApiError(409, 'EMAIL_ALREADY_EXISTS', 'Пользователь с таким email уже существует.') from None
        raise
    response.headers['ETag'] = f'"{user.auth_version}"'
    return user_response(user)


def _change_user(db, admin, target_id, request, if_match, *, role=None, active=None):
    version = version_header(if_match)
    _lock_admin(db, admin, request)
    user = _user(db, target_id, lock=True)
    if user.auth_version != version:
        raise ApiError(412, 'VERSION_CONFLICT', 'Запись уже изменена. Загрузите актуальную версию.')
    next_role = user.role if role is None else role
    next_active = user.is_active if active is None else active
    if user.role == 'admin' and user.is_active and (next_role != 'admin' or not next_active):
        admins = db.scalar(select(func.count()).select_from(User).where(User.role == 'admin', User.is_active.is_(True)))
        if admins <= 1:
            raise ApiError(409, 'LAST_ACTIVE_ADMIN', 'Нельзя отключить последнего администратора.')
    if user.role != next_role or user.is_active != next_active:
        before = {'role': user.role, 'isActive': user.is_active}
        user.role, user.is_active = next_role, next_active
        user.auth_version += 1
        record_audit(db, admin, 'user.permissions_changed' if role is not None else 'user.status_changed', user.id,
                     user.auth_version, {'before': before, 'after': {'role': user.role, 'isActive': user.is_active}})
        db.commit()
    return user


@router.patch('/{user_id}/permissions', operation_id='updateUserRole')
def update_role(user_id: UserId, body: PermissionsInput, request: Request, response: Response,
                if_match: str | None = Header(default=None), db: Session = Depends(get_db), admin: User = Depends(require_admin_mutation)):
    user = _change_user(db, admin, user_id, request, if_match, role=body.role)
    response.headers['ETag'] = f'"{user.auth_version}"'
    return user_response(user)


@router.patch('/{user_id}/status', operation_id='updateUserStatus')
def update_status(user_id: UserId, body: StatusInput, request: Request, response: Response,
                  if_match: str | None = Header(default=None), db: Session = Depends(get_db), admin: User = Depends(require_admin_mutation)):
    user = _change_user(db, admin, user_id, request, if_match, active=body.isActive)
    response.headers['ETag'] = f'"{user.auth_version}"'
    return user_response(user)
