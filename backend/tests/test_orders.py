from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import integration
from app.auth import AuditEntry
from app.catalog import Category, Product, ProductPresentation
from app.common import ApiError, utcnow
from app.integration import IntegrationState, Outbox
from app.orders import Order, OrderAttempt, advisory_key


@pytest.fixture
def order_product(db, monkeypatch):
    monkeypatch.setattr(integration, 'get_erp_adapter', lambda: type('TestAdapter', (), {'is_configured': True})())
    category = Category(id='order-test-category', name='Заказы', normalized_name='заказы', position=0, is_visible=True, version=1)
    product = Product(id='order-test-product', external_id='test-erp-product', sku='TEST-ORDER', brand='Test', name='Тестовый фильтр', price_kopecks=12500, stock=4, reserved=0, is_active=True, updated_at=utcnow())
    presentation = ProductPresentation(product_id=product.id, category_id=category.id, website_title=None, website_description=None, image_ids=[], version=1)
    db.add_all([category, product, presentation])
    state = db.get(IntegrationState, 1)
    if state is None:
        state = IntegrationState(id=1)
        db.add(state)
    state.catalog_updated_at = utcnow()
    state.state = 'healthy'
    db.commit()
    return product


def order_body(product, quantity=1):
    return {'customer': {'name': 'Покупатель', 'phone': '+7 (999) 123-45-67', 'email': ''}, 'fulfillment': {'method': 'pickup'}, 'items': [{'productId': product.id, 'quantity': quantity, 'expectedUnitPriceKopecks': product.price_kopecks}]}


def submit(client, product, *, quantity=1, key=None, body=None):
    return client.post('/api/orders', json=body or order_body(product, quantity), headers={'Cookie': '', 'Idempotency-Key': key or str(uuid4())})


def test_disabled_erp_accepts_manual_order_and_reports_real_health(client, order_product, monkeypatch, admin_headers, manager_headers, db):
    monkeypatch.setattr(integration, 'get_erp_adapter', lambda: integration.DisabledAdapter())
    response = submit(client, order_product)
    assert response.status_code == 201
    assert response.json()['integrationStatus'] == 'not_connected'
    assert db.scalar(select(func.count()).select_from(Order)) == 1
    health = client.get('/api/manager/integration/health', headers=admin_headers)
    assert health.status_code == 200
    assert health.json()['state'] == 'not_connected'
    assert client.get('/api/manager/integration/health', headers=manager_headers).status_code == 200
    assert client.post('/api/manager/integration/sync', headers={**admin_headers, 'Idempotency-Key': str(uuid4())}).status_code == 503


def test_order_atomic_snapshot_and_safe_idempotent_receipt(client, order_product, db):
    key = str(uuid4())
    response = submit(client, order_product, quantity=2, key=key)
    assert response.status_code == 201
    receipt = response.json()
    assert set(receipt) == {'id', 'number', 'createdAt', 'totalKopecks', 'status', 'integrationStatus'}
    assert receipt['totalKopecks'] == 25000
    assert receipt['status'] == 'new'
    assert receipt['integrationStatus'] == 'pending_sync'
    assert response.headers['cache-control'] == 'no-store'
    order = db.get(Order, receipt['id'])
    assert order.customer['phone'] == '+79991234567'
    assert order.items[0]['unitPriceKopecks'] == 12500
    db.refresh(order_product)
    assert order_product.reserved == 2
    assert db.scalar(select(func.count()).select_from(OrderAttempt)) == 1
    assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.event_type == 'order_export')) == 1
    event = db.scalar(select(Outbox).where(Outbox.event_type == 'order_telegram'))
    assert event.order_id == order.id
    assert event.payload == {'orderId': order.id}
    assert event.state == 'queued' and event.attempts == 0
    order.status = 'confirmed'
    order.integration_status = 'exported'
    order.external_document_id = 'private-external-document'
    db.commit()
    replay = submit(client, order_product, quantity=2, key=key)
    assert replay.status_code == 200
    assert replay.json() == receipt
    conflict = submit(client, order_product, key=key)
    assert conflict.status_code == 409
    assert conflict.json()['code'] == 'IDEMPOTENCY_CONFLICT'


@pytest.mark.parametrize('change', [
    lambda b: b['items'][0].update(quantity=True),
    lambda b: b['items'][0].update(quantity=1.5),
    lambda b: b['items'][0].update(quantity=1000),
    lambda b: b['items'].append(dict(b['items'][0])),
    lambda b: b['customer'].update(phone='not a phone'),
    lambda b: b['customer'].update(phone='+7٩٩٩١٢٣٤٥٦٧'),
    lambda b: b['customer'].update(name='   '),
    lambda b: b['customer'].update(name='Имя\x00'),
    lambda b: b['customer'].update(email='broken email'),
    lambda b: b['customer'].update(email='a\x00@example.test'),
    lambda b: b.update(fulfillment={'method': 'delivery', 'address': '   '}),
    lambda b: b.update(fulfillment={'method': 'delivery', 'address': 'Адрес\x00'}),
    lambda b: b.update(totalKopecks=1),
])
def test_order_validates_every_untrusted_field(client, order_product, change, db):
    body = order_body(order_product)
    change(body)
    assert submit(client, order_product, body=body).status_code == 422
    assert db.scalar(select(func.count()).select_from(Order)) == 0
    db.refresh(order_product)
    assert order_product.reserved == 0


def test_order_conflicts_do_not_leave_attempts_or_reserves(client, order_product, db):
    body = order_body(order_product)
    body['items'][0]['expectedUnitPriceKopecks'] = 100
    response = submit(client, order_product, body=body)
    assert response.status_code == 409
    assert response.json()['code'] == 'PRICE_CHANGED'
    assert response.json()['details'][0]['currentPriceKopecks'] == 12500
    response = submit(client, order_product, quantity=5)
    assert response.status_code == 409
    assert response.json()['code'] == 'OUT_OF_STOCK'
    assert response.json()['details'][0]['availableStock'] == 4
    assert db.scalar(select(func.count()).select_from(OrderAttempt)) == 0
    assert db.scalar(select(func.count()).select_from(Outbox)) == 0
    db.refresh(order_product)
    assert order_product.reserved == 0


def test_hidden_category_blocks_orders(client, order_product, db):
    category = db.get(Category, 'order-test-category')
    category.is_visible = False
    db.commit()
    response = submit(client, order_product)
    assert response.status_code == 409
    assert response.json()['code'] == 'PRODUCT_UNAVAILABLE'


def test_stale_catalog_accepts_orders_and_remains_visible_to_staff(client, order_product, db, admin_headers):
    db.get(IntegrationState, 1).catalog_updated_at = utcnow() - timedelta(days=2)
    db.commit()
    response = submit(client, order_product)
    assert response.status_code == 201, response.text
    db.refresh(order_product)
    assert order_product.reserved == 1
    health = client.get('/api/manager/integration/health', headers=admin_headers)
    assert health.status_code == 200
    assert health.json()['catalogStale'] is True


def test_last_unit_is_reserved_once_under_real_concurrent_transactions(client, order_product, db):
    order_product.stock = 1
    db.commit()
    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(lambda _: submit(client, order_product), range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    db.refresh(order_product)
    assert order_product.reserved == 1
    assert db.scalar(select(func.count()).select_from(Order)) == 1


def test_parallel_idempotency_key_returns_retry_instead_of_waiting(client, order_product, db):
    key = str(uuid4())
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': advisory_key('order:' + key)})
    response = submit(client, order_product, key=key)
    assert response.status_code == 409
    assert response.json()['code'] == 'IDEMPOTENCY_IN_PROGRESS'
    assert response.headers['retry-after'] == '1'
    db.rollback()


def test_safe_cancellation_releases_reserve_outbox_and_audits_atomically(client, order_product, db, manager_headers):
    receipt = submit(client, order_product, quantity=2).json()
    path = f"/api/manager/orders/{receipt['id']}/status"
    response = client.patch(path, json={'status': 'cancelled'}, headers={**manager_headers, 'If-Match': '"1"'})
    assert response.status_code == 200
    assert response.json()['status'] == 'cancelled'
    assert response.json()['integrationStatus'] == 'cancelled_before_sync'
    assert response.headers['etag'] == '"2"'
    db.refresh(order_product)
    assert order_product.reserved == 0
    assert db.scalar(select(Outbox.state).where(Outbox.event_type == 'order_export')) == 'cancelled'
    assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == 'order.status_changed')) == 1
    assert client.patch(path, json={'status': 'ready'}, headers={**manager_headers, 'If-Match': '"1"'}).status_code == 412


def test_manager_cannot_confirm_or_cancel_uncertain_export(client, order_product, db, manager_headers):
    receipt = submit(client, order_product).json()
    path = f"/api/manager/orders/{receipt['id']}"
    assert client.patch(path + '/status', json={'status': 'confirmed'}, headers={**manager_headers, 'If-Match': '"1"'}).status_code == 409
    event = db.scalar(select(Outbox).where(Outbox.event_type == 'order_export'))
    event.state = 'uncertain'
    event.attempts = 1
    event.lease_until = utcnow() - timedelta(minutes=1)
    db.commit()
    assert client.get(path, headers=manager_headers).json()['canCancel'] is False
    response = client.patch(path + '/status', json={'status': 'cancelled'}, headers={**manager_headers, 'If-Match': '"1"'})
    assert response.status_code == 409
    assert response.json()['code'] == 'CANCEL_NOT_ALLOWED'
    db.refresh(order_product)
    assert order_product.reserved == 1


def test_manager_transitions_require_confirmed_external_document_and_version(client, order_product, db, manager_headers):
    receipt = submit(client, order_product).json()
    order = db.get(Order, receipt['id'])
    order.status = 'confirmed'
    order.integration_status = 'exported'
    db.commit()
    path = f"/api/manager/orders/{order.id}/status"
    assert client.patch(path, json={'status': 'ready'}, headers={**manager_headers, 'If-Match': '"1"'}).status_code == 409
    order.external_document_id = 'erp-document'
    db.commit()
    ready = client.patch(path, json={'status': 'ready'}, headers={**manager_headers, 'If-Match': '"1"'})
    assert ready.status_code == 200
    assert client.patch(path, json={'status': 'completed'}, headers={**manager_headers, 'If-Match': '"2"'}).status_code == 200
    assert client.patch(path, json={'status': 'cancelled'}, headers={**manager_headers, 'If-Match': '"3"'}).status_code == 409


def test_orders_are_private_and_filtered_by_server(client, order_product, manager_headers):
    receipt = submit(client, order_product).json()
    assert client.get('/api/manager/orders', headers={'Cookie': ''}).status_code == 401
    assert client.get(f"/api/manager/orders/{receipt['id']}", headers={'Cookie': ''}).status_code == 401
    response = client.get('/api/manager/orders?q=%2B79991234567&status=new&limit=1', headers=manager_headers)
    assert response.status_code == 200
    assert response.json()['total'] == 1
    assert response.json()['items'][0]['customer']['phone'] == '+79991234567'
    assert client.get('/api/manager/orders?q=missing', headers=manager_headers).json()['total'] == 0
    assert client.get('/api/manager/orders', params={'q': 'text\x00'}, headers=manager_headers).status_code == 422
    assert client.get('/api/manager/orders/invalid%00id', headers=manager_headers).status_code == 422


@pytest.mark.parametrize('offset', [9007199254740992, 9223372036854775808])
def test_manager_pagination_rejects_unsafe_offset(client, manager_headers, offset):
    response = client.get('/api/manager/orders', params={'offset': offset}, headers=manager_headers)
    assert response.status_code == 422


def test_sync_command_is_idempotent_and_uses_one_live_job(client, order_product, admin_headers, db):
    headers = {**admin_headers, 'Idempotency-Key': str(uuid4())}
    first = client.post('/api/manager/integration/sync', headers=headers)
    assert first.status_code == 202
    assert client.post('/api/manager/integration/sync', headers=headers).json() == first.json()
    other = client.post('/api/manager/integration/sync', headers={**admin_headers, 'Idempotency-Key': str(uuid4())})
    assert other.json() == first.json()
    assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.event_type == 'catalog_sync')) == 1


def test_retry_keeps_external_order_id_event_and_uncertainty(client, order_product, admin_headers, db):
    receipt = submit(client, order_product).json()
    order = db.get(Order, receipt['id'])
    event = db.scalar(select(Outbox).where(Outbox.order_id == order.id, Outbox.event_type == 'order_export'))
    event.state = 'uncertain'
    event.attempts = 1
    event.lease_until = utcnow() - timedelta(minutes=1)
    order.integration_status = 'error'
    db.commit()
    path = f'/api/manager/integration/orders/{order.id}/retry'
    headers = {**admin_headers, 'Idempotency-Key': str(uuid4())}
    first = client.post(path, headers=headers)
    assert first.status_code == 202
    assert first.json() == {'jobId': event.id, 'state': 'queued'}
    assert client.post(path, headers=headers).json() == first.json()
    db.refresh(event)
    assert event.attempts == 1
    assert event.payload['externalOrderId'] == order.id
    db.refresh(order)
    assert order.integration_status == 'pending_sync'
    assert client.get(f'/api/manager/orders/{order.id}', headers=admin_headers).json()['canCancel'] is False
    assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.event_type == 'order_export')) == 1


def test_idempotency_header_is_required_uuid(client, order_product):
    assert client.post('/api/orders', json=order_body(order_product), headers={'Cookie': ''}).status_code == 400
    assert client.post('/api/orders', json=order_body(order_product), headers={'Cookie': '', 'Idempotency-Key': 'bad-key'}).status_code == 400


def test_commit_failure_rolls_back_order_reserve_attempt_and_outbox(client, order_product, db, monkeypatch):
    key = str(uuid4())

    def failing_commit(session):
        session.flush()
        raise ApiError(503, 'DATABASE_UNAVAILABLE', 'Не удалось сохранить заказ.')

    with monkeypatch.context() as patch:
        patch.setattr(Session, 'commit', failing_commit)
        assert submit(client, order_product, key=key).status_code == 503
    db.refresh(order_product)
    assert order_product.reserved == 0
    assert db.scalar(select(func.count()).select_from(Order)) == 0
    assert db.scalar(select(func.count()).select_from(OrderAttempt)) == 0
    assert db.scalar(select(func.count()).select_from(Outbox)) == 0
    assert submit(client, order_product, key=key).status_code == 201


def test_sum_cannot_exceed_javascript_safe_integer(client, order_product, db):
    order_product.price_kopecks = 9007199254740991
    db.commit()
    assert submit(client, order_product, quantity=2).status_code == 422
    db.refresh(order_product)
    assert order_product.reserved == 0
    assert db.scalar(select(func.count()).select_from(Order)) == 0


def test_fractional_order_reserve_rounding_and_cancel(client, order_product, db, manager_headers):
    from decimal import Decimal

    order_product.unit = 'кг'
    order_product.price_kopecks = 101
    db.commit()
    body = order_body(order_product, 0.5)
    body['items'][0]['expectedUnit'] = 'кг'
    response = submit(client, order_product, body=body)
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert receipt['totalKopecks'] == 51
    order = db.get(Order, receipt['id'])
    assert order.items[0]['quantity'] == 0.5
    assert order.items[0]['unit'] == 'кг'
    assert order.items[0]['lineTotalKopecks'] == 51
    event = db.scalar(select(Outbox).where(Outbox.order_id == order.id, Outbox.event_type == 'order_export'))
    assert event.payload['items'] == order.items
    db.refresh(order_product)
    assert order_product.reserved == Decimal('0.5')
    cancelled = client.patch(f'/api/manager/orders/{order.id}/status', json={'status': 'cancelled'}, headers={**manager_headers, 'If-Match': '"1"'})
    assert cancelled.status_code == 200, cancelled.text
    db.refresh(order_product)
    assert order_product.reserved == 0


def test_legacy_unitless_basket_cannot_reinterpret_new_unit(client, order_product, db):
    order_product.unit = 'кг'
    db.commit()
    response = submit(client, order_product)
    assert response.status_code == 409, response.text
    assert response.json()['code'] == 'UNIT_CHANGED'
    assert response.json()['details'] == [{'productId': order_product.id, 'code': 'UNIT_CHANGED', 'currentUnit': 'кг'}]
    db.refresh(order_product)
    assert order_product.reserved == 0


@pytest.mark.parametrize('observation_age', [2, None])
def test_imported_product_observation_does_not_block_orders(client, order_product, db, observation_age):
    order_product.accounting_source = 'file:stock'
    order_product.stock_observed_at = utcnow() - timedelta(days=observation_age) if observation_age else None
    order_product.updated_at = utcnow()
    db.commit()
    response = submit(client, order_product)
    assert response.status_code == 201, response.text
    db.refresh(order_product)
    assert order_product.reserved == 1


def test_legacy_snapshot_cancellation_uses_normal_units(client, order_product, db, manager_headers):
    receipt = submit(client, order_product, quantity=2).json()
    order = db.get(Order, receipt['id'])
    order.items = [{key: value for key, value in item.items() if key not in {'unit', 'lineTotalKopecks'}} for item in order.items]
    db.commit()
    response = client.patch(f'/api/manager/orders/{order.id}/status', json={'status': 'cancelled'}, headers={**manager_headers, 'If-Match': '"1"'})
    assert response.status_code == 200, response.text
    db.refresh(order_product)
    assert order_product.reserved == 0


def test_last_thousandth_is_reserved_once_under_real_concurrent_transactions(client, order_product, db):
    from decimal import Decimal

    order_product.unit = 'кг'
    order_product.stock = Decimal('0.001')
    db.commit()
    body = order_body(order_product, 0.001)
    body['items'][0]['expectedUnit'] = 'кг'
    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(lambda _: submit(client, order_product, body=body), range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    db.refresh(order_product)
    assert order_product.reserved == Decimal('0.001')
    assert db.scalar(select(func.count()).select_from(Order)) == 1


def test_reserved_product_unit_change_is_rejected(client, order_product, db):
    assert submit(client, order_product).status_code == 201
    db.refresh(order_product)
    order_product.unit = 'кг'
    with pytest.raises(ApiError) as error:
        db.commit()
    assert error.value.code == 'UNIT_RESERVED'
    db.rollback()
    db.refresh(order_product)
    assert order_product.unit == 'шт'
    assert order_product.reserved == 1


@pytest.mark.parametrize('version', [1, 2])
def test_historical_guest_digest_replays_before_catalog_revalidation(client, order_product, db, monkeypatch, version):
    import hashlib
    import json

    key = str(uuid4())
    body = order_body(order_product, 2)
    first = submit(client, order_product, body=body, key=key)
    assert first.status_code == 201
    frozen = {'customer': {'name': 'Покупатель', 'phone': '+79991234567', 'email': ''}, 'fulfillment': {'method': 'pickup', 'address': ''}, 'items': [{'productId': order_product.id, 'quantity': 2, 'expectedUnitPriceKopecks': 12500}]}
    payload = frozen if version == 1 else {'actor': 'guest', 'body': frozen}
    attempt = db.get(OrderAttempt, key)
    attempt.digest_version = version
    attempt.digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    db.commit()
    monkeypatch.setattr(integration, 'get_erp_adapter', lambda: integration.DisabledAdapter())
    replay = submit(client, order_product, body=body, key=key)
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert db.scalar(select(func.count()).select_from(Order)) == 1


def test_unit_change_guard_loads_expired_previous_unit(client, order_product, db):
    assert submit(client, order_product).status_code == 201
    db.expire(order_product)
    order_product.unit = 'кг'
    with pytest.raises(ApiError) as error:
        db.commit()
    assert error.value.code == 'UNIT_RESERVED'
    db.rollback()
    db.refresh(order_product)
    assert order_product.unit == 'шт'
    assert order_product.reserved == 1
