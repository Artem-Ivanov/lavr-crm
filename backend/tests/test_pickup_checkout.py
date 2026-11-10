from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app import integration, orders
from app.customer_records import customer_order
from app.integration import Outbox
from app.orders import Order, OrderAttempt
from test_customer_records import customer, own_order
from test_feedback import mutation, submit as submit_feedback
from test_orders import order_body, order_product, submit


DELIVERY = {'method': 'delivery', 'address': 'Улица Тестовая, дом 1'}


def checkout(client, product, actor, signed_customer, fulfillment, key):
    if actor == 'customer':
        return own_order(client, product, signed_customer[1], key=key, fulfillment=fulfillment)
    body = order_body(product)
    body['fulfillment'] = fulfillment
    return submit(client, product, key=key, body=body)


@pytest.mark.parametrize('actor', ['guest', 'customer'])
@pytest.mark.parametrize('erp_configured', [False, True])
def test_storefront_delivery_rejection_is_atomic_and_pickup_can_reuse_key(client, db, order_product, customer, monkeypatch, actor, erp_configured):
    if not erp_configured:
        monkeypatch.setattr(integration, 'get_erp_adapter', lambda: integration.DisabledAdapter())
    key = str(uuid4())
    rejected = checkout(client, order_product, actor, customer, DELIVERY, key)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()['code'] == 'FULFILLMENT_UNAVAILABLE'
    assert 'самовывоз' in rejected.json()['message'].lower()
    for model in (Order, OrderAttempt, Outbox):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    db.refresh(order_product)
    assert order_product.reserved == 0
    accepted = checkout(client, order_product, actor, customer, {'method': 'pickup'}, key)
    assert accepted.status_code == 201, accepted.text
    order = db.get(Order, accepted.json()['id'])
    assert order.fulfillment == {'method': 'pickup', 'address': ''}
    assert order.integration_status == ('pending_sync' if erp_configured else 'not_connected')
    assert order.customer_id == (customer[0].id if actor == 'customer' else None)
    db.refresh(order_product)
    assert order_product.reserved == 1
    assert db.scalar(select(func.count()).select_from(Order)) == 1
    assert db.scalar(select(func.count()).select_from(OrderAttempt)) == 1
    assert set(db.scalars(select(Outbox.event_type))) == ({'order_export', 'order_telegram'} if erp_configured else {'order_telegram'})


@pytest.mark.parametrize('actor', ['guest', 'customer'])
def test_legacy_delivery_receipt_replays_before_pickup_policy_without_duplicates(client, db, order_product, customer, actor):
    body = order_body(order_product)
    body['fulfillment'] = DELIVERY
    trusted = orders.CreateOrderRequest.model_validate(body)
    user = customer[0] if actor == 'customer' else None
    if user:
        trusted.customer = orders.CustomerInput.model_construct(name='', phone=user.profile['phone'], email=user.profile['email'])
    key = str(uuid4())
    order = orders.build_order(db, trusted, customer_id=user.id if user else None)
    receipt = customer_order(order) if user else orders.order_receipt(order)
    identity = user.id if user else 'guest'
    db.add(OrderAttempt(idempotency_key=key, actor=identity, digest_version=2,
                        digest=orders.request_digest(trusted, identity), order_id=order.id, receipt=receipt))
    order.status = 'confirmed'
    db.commit()
    replay = checkout(client, order_product, actor, customer, DELIVERY, key)
    assert replay.status_code == 200, replay.text
    assert replay.json() == receipt
    db.refresh(order_product)
    db.refresh(order)
    assert order_product.reserved == 1
    assert order.fulfillment == DELIVERY
    assert order.status == 'confirmed'
    assert db.scalar(select(func.count()).select_from(Order)) == 1
    assert db.scalar(select(func.count()).select_from(OrderAttempt)) == 1
    assert db.scalar(select(func.count()).select_from(Outbox)) == 2


def test_manager_feedback_conversion_keeps_delivery_support(client, db, order_product, manager_headers):
    case = submit_feedback(client).json()
    assert mutation(client, case, manager_headers).status_code == 200
    body = order_body(order_product)
    body['fulfillment'] = DELIVERY
    converted = mutation(client, case, manager_headers, 'order', body, version=2, key=str(uuid4()))
    assert converted.status_code == 201, converted.text
    order = db.get(Order, converted.json()['order']['id'])
    assert order.fulfillment == DELIVERY
    db.refresh(order_product)
    assert order_product.reserved == 1
