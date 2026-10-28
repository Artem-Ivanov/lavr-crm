import hashlib
import hmac
import ipaddress
import json
import math
import re
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from pydantic import Field, field_validator, model_validator
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import integration, orders
from .auth import AuditEntry, Staff, check_origin, get_current_staff, record_audit, require_mutation
from .common import ApiError, check_version, new_id, page_response, utcnow
from .config import settings
from .db import Base, get_db


FeedbackStatus = Literal['new', 'in_progress', 'converted', 'closed']
CaseId = Annotated[str, Path(min_length=1, max_length=128, pattern=r'^[^\x00-\x1f\x7f]+$')]
ContactMethod = Literal['', 'phone', 'email', 'messenger']


class Feedback(Base):
    __tablename__ = 'feedback'
    __table_args__ = (
        CheckConstraint("type IN ('contact', 'request')"),
        CheckConstraint("status IN ('new', 'in_progress', 'converted', 'closed')"),
        CheckConstraint("(status = 'converted') = (order_id IS NOT NULL)"),
        CheckConstraint("status != 'in_progress' OR assigned_staff_id IS NOT NULL"),
        CheckConstraint("status != 'closed' OR length(trim(resolution)) > 0"),
        CheckConstraint(f'version BETWEEN 1 AND {orders.MAX_SAFE_INTEGER}'),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    number: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    type: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100), default='')
    phone: Mapped[str] = mapped_column(String(30), default='')
    email: Mapped[str] = mapped_column(String(254), default='')
    messenger: Mapped[dict] = mapped_column(JSONB)
    message: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(20), default='new', index=True)
    assigned_staff_id: Mapped[str | None] = mapped_column(ForeignKey('staff.id'), nullable=True, index=True)
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    resolution: Mapped[str] = mapped_column(Text, default='')
    order_id: Mapped[str | None] = mapped_column(ForeignKey('orders.id', ondelete='RESTRICT'), nullable=True, unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(36), unique=True)
    digest: Mapped[str] = mapped_column(String(64))
    receipt: Mapped[dict] = mapped_column(JSONB)
    conversion_key: Mapped[str | None] = mapped_column(String(36), nullable=True)
    conversion_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    conversion_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class FeedbackNote(Base):
    __tablename__ = 'feedback_notes'

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    feedback_id: Mapped[str] = mapped_column(ForeignKey('feedback.id', ondelete='CASCADE'), index=True)
    staff_id: Mapped[str] = mapped_column(ForeignKey('staff.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    text: Mapped[str] = mapped_column(Text)
    contact_method: Mapped[str] = mapped_column(String(20), default='')


class FeedbackThrottle(Base):
    __tablename__ = 'feedback_throttles'
    __table_args__ = (CheckConstraint('count >= 0'),)

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def clean_text(value, multiline=False):
    if any(ord(char) < 32 and not (multiline and char in '\r\n') or 127 <= ord(char) <= 159 for char in value):
        raise ValueError('Текст содержит недопустимый символ.')
    return value.strip()


class MessengerInput(orders.InputModel):
    service: str = Field(default='', max_length=40)
    address: str = Field(default='', max_length=254)

    @field_validator('service', 'address')
    @classmethod
    def clean(cls, value):
        return clean_text(value)

    @model_validator(mode='after')
    def complete_pair(self):
        if bool(self.service) != bool(self.address):
            raise ValueError('Укажите сервис и контакт в мессенджере.')
        return self


class CreateFeedbackRequest(orders.InputModel):
    type: Literal['contact', 'request']
    name: str = Field(default='', max_length=100)
    phone: str = Field(default='', max_length=30)
    email: str = Field(default='', max_length=254)
    messenger: MessengerInput = Field(default_factory=MessengerInput)
    message: str = Field(default='', max_length=2000)

    @field_validator('name', 'phone', 'email')
    @classmethod
    def single_line(cls, value):
        return clean_text(value)

    @field_validator('phone')
    @classmethod
    def phone_number(cls, value):
        return orders.CustomerInput.normalize_phone(value) if value else ''

    @field_validator('email')
    @classmethod
    def email_address(cls, value):
        if value and not re.fullmatch(r'[^\s@?#]+@[^\s@?#]+\.[^\s@?#]+', value):
            raise ValueError('Проверьте адрес электронной почты.')
        return value

    @field_validator('message')
    @classmethod
    def message_text(cls, value):
        return clean_text(value, multiline=True)

    @model_validator(mode='after')
    def required_contacts_and_message(self):
        if not (self.phone or self.email or self.messenger.address):
            raise ValueError('Укажите хотя бы один способ связи.')
        if self.type == 'request' and not self.message:
            raise ValueError('Укажите текст запроса.')
        return self


class StatusInput(orders.InputModel):
    status: Literal['in_progress', 'closed']
    resolution: str = Field(default='', max_length=2000)

    @field_validator('resolution')
    @classmethod
    def result_text(cls, value):
        return clean_text(value, multiline=True)

    @model_validator(mode='after')
    def closing_result(self):
        if self.status == 'closed' and not self.resolution:
            raise ValueError('Укажите результат обращения.')
        if self.status == 'in_progress' and self.resolution:
            raise ValueError('Результат указывается при закрытии обращения.')
        return self


class NoteInput(orders.InputModel):
    text: str = Field(min_length=1, max_length=2000)
    contactMethod: ContactMethod = ''

    @field_validator('text')
    @classmethod
    def note_text(cls, value):
        value = clean_text(value, multiline=True)
        if not value:
            raise ValueError('Укажите текст заметки.')
        return value


def payload_digest(body):
    return hashlib.sha256(json.dumps(body.model_dump(), ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def client_ip(request):
    host = request.client.host if request.client else ''
    trusted = False
    for proxy in settings.trusted_proxy_hosts:
        try:
            trusted = ipaddress.ip_address(host) in ipaddress.ip_network(proxy, strict=False)
        except ValueError:
            trusted = host == proxy
        if trusted:
            break
    if trusted:
        try:
            return str(ipaddress.ip_address(request.headers.get('x-real-ip', '')))
        except ValueError:
            pass
    return host


def throttle_feedback(db, request, body):
    values = [('ip', client_ip(request), 10)]
    if body.phone:
        values.append(('phone', body.phone, 3))
    if body.email:
        values.append(('email', body.email.casefold(), 3))
    if body.messenger.address:
        values.append(('messenger', body.messenger.service.casefold() + '\n' + body.messenger.address.casefold(), 3))
    keys = sorted((hmac.new(settings.session_secret.encode(), f'feedback:{kind}:{value}'.encode(), hashlib.sha256).hexdigest(), limit) for kind, value, limit in values)
    now = utcnow()
    for key, limit in keys:
        db.execute(insert(FeedbackThrottle).values(key=key, count=0, window_start=now).on_conflict_do_nothing())
        counter = db.scalar(select(FeedbackThrottle).where(FeedbackThrottle.key == key).with_for_update())
        if counter.window_start <= now - timedelta(minutes=10):
            counter.count, counter.window_start = 0, now
        if counter.count >= limit:
            retry = max(1, math.ceil((counter.window_start + timedelta(minutes=10) - now).total_seconds()))
            raise ApiError(429, 'FEEDBACK_RATE_LIMIT', 'Слишком много обращений. Повторите позже.', headers={'Retry-After': str(retry)})
        counter.count += 1


def staff_view(staff):
    return {'id': staff.id, 'name': staff.name} if staff else None


def manager_feedback(db, case):
    owner = db.get(Staff, case.assigned_staff_id) if case.assigned_staff_id else None
    history = [{'id': case.id, 'createdAt': case.created_at.isoformat(), 'staff': None, 'action': 'feedback.created', 'text': '', 'contactMethod': ''}]
    for entry, staff in db.execute(select(AuditEntry, Staff).join(Staff, Staff.id == AuditEntry.staff_id).where(AuditEntry.entity_id == case.id, AuditEntry.action.in_(['feedback.claimed', 'feedback.closed', 'feedback.converted']))):
        history.append({'id': entry.id, 'createdAt': entry.created_at.isoformat(), 'staff': staff_view(staff), 'action': entry.action, 'text': case.resolution if entry.action == 'feedback.closed' else '', 'contactMethod': ''})
    for note, staff in db.execute(select(FeedbackNote, Staff).join(Staff, Staff.id == FeedbackNote.staff_id).where(FeedbackNote.feedback_id == case.id)):
        history.append({'id': note.id, 'createdAt': note.created_at.isoformat(), 'staff': staff_view(staff), 'action': 'feedback.note_added', 'text': note.text, 'contactMethod': note.contact_method})
    history.sort(key=lambda entry: (entry['createdAt'], entry['id']))
    event = db.get(integration.Outbox, case.id)
    state = event.state if event and event.state in ('queued', 'running', 'done', 'error', 'uncertain') else 'queued'
    if state != 'done' and not (settings.telegram_bot_token and settings.telegram_chat_id and settings.public_site_url):
        state = 'not_configured'
    code = event.last_error_code if event else None
    if not isinstance(code, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', code):
        code = None
    linked = db.get(orders.Order, case.order_id) if case.order_id else None
    return {'id': case.id, 'number': case.number, 'createdAt': case.created_at.isoformat(), 'updatedAt': case.updated_at.isoformat(), 'type': case.type, 'name': case.name, 'phone': case.phone, 'email': case.email, 'messenger': case.messenger, 'message': case.message, 'status': case.status, 'assignedTo': staff_view(owner), 'version': case.version, 'resolution': case.resolution, 'order': orders.order_receipt(linked) if linked else None, 'history': history, 'notification': {'state': state, 'code': code}}


def locked_feedback(db, case_id):
    case = db.scalar(select(Feedback).where(Feedback.id == case_id).with_for_update())
    if case is None:
        raise ApiError(404, 'NOT_FOUND', 'Обращение не найдено.')
    return case


def require_owner(case, staff):
    if case.assigned_staff_id and case.assigned_staff_id != staff.id and staff.role != 'admin':
        raise ApiError(403, 'FORBIDDEN', 'Обращение ведёт другой сотрудник.')


def changed_response(db, case, response):
    db.flush()
    result = manager_feedback(db, case)
    db.commit()
    response.headers['ETag'] = f'"{case.version}"'
    return result


router = APIRouter(prefix='/api')


@router.post('/feedback', status_code=202)
def submit_feedback(body: CreateFeedbackRequest, request: Request, idempotency_key: str | None = Header(default=None), db: Session = Depends(get_db)):
    check_origin(request)
    key = orders.parse_idempotency_key(idempotency_key)
    digest = payload_digest(body)
    orders.lock_attempt(db, 'feedback:' + key)
    previous = db.scalar(select(Feedback).where(Feedback.idempotency_key == key))
    if previous:
        if previous.digest != digest:
            raise ApiError(409, 'IDEMPOTENCY_CONFLICT', 'С этим ключом уже принят другой запрос.')
        return previous.receipt
    throttle_feedback(db, request, body)
    now, case_id = utcnow(), new_id()
    receipt = {'id': case_id, 'number': f'F-{now:%Y%m%d}-{case_id.upper()}', 'createdAt': now.isoformat(), 'state': 'accepted'}
    case = Feedback(id=case_id, number=receipt['number'], created_at=now, updated_at=now, **body.model_dump(), status='new', version=1, idempotency_key=key, digest=digest, receipt=receipt)
    db.add(case)
    db.add(integration.Outbox(id=case_id, event_type='feedback_telegram', payload={'feedbackId': case_id}, state='queued', attempts=0))
    db.commit()
    return receipt


@router.get('/manager/feedback')
def list_feedback(status: FeedbackStatus | None = None, q: Annotated[str, Query(max_length=120, pattern=r'^[^\x00-\x1f\x7f]*$')] = '', offset: Annotated[int, Query(ge=0, le=orders.MAX_SAFE_INTEGER)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 24, db: Session = Depends(get_db), staff=Depends(get_current_staff)):
    query = select(Feedback)
    if status:
        query = query.where(Feedback.status == status)
    if q.strip():
        search = '%' + q.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        fields = [Feedback.number, Feedback.name, Feedback.phone, Feedback.email, Feedback.messenger['service'].astext, Feedback.messenger['address'].astext]
        query = query.where(or_(*(field.ilike(search, escape='\\') for field in fields)))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    cases = db.scalars(query.order_by((Feedback.status == 'new').desc(), Feedback.created_at.desc(), Feedback.id.desc()).offset(offset).limit(limit))
    return page_response([manager_feedback(db, case) for case in cases], total, offset, limit)


@router.get('/manager/feedback/{case_id}')
def get_feedback(case_id: CaseId, response: Response, db: Session = Depends(get_db), staff=Depends(get_current_staff)):
    case = db.get(Feedback, case_id)
    if case is None:
        raise ApiError(404, 'NOT_FOUND', 'Обращение не найдено.')
    response.headers['ETag'] = f'"{case.version}"'
    return manager_feedback(db, case)


@router.patch('/manager/feedback/{case_id}/status')
def update_status(case_id: CaseId, body: StatusInput, response: Response, if_match: str | None = Header(default=None), db: Session = Depends(get_db), staff=Depends(require_mutation)):
    case = locked_feedback(db, case_id)
    check_version(case, if_match)
    require_owner(case, staff)
    if case.status in ('closed', 'converted') or body.status == 'in_progress' and case.status != 'new':
        raise ApiError(409, 'INVALID_TRANSITION', 'Этот переход статуса недоступен.')
    if body.status == 'in_progress':
        case.assigned_staff_id = staff.id
    case.status, case.resolution = body.status, body.resolution
    case.version += 1
    case.updated_at = utcnow()
    record_audit(db, staff, 'feedback.claimed' if case.status == 'in_progress' else 'feedback.closed', case.id, case.version)
    return changed_response(db, case, response)


@router.post('/manager/feedback/{case_id}/notes')
def add_note(case_id: CaseId, body: NoteInput, response: Response, if_match: str | None = Header(default=None), db: Session = Depends(get_db), staff=Depends(require_mutation)):
    case = locked_feedback(db, case_id)
    require_owner(case, staff)
    check_version(case, if_match)
    if case.status != 'in_progress':
        raise ApiError(409, 'INVALID_TRANSITION', 'Заметку можно добавить к обращению в работе.')
    db.add(FeedbackNote(feedback_id=case.id, staff_id=staff.id, text=body.text, contact_method=body.contactMethod))
    case.version += 1
    case.updated_at = utcnow()
    return changed_response(db, case, response)


@router.post('/manager/feedback/{case_id}/order', status_code=201)
def create_feedback_order(case_id: CaseId, body: orders.CreateOrderRequest, response: Response, idempotency_key: str | None = Header(default=None), if_match: str | None = Header(default=None), db: Session = Depends(get_db), staff=Depends(require_mutation)):
    key = orders.parse_idempotency_key(idempotency_key)
    digest = payload_digest(body)
    orders.lock_attempt(db, 'feedback-order:' + case_id + ':' + key)
    case = locked_feedback(db, case_id)
    require_owner(case, staff)
    if case.conversion_key == key:
        if case.conversion_digest != digest:
            raise ApiError(409, 'IDEMPOTENCY_CONFLICT', 'С этим ключом уже принят другой запрос.')
        response.status_code = 200
        response.headers['ETag'] = f'"{case.conversion_response["version"]}"'
        return case.conversion_response
    if case.status == 'converted':
        raise ApiError(409, 'FEEDBACK_ALREADY_CONVERTED', 'Из обращения уже создан заказ.', [{'orderId': case.order_id}])
    check_version(case, if_match)
    if case.status != 'in_progress':
        raise ApiError(409, 'INVALID_TRANSITION', 'Заказ создаётся из обращения в работе.')
    order = orders.build_order(db, body)
    case.status, case.order_id = 'converted', order.id
    case.version += 1
    case.updated_at = utcnow()
    case.conversion_key, case.conversion_digest = key, digest
    record_audit(db, staff, 'feedback.converted', case.id, case.version)
    db.flush()
    case.conversion_response = manager_feedback(db, case)
    db.commit()
    response.headers['ETag'] = f'"{case.version}"'
    return case.conversion_response
