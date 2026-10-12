import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Response
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from .auth import record_audit, require_admin, require_admin_mutation
from .common import ApiError, new_id, utcnow
from .db import Base, get_db


class IntegrationState(Base):
    __tablename__ = 'integration_state'
    __table_args__ = (CheckConstraint('id = 1'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    catalog_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_order_export_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[str] = mapped_column(String(30), default='not_connected')
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class Outbox(Base):
    __tablename__ = 'outbox'
    __table_args__ = (
        CheckConstraint("state IN ('queued', 'running', 'done', 'error', 'uncertain', 'rejected', 'cancelled')"),
        CheckConstraint('attempts >= 0'),
        UniqueConstraint('order_id', 'event_type'),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    order_id: Mapped[str | None] = mapped_column(ForeignKey('orders.id', ondelete='CASCADE'), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(30), default='queued', index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IntegrationCommand(Base):
    __tablename__ = 'integration_commands'

    scope: Mapped[str] = mapped_column(String(512), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('outbox.id', ondelete='CASCADE'))
    response_state: Mapped[str] = mapped_column(String(20), default='queued')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DisabledAdapter:
    # shortcut: конфигурация 1С неизвестна, подключить адаптер после согласования контракта обмена.
    is_configured = False


_adapter = DisabledAdapter()


def get_erp_adapter():
    return _adapter


def require_erp():
    if not get_erp_adapter().is_configured:
        raise ApiError(503, 'ERP_NOT_CONFIGURED', 'Обмен с 1С ещё не настроен. Приём заказов недоступен.')


router = APIRouter(prefix='/api')


@router.get('/manager/integration/health')
def health(response: Response, db: Session = Depends(get_db), staff=Depends(require_admin)):
    from .catalog import catalog_metadata
    from .orders import Order

    metadata = catalog_metadata(db)
    state = db.get(IntegrationState, 1)
    pending = db.scalar(select(func.count()).select_from(Order).where(Order.integration_status.in_(['pending_sync', 'exporting'])))
    errors = db.scalar(select(func.count()).select_from(Order).where(Order.integration_status == 'error'))
    connected = get_erp_adapter().is_configured
    code = state.error_code if state else None
    response.headers['Cache-Control'] = 'no-store'
    return {
        'state': state.state if connected and state and state.state in ('healthy', 'degraded', 'offline') else ('offline' if connected else 'not_connected'),
        'catalogUpdatedAt': metadata['catalogUpdatedAt'],
        'lastOrderExportAt': state.last_order_export_at.isoformat() if state and state.last_order_export_at else None,
        'catalogStale': metadata['stale'],
        'pendingCount': pending,
        'errorCount': errors,
        'lastErrorCode': code if code and re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', code) else None,
    }


def command_scope(staff, path):
    return f'POST:{path}:{staff.id}'


def command_replay(db, scope, key):
    from .orders import lock_attempt

    lock_attempt(db, 'command:' + scope + ':' + key)
    previous = db.get(IntegrationCommand, (scope, key))
    if previous:
        return {'jobId': previous.job_id, 'state': previous.response_state}
    return None


@router.post('/manager/integration/sync', status_code=202)
def sync(response: Response, idempotency_key: str | None = Header(default=None), db: Session = Depends(get_db), staff=Depends(require_admin_mutation)):
    from .orders import advisory_key, parse_idempotency_key

    key = parse_idempotency_key(idempotency_key)
    response.headers['Cache-Control'] = 'no-store'
    scope = command_scope(staff, '/manager/integration/sync')
    replay = command_replay(db, scope, key)
    if replay:
        return replay
    require_erp()
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': advisory_key('catalog_sync')})
    job = db.scalar(select(Outbox).where(Outbox.event_type == 'catalog_sync', Outbox.state.in_(['queued', 'running'])).order_by(Outbox.created_at).with_for_update())
    if job is None:
        job = Outbox(id=new_id(), event_type='catalog_sync', payload={}, state='queued', attempts=0)
        db.add(job)
        db.flush()
    state = 'running' if job.state == 'running' else 'queued'
    db.add(IntegrationCommand(scope=scope, idempotency_key=key, job_id=job.id, response_state=state))
    record_audit(db, staff, 'integration.catalog_sync', job.id)
    db.commit()
    response.headers['Cache-Control'] = 'no-store'
    return {'jobId': job.id, 'state': state}


@router.post('/manager/integration/orders/{order_id}/retry', status_code=202)
def retry(order_id: Annotated[str, Path(min_length=1, max_length=128, pattern=r'^[^\x00-\x1f\x7f]+$')], response: Response, idempotency_key: str | None = Header(default=None), db: Session = Depends(get_db), staff=Depends(require_admin_mutation)):
    from .orders import Order, parse_idempotency_key

    key = parse_idempotency_key(idempotency_key)
    response.headers['Cache-Control'] = 'no-store'
    scope = command_scope(staff, f'/manager/integration/orders/{order_id}/retry')
    replay = command_replay(db, scope, key)
    if replay:
        return replay
    require_erp()
    order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise ApiError(404, 'NOT_FOUND', 'Заказ не найден.')
    if order.status != 'new' or order.integration_status != 'error' or order.external_document_id:
        raise ApiError(409, 'RETRY_NOT_ALLOWED', 'Этот заказ нельзя отправить повторно.')
    job = db.scalar(select(Outbox).where(Outbox.order_id == order.id, Outbox.event_type == 'order_export').with_for_update())
    if job is None or job.state not in ('error', 'uncertain', 'rejected') or (job.lease_until and job.lease_until > utcnow()):
        raise ApiError(409, 'RETRY_NOT_ALLOWED', 'Отправка ещё выполняется или требует сверки.')
    job.state = 'queued'
    job.lease_until = None
    order.integration_status = 'pending_sync'
    order.version += 1
    db.add(IntegrationCommand(scope=scope, idempotency_key=key, job_id=job.id, response_state='queued'))
    record_audit(db, staff, 'integration.order_retry', order.id, order.version)
    db.commit()
    response.headers['Cache-Control'] = 'no-store'
    return {'jobId': job.id, 'state': 'queued'}
