from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app import integration
from app.auth import AuditEntry
from app.catalog import Category, Product
from app.common import utcnow
from app.integration import IntegrationState, Outbox
from app.orders import Order, OrderAttempt
from test_orders import order_body, order_product, submit


@pytest.fixture
def manual_product(order_product, monkeypatch):
    monkeypatch.setattr(integration, 'get_erp_adapter', lambda: integration.DisabledAdapter())
    return order_product


def change_status(client, receipt, headers, status, version):
    return client.patch(f"/api/manager/orders/{receipt['id']}/status", json={'status': status},
                        headers={**headers, 'If-Match': f'"{version}"'})


def test_manual_acceptance_reserves_and_queues_one_notification_on_replay(client, manual_product, db, monkeypatch):
    key = str(uuid4())
    first = submit(client, manual_product, quantity=2, key=key)
    assert first.status_code == 201, first.text
    receipt = first.json()
    assert receipt['status'] == 'new' and receipt['integrationStatus'] == 'not_connected'
    order = db.get(Order, receipt['id'])
    assert order.external_document_id is None
    events = db.scalars(select(Outbox).where(Outbox.order_id == order.id)).all()
    assert len(events) == 1
    assert events[0].event_type == 'order_telegram'
    assert events[0].payload == {'orderId': order.id}
    assert events[0].state == 'queued' and events[0].attempts == 0
    monkeypatch.setattr(integration, 'get_erp_adapter', lambda: type('TestAdapter', (), {'is_configured': True})())
    replay = submit(client, manual_product, quantity=2, key=key)
    assert replay.status_code == 200 and replay.json() == receipt
    assert submit(client, manual_product, quantity=1, key=key).status_code == 409
    db.refresh(manual_product)
    assert manual_product.reserved == 2
    assert db.scalar(select(func.count()).select_from(Order)) == 1
    assert db.scalar(select(func.count()).select_from(OrderAttempt)) == 1
    assert db.scalar(select(func.count()).select_from(Outbox)) == 1


def test_notification_write_failure_rolls_back_manual_order_reserve_and_key(client, manual_product, db, db_engine):
    schema = db_engine.get_execution_options()['schema_translate_map'][None]
    with db_engine.begin() as connection:
        connection.exec_driver_sql(f"ALTER TABLE {schema}.outbox ADD CONSTRAINT reject_order_telegram CHECK (event_type != 'order_telegram')")
    key = str(uuid4())
    response = submit(client, manual_product, key=key)
    assert response.status_code == 409, response.text
    db.refresh(manual_product)
    assert manual_product.reserved == 0
    for model in (Order, OrderAttempt, Outbox):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    db.rollback()
    with db_engine.begin() as connection:
        connection.exec_driver_sql(f'ALTER TABLE {schema}.outbox DROP CONSTRAINT reject_order_telegram')
    assert submit(client, manual_product, key=key).status_code == 201


def test_manual_last_unit_is_reserved_once_by_concurrent_transactions(client, manual_product, db):
    manual_product.stock = 1
    db.commit()
    body = order_body(manual_product)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: submit(client, manual_product, body=body), range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    db.refresh(manual_product)
    assert manual_product.reserved == 1
    assert db.scalar(select(func.count()).select_from(Order)) == 1
    assert db.scalar(select(func.count()).select_from(Outbox)) == 1


@pytest.mark.parametrize(('guard', 'code'), [
    ('price', 'PRICE_CHANGED'), ('unit', 'UNIT_CHANGED'), ('hidden', 'PRODUCT_UNAVAILABLE'),
    ('catalog_stale', 'CATALOG_STALE'), ('stock_stale', 'CATALOG_STALE'), ('stock', 'OUT_OF_STOCK'),
])
def test_manual_acceptance_keeps_catalog_guards(client, manual_product, db, guard, code):
    body = order_body(manual_product)
    if guard == 'price':
        body['items'][0]['expectedUnitPriceKopecks'] = 1
    elif guard == 'unit':
        body['items'][0]['expectedUnit'] = 'кг'
    elif guard == 'hidden':
        db.get(Category, 'order-test-category').is_visible = False
    elif guard == 'catalog_stale':
        db.get(IntegrationState, 1).catalog_updated_at = utcnow() - timedelta(days=2)
    elif guard == 'stock_stale':
        manual_product.accounting_source = 'file:stock'
        manual_product.stock_observed_at = utcnow() - timedelta(days=2)
    else:
        manual_product.stock = 0
    db.commit()
    response = submit(client, manual_product, body=body)
    assert response.status_code in (409, 503), response.text
    assert response.json()['code'] == code
    db.refresh(manual_product)
    assert manual_product.reserved == 0
    assert db.scalar(select(func.count()).select_from(Outbox)) == 0


def test_manual_manager_progression_consumes_stock_once_with_audit_and_version(client, manual_product, manager_headers, db, monkeypatch):
    first = submit(client, manual_product, quantity=2)
    assert first.status_code == 201, first.text
    receipt = first.json()
    assert change_status(client, receipt, manager_headers, 'ready', 1).status_code == 409
    assert change_status(client, receipt, manager_headers, 'completed', 1).status_code == 409
    assert change_status(client, receipt, manager_headers, 'confirmed', 1).status_code == 200
    assert change_status(client, receipt, manager_headers, 'ready', 1).status_code == 412
    # Persisted manual orders remain manual when ERP is configured later.
    monkeypatch.setattr(integration, 'get_erp_adapter', lambda: type('TestAdapter', (), {'is_configured': True})())
    ready = change_status(client, receipt, manager_headers, 'ready', 2)
    assert ready.status_code == 200 and ready.json()['canCancel'] is True
    db.refresh(manual_product)
    assert manual_product.stock == 4 and manual_product.reserved == 2
    completed = change_status(client, receipt, manager_headers, 'completed', 3)
    assert completed.status_code == 200, completed.text
    assert completed.json()['integrationStatus'] == 'not_connected'
    assert completed.json()['canCancel'] is False
    assert completed.headers['etag'] == '"4"'
    db.refresh(manual_product)
    assert manual_product.stock == 2 and manual_product.reserved == 0
    assert change_status(client, receipt, manager_headers, 'completed', 3).status_code == 412
    assert change_status(client, receipt, manager_headers, 'completed', 4).status_code == 409
    assert change_status(client, receipt, manager_headers, 'cancelled', 4).status_code == 409
    db.refresh(manual_product)
    assert manual_product.stock == 2 and manual_product.reserved == 0
    assert db.scalars(select(AuditEntry.version).where(AuditEntry.action == 'order.status_changed').order_by(AuditEntry.version)).all() == [2, 3, 4]


@pytest.mark.parametrize('steps', [[], ['confirmed'], ['confirmed', 'ready']])
def test_manual_cancellation_releases_reserve_from_each_live_status(client, manual_product, manager_headers, db, steps):
    first = submit(client, manual_product, quantity=2)
    assert first.status_code == 201, first.text
    receipt = first.json()
    for version, status in enumerate(steps, 1):
        assert change_status(client, receipt, manager_headers, status, version).status_code == 200
    response = change_status(client, receipt, manager_headers, 'cancelled', len(steps) + 1)
    assert response.status_code == 200, response.text
    assert response.json()['integrationStatus'] == 'not_connected'
    assert response.json()['canCancel'] is False
    db.refresh(manual_product)
    assert manual_product.stock == 4 and manual_product.reserved == 0
    assert db.scalar(select(Outbox.state).where(Outbox.event_type == 'order_telegram')) == 'cancelled'
    assert change_status(client, receipt, manager_headers, 'confirmed', len(steps) + 2).status_code == 409


@pytest.mark.parametrize('notification', ['attempted', 'leased', 'relay', 'running', 'done'])
def test_manual_cancellation_preserves_notifications_that_may_have_been_sent(client, manual_product, manager_headers, db, notification):
    first = submit(client, manual_product)
    assert first.status_code == 201, first.text
    receipt = first.json()
    event = db.scalar(select(Outbox).where(Outbox.order_id == receipt['id']))
    if notification == 'attempted':
        event.attempts = 1
    elif notification == 'leased':
        event.lease_until = utcnow() + timedelta(minutes=1)
    elif notification == 'relay':
        event.payload = {**event.payload, 'relay_request': {'request_id': event.id}}
    else:
        event.state = notification
    db.commit()
    original = (event.state, event.attempts, event.payload, event.lease_until)
    response = change_status(client, receipt, manager_headers, 'cancelled', 1)
    assert response.status_code == 200, response.text
    db.refresh(event)
    assert (event.state, event.attempts, event.payload, event.lease_until) == original
    db.refresh(manual_product)
    assert manual_product.reserved == 0


def test_manual_fractional_completion_keeps_available_stock_constant(client, manual_product, manager_headers, db):
    manual_product.unit = 'кг'
    manual_product.price_kopecks = 101
    manual_product.stock = Decimal('1.001')
    db.commit()
    body = order_body(manual_product, 0.501)
    body['items'][0]['expectedUnit'] = 'кг'
    first = submit(client, manual_product, body=body)
    assert first.status_code == 201, first.text
    receipt = first.json()
    assert receipt['totalKopecks'] == 51
    for version, status in enumerate(('confirmed', 'ready', 'completed'), 1):
        assert change_status(client, receipt, manager_headers, status, version).status_code == 200
    db.refresh(manual_product)
    assert manual_product.stock == Decimal('0.500') and manual_product.reserved == 0


@pytest.mark.parametrize('conflict', ['reserve', 'unit', 'physical_stock'])
def test_manual_completion_rejects_inconsistent_stock_without_partial_changes(client, manual_product, manager_headers, db, conflict):
    first = submit(client, manual_product, quantity=2)
    assert first.status_code == 201, first.text
    receipt = first.json()
    assert change_status(client, receipt, manager_headers, 'confirmed', 1).status_code == 200
    assert change_status(client, receipt, manager_headers, 'ready', 2).status_code == 200
    if conflict == 'reserve':
        manual_product.reserved = 1
    elif conflict == 'physical_stock':
        manual_product.stock = 1
    else:
        db.execute(Product.__table__.update().where(Product.id == manual_product.id).values(unit='кг'))
    db.commit()
    db.refresh(manual_product)
    before = (manual_product.stock, manual_product.reserved, manual_product.unit)
    response = change_status(client, receipt, manager_headers, 'completed', 3)
    assert response.status_code == 409, response.text
    assert response.json()['code'] == 'RESERVE_CONFLICT'
    db.refresh(manual_product)
    assert (manual_product.stock, manual_product.reserved, manual_product.unit) == before
    order = db.get(Order, receipt['id'])
    assert order.status == 'ready' and order.version == 3


def test_duplicate_concurrent_manual_completion_consumes_stock_once(client, manual_product, manager_headers, db):
    first = submit(client, manual_product, quantity=2)
    assert first.status_code == 201, first.text
    receipt = first.json()
    assert change_status(client, receipt, manager_headers, 'confirmed', 1).status_code == 200
    assert change_status(client, receipt, manager_headers, 'ready', 2).status_code == 200
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: change_status(client, receipt, manager_headers, 'completed', 3), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 412]
    db.refresh(manual_product)
    assert manual_product.stock == 2 and manual_product.reserved == 0


@pytest.mark.parametrize('status', ['completed', 'cancelled'])
def test_manual_stock_mutation_rolls_back_if_audit_cannot_be_saved(client, manual_product, manager_headers, db, db_engine, status):
    first = submit(client, manual_product, quantity=2)
    assert first.status_code == 201, first.text
    receipt = first.json()
    assert change_status(client, receipt, manager_headers, 'confirmed', 1).status_code == 200
    assert change_status(client, receipt, manager_headers, 'ready', 2).status_code == 200
    schema = db_engine.get_execution_options()['schema_translate_map'][None]
    with db_engine.begin() as connection:
        connection.exec_driver_sql(f"ALTER TABLE {schema}.audit_entries ADD CONSTRAINT reject_terminal_order_audit CHECK (action != 'order.status_changed' OR version != 4)")
    response = change_status(client, receipt, manager_headers, status, 3)
    assert response.status_code == 409, response.text
    db.refresh(manual_product)
    assert manual_product.stock == 4 and manual_product.reserved == 2
    order = db.get(Order, receipt['id'])
    assert order.status == 'ready' and order.version == 3
    assert db.scalar(select(Outbox.state).where(Outbox.order_id == order.id)) == 'queued'
    assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == 'order.status_changed')) == 2
    db.rollback()
    with db_engine.begin() as connection:
        connection.exec_driver_sql(f'ALTER TABLE {schema}.audit_entries DROP CONSTRAINT reject_terminal_order_audit')
    assert change_status(client, receipt, manager_headers, status, 3).status_code == 200


def test_disabling_adapter_cannot_make_erp_order_manually_confirmable(client, order_product, manager_headers, db, monkeypatch):
    receipt = submit(client, order_product).json()
    monkeypatch.setattr(integration, 'get_erp_adapter', lambda: integration.DisabledAdapter())
    for status in ('confirmed', 'ready', 'completed'):
        response = change_status(client, receipt, manager_headers, status, 1)
        assert response.status_code == 409, response.text
    db.refresh(order_product)
    assert order_product.stock == 4 and order_product.reserved == 1


def test_manual_marker_with_external_document_cannot_bypass_export_guards(client, manual_product, manager_headers, db):
    first = submit(client, manual_product)
    assert first.status_code == 201, first.text
    receipt = first.json()
    order = db.get(Order, receipt['id'])
    order.external_document_id = 'erp-document'
    db.commit()
    for status in ('confirmed', 'ready', 'cancelled'):
        assert change_status(client, receipt, manager_headers, status, 1).status_code == 409
