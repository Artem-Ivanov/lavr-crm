import hashlib
import json
import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, func, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from .auth import get_current_staff, record_audit, require_mutation
from .common import ApiError, check_version, new_id, page_response, utcnow
from .db import Base, get_db
from . import integration


MAX_SAFE_INTEGER = 9007199254740991
OrderStatus = Literal['new', 'confirmed', 'ready', 'completed', 'cancelled']
IntegrationStatus = Literal['not_connected', 'pending_sync', 'exporting', 'exported', 'error', 'cancelled_before_sync']


class Order(Base):
    __tablename__ = 'orders'
    __table_args__ = (
        CheckConstraint("status IN ('new', 'confirmed', 'ready', 'completed', 'cancelled')"),
        CheckConstraint("integration_status IN ('not_connected', 'pending_sync', 'exporting', 'exported', 'error', 'cancelled_before_sync')"),
        CheckConstraint(f'total_kopecks >= 0 AND total_kopecks <= {MAX_SAFE_INTEGER}'),
        CheckConstraint(f'version BETWEEN 1 AND {MAX_SAFE_INTEGER}'),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    number: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    customer: Mapped[dict] = mapped_column(JSONB)
    fulfillment: Mapped[dict] = mapped_column(JSONB)
    items: Mapped[list] = mapped_column(JSONB)
    total_kopecks: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(30), default='new', index=True)
    integration_status: Mapped[str] = mapped_column(String(30), default='pending_sync', index=True)
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    external_document_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class OrderAttempt(Base):
    __tablename__ = 'order_attempts'

    idempotency_key: Mapped[str] = mapped_column(String(36), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id', ondelete='CASCADE'), unique=True)
    receipt: Mapped[dict] = mapped_column(JSONB)


class InputModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    @model_validator(mode='before')
    @classmethod
    def reject_null_characters(cls, value):
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, str) and '\x00' in item:
                raise ValueError('Текст содержит недопустимый символ.')
            if isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        return value


class CustomerInput(InputModel):
    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=10, max_length=30)
    email: str = Field(default='', max_length=254)

    @field_validator('name')
    @classmethod
    def name_not_blank(cls, value):
        if not value.strip():
            raise ValueError('Укажите имя покупателя.')
        return value.strip()

    @field_validator('phone')
    @classmethod
    def normalize_phone(cls, value):
        digits = re.sub(r'[+\s()-]', '', value)
        if not re.fullmatch(r'[78][0-9]{10}', digits):
            raise ValueError('Укажите российский телефон: +7 и 10 цифр.')
        return '+7' + digits[1:]

    @field_validator('email')
    @classmethod
    def check_email(cls, value):
        value = value.strip()
        if value and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('Проверьте адрес электронной почты.')
        return value


class FulfillmentInput(InputModel):
    method: Literal['pickup', 'delivery']
    address: str = Field(default='', max_length=500)

    @model_validator(mode='after')
    def check_address(self):
        self.address = self.address.strip() if self.method == 'delivery' else ''
        if self.method == 'delivery' and not self.address:
            raise ValueError('Укажите адрес доставки.')
        return self


class OrderItemInput(InputModel):
    productId: str = Field(min_length=1, max_length=128, pattern=r'^[^\x00-\x1f\x7f]+$')
    quantity: int = Field(ge=1, le=999)
    expectedUnitPriceKopecks: int = Field(ge=0, le=MAX_SAFE_INTEGER)


class CreateOrderRequest(InputModel):
    customer: CustomerInput
    fulfillment: FulfillmentInput
    items: list[OrderItemInput] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def unique_products(self):
        if len({item.productId for item in self.items}) != len(self.items):
            raise ValueError('Товар не может повторяться в заказе.')
        self.items.sort(key=lambda item: item.productId)
        return self


class StatusInput(InputModel):
    status: Literal['ready', 'completed', 'cancelled']


def parse_idempotency_key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', value):
        raise ApiError(400, 'INVALID_IDEMPOTENCY_KEY', 'Нужен заголовок Idempotency-Key в формате UUID.')
    return str(UUID(value))


def advisory_key(value):
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], 'big', signed=True)


def lock_attempt(db, value):
    if not db.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': advisory_key(value)}):
        raise ApiError(409, 'IDEMPOTENCY_IN_PROGRESS', 'Этот запрос ещё выполняется. Повторите его с прежним ключом.', headers={'Retry-After': '1'})


def order_receipt(order):
    return {'id': order.id, 'number': order.number, 'createdAt': order.created_at.isoformat(), 'totalKopecks': order.total_kopecks, 'status': order.status, 'integrationStatus': order.integration_status}


def can_cancel(db, order, event=None):
    if order.status != 'new' or order.external_document_id:
        return False
    if event is None:
        event = db.scalar(select(integration.Outbox).where(integration.Outbox.order_id == order.id, integration.Outbox.event_type == 'order_export'))
    if event is None or event.lease_until is not None:
        return False
    return (
        order.integration_status == 'pending_sync' and event.state == 'queued' and event.attempts == 0
        or order.integration_status == 'error' and event.state == 'rejected'
    )


def manager_order(db, order):
    return {**order_receipt(order), 'customer': order.customer, 'fulfillment': order.fulfillment, 'items': order.items, 'version': order.version, 'canCancel': can_cancel(db, order), 'externalDocumentId': order.external_document_id}


router = APIRouter(prefix='/api')


def build_order(db: Session, body: CreateOrderRequest) -> Order:
    from .catalog import Category, Product, ProductPresentation, catalog_metadata

    integration.require_erp()
    if catalog_metadata(db)['stale']:
        raise ApiError(503, 'CATALOG_STALE', 'Каталог требует обновления из 1С. Приём заказов временно недоступен.')
    ids = [item.productId for item in body.items]
    products = {product.id: product for product in db.scalars(select(Product).where(Product.id.in_(ids)).order_by(Product.id).with_for_update())}
    presentations = {item.product_id: item for item in db.scalars(select(ProductPresentation).where(ProductPresentation.product_id.in_(ids)).order_by(ProductPresentation.product_id).with_for_update())}
    category_ids = sorted({item.category_id for item in presentations.values() if item.category_id})
    categories = {category.id: category for category in db.scalars(select(Category).where(Category.id.in_(category_ids)).order_by(Category.id).with_for_update())}
    snapshots = []
    total = 0
    for item in body.items:
        product = products.get(item.productId)
        presentation = presentations.get(item.productId)
        category = categories.get(presentation.category_id) if presentation else None
        if not product or not product.is_active or not category or not category.is_visible:
            raise ApiError(409, 'PRODUCT_UNAVAILABLE', 'Товар больше недоступен для заказа.', [{'productId': item.productId, 'code': 'PRODUCT_UNAVAILABLE'}])
        if product.price_kopecks != item.expectedUnitPriceKopecks:
            raise ApiError(409, 'PRICE_CHANGED', 'Цена изменилась. Подтвердите обновлённую корзину.', [{'productId': product.id, 'code': 'PRICE_CHANGED', 'currentPriceKopecks': product.price_kopecks}])
        available = max(0, product.stock - product.reserved)
        if item.quantity > available:
            raise ApiError(409, 'OUT_OF_STOCK', 'Недостаточно товара на складе.', [{'productId': product.id, 'code': 'OUT_OF_STOCK', 'availableStock': available}])
        total += product.price_kopecks * item.quantity
        if total > MAX_SAFE_INTEGER:
            raise ApiError(422, 'VALIDATION_ERROR', 'Сумма заказа превышает допустимый предел.')
        snapshots.append({'productId': product.id, 'sku': product.sku, 'brand': product.brand, 'name': presentation.website_title or product.name, 'quantity': item.quantity, 'unitPriceKopecks': product.price_kopecks})
    created_at = utcnow()
    order_id = new_id()
    order = Order(id=order_id, number=f'L-{created_at:%Y%m%d}-{order_id.upper()}', created_at=created_at, customer=body.customer.model_dump(), fulfillment=body.fulfillment.model_dump(), items=snapshots, total_kopecks=total, status='new', integration_status='pending_sync', version=1)
    db.add(order)
    db.flush()
    for item in body.items:
        products[item.productId].reserved += item.quantity
    db.add(integration.Outbox(id=new_id(), order_id=order.id, event_type='order_export', payload={'externalOrderId': order.id, 'customer': order.customer, 'fulfillment': order.fulfillment, 'items': order.items, 'totalKopecks': total}, state='queued', attempts=0))
    return order


@router.post('/orders')
def create_order(body: CreateOrderRequest, response: Response, idempotency_key: str | None = Header(default=None), db: Session = Depends(get_db)):
    key = parse_idempotency_key(idempotency_key)
    digest = hashlib.sha256(json.dumps(body.model_dump(), ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    lock_attempt(db, 'order:' + key)
    previous = db.get(OrderAttempt, key)
    response.headers['Cache-Control'] = 'no-store'
    if previous:
        if previous.digest != digest:
            raise ApiError(409, 'IDEMPOTENCY_CONFLICT', 'С этим ключом уже принят другой запрос.')
        response.status_code = 200
        return previous.receipt
    order = build_order(db, body)
    receipt = order_receipt(order)
    db.add(OrderAttempt(idempotency_key=key, digest=digest, order_id=order.id, receipt=receipt))
    db.commit()
    response.status_code = 201
    return receipt


@router.get('/manager/orders')
def list_orders(response: Response, status: OrderStatus | None = None, integrationStatus: IntegrationStatus | None = None, q: Annotated[str, Query(max_length=120, pattern=r'^[^\x00]*$')] = '', offset: Annotated[int, Query(ge=0, le=MAX_SAFE_INTEGER)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 24, db: Session = Depends(get_db), staff=Depends(get_current_staff)):
    query = select(Order)
    if status:
        query = query.where(Order.status == status)
    if integrationStatus:
        query = query.where(Order.integration_status == integrationStatus)
    if q.strip():
        search = '%' + q.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        query = query.where(or_(Order.number.ilike(search, escape='\\'), Order.customer['name'].astext.ilike(search, escape='\\'), Order.customer['phone'].astext.ilike(search, escape='\\')))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    orders = db.scalars(query.order_by(Order.created_at.desc(), Order.id.desc()).offset(offset).limit(limit))
    response.headers['Cache-Control'] = 'no-store'
    return page_response([manager_order(db, order) for order in orders], total, offset, limit)


@router.get('/manager/orders/{order_id}')
def get_order(order_id: Annotated[str, Path(min_length=1, max_length=128, pattern=r'^[^\x00-\x1f\x7f]+$')], response: Response, db: Session = Depends(get_db), staff=Depends(get_current_staff)):
    order = db.get(Order, order_id)
    if order is None:
        raise ApiError(404, 'NOT_FOUND', 'Заказ не найден.')
    response.headers['Cache-Control'] = 'no-store'
    response.headers['ETag'] = f'"{order.version}"'
    return manager_order(db, order)


@router.patch('/manager/orders/{order_id}/status')
def update_status(order_id: Annotated[str, Path(min_length=1, max_length=128, pattern=r'^[^\x00-\x1f\x7f]+$')], body: StatusInput, response: Response, if_match: str | None = Header(default=None), db: Session = Depends(get_db), staff=Depends(require_mutation)):
    from .catalog import Product

    order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise ApiError(404, 'NOT_FOUND', 'Заказ не найден.')
    check_version(order, if_match)
    if body.status == 'cancelled':
        event = db.scalar(select(integration.Outbox).where(integration.Outbox.order_id == order.id, integration.Outbox.event_type == 'order_export').with_for_update())
        if not can_cancel(db, order, event):
            raise ApiError(409, 'CANCEL_NOT_ALLOWED', 'Результат отправки не позволяет безопасно отменить заказ в CRM.')
        products = {product.id: product for product in db.scalars(select(Product).where(Product.id.in_([item['productId'] for item in order.items])).order_by(Product.id).with_for_update())}
        for item in order.items:
            product = products.get(item['productId'])
            if product is None or product.reserved < item['quantity']:
                raise ApiError(409, 'RESERVE_CONFLICT', 'Локальный резерв требует проверки. Отмена не выполнена.')
            product.reserved -= item['quantity']
        event.state = 'cancelled'
        order.integration_status = 'cancelled_before_sync'
    elif not (body.status == 'ready' and order.status == 'confirmed' or body.status == 'completed' and order.status == 'ready') or order.integration_status != 'exported' or not order.external_document_id:
        raise ApiError(409, 'INVALID_TRANSITION', 'Этот переход статуса недоступен.')
    order.status = body.status
    order.version += 1
    record_audit(db, staff, 'order.status_changed', order.id, order.version)
    db.commit()
    response.headers['Cache-Control'] = 'no-store'
    response.headers['ETag'] = f'"{order.version}"'
    return manager_order(db, order)
