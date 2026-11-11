"""Private customer projections; contacts and ownership come from the session."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from pydantic import Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import feedback, orders
from .auth import get_current_user, require_customer_mutation
from .common import ApiError, page_response
from .db import get_db


class CreateOrderRequest(orders.InputModel):
    name: str = Field(default='', max_length=100)
    fulfillment: orders.FulfillmentInput
    items: list[orders.OrderItemInput] = Field(min_length=1, max_length=100)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        return feedback.clean_text(value)

    @model_validator(mode='after')
    def unique_products(self):
        return orders.CreateOrderRequest.unique_products(self)


class CreateFeedbackRequest(orders.InputModel):
    type: Literal['contact', 'request']
    message: str = Field(default='', max_length=2000)

    @field_validator('message')
    @classmethod
    def clean_message(cls, value):
        return feedback.clean_text(value, multiline=True)

    @model_validator(mode='after')
    def required_message(self):
        if self.type == 'request' and not self.message:
            raise ValueError('Укажите текст запроса.')
        return self


def order_receipt(order):
    return {key: value for key, value in orders.order_receipt(order).items() if key != 'integrationStatus'}


def customer_order(order):
    return {**order_receipt(order), 'items': order.items, 'fulfillment': order.fulfillment}


def customer_feedback(db, case):
    linked = db.scalar(select(orders.Order).where(orders.Order.id == case.order_id, orders.Order.customer_id == case.customer_id)) if case.order_id else None
    return {'id': case.id, 'number': case.number, 'createdAt': case.created_at.isoformat(), 'type': case.type, 'message': case.message, 'status': case.status, 'order': order_receipt(linked) if linked else None}


def owned_record(db, model, record_id, customer_id):
    record = db.scalar(select(model).where(model.id == record_id, model.customer_id == customer_id))
    if record is None:
        raise ApiError(404, 'NOT_FOUND', 'Запись не найдена.')
    return record


router = APIRouter(prefix='/api/customer')
RecordId = Annotated[str, Path(min_length=1, max_length=128, pattern=r'^[^\x00-\x1f\x7f]+$')]
Offset = Annotated[int, Query(ge=0, le=orders.MAX_SAFE_INTEGER)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get('/orders')
def list_orders(offset: Offset = 0, limit: Limit = 24, db: Session = Depends(get_db), user=Depends(get_current_user)):
    query = select(orders.Order).where(orders.Order.customer_id == user.id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    records = db.scalars(query.order_by(orders.Order.created_at.desc(), orders.Order.id.desc()).offset(offset).limit(limit))
    return page_response([customer_order(record) for record in records], total, offset, limit)


@router.get('/orders/{record_id}')
def get_order(record_id: RecordId, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return customer_order(owned_record(db, orders.Order, record_id, user.id))


@router.post('/orders', status_code=201)
def create_order(body: CreateOrderRequest, response: Response, idempotency_key: str | None = Header(default=None), db: Session = Depends(get_db), user=Depends(require_customer_mutation)):
    profile = user.profile
    customer = orders.CustomerInput.model_construct(name=body.name, phone=profile['phone'], email=profile['email'])
    trusted = orders.CreateOrderRequest.model_construct(customer=customer, fulfillment=body.fulfillment, items=body.items)
    receipt, created = orders.accept_order(db, trusted, orders.parse_idempotency_key(idempotency_key), customer_id=user.id, receipt_builder=customer_order)
    response.status_code = 201 if created else 200
    return receipt


@router.get('/feedback')
def list_feedback(offset: Offset = 0, limit: Limit = 24, db: Session = Depends(get_db), user=Depends(get_current_user)):
    query = select(feedback.Feedback).where(feedback.Feedback.customer_id == user.id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    records = db.scalars(query.order_by(feedback.Feedback.created_at.desc(), feedback.Feedback.id.desc()).offset(offset).limit(limit))
    return page_response([customer_feedback(db, record) for record in records], total, offset, limit)


@router.get('/feedback/{record_id}')
def get_feedback(record_id: RecordId, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return customer_feedback(db, owned_record(db, feedback.Feedback, record_id, user.id))


@router.post('/feedback', status_code=202)
def create_feedback(body: CreateFeedbackRequest, request: Request, idempotency_key: str | None = Header(default=None), db: Session = Depends(get_db), user=Depends(require_customer_mutation)):
    profile = user.profile
    trusted = feedback.CreateFeedbackRequest(type=body.type, message=body.message, phone=profile['phone'], email=profile['email'])
    case = feedback.accept_feedback(db, trusted, request, orders.parse_idempotency_key(idempotency_key), customer_id=user.id)
    return customer_feedback(db, case)
