"""Run the real migration against seeded legacy tables in a disposable schema."""
import hashlib
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.crypto import open_json, open_text


VERSIONS = Path(__file__).parents[1] / 'alembic' / 'versions'
LEGACY_ORDER = {'customer': {'name': 'Private buyer', 'phone': '+79991234567', 'email': 'private@example.test'},
                'fulfillment': {'method': 'delivery', 'address': 'Private street'},
                'items': [{'productId': 'old-product', 'quantity': 1, 'expectedUnitPriceKopecks': 100}]}
LEGACY_FEEDBACK = {'type': 'request', 'name': 'Private buyer', 'phone': '+79991234567', 'email': 'private@example.test',
                   'messenger': {'service': 'Telegram', 'address': '@private'}, 'message': 'Private request'}


def legacy_digest(body):
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def migration(prefix):
    path = next(VERSIONS.glob(prefix + '_*.py'))
    spec = importlib.util.spec_from_file_location('migration_' + prefix, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def legacy_database(db_engine):
    schema = 'migration_' + uuid4().hex
    engine = db_engine.execution_options(schema_translate_map={None: schema})
    with engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA {schema}')
    try:
        with engine.connect() as conn, conn.begin():
            conn.exec_driver_sql(f'SET LOCAL search_path TO {schema}')
            with Operations.context(MigrationContext.configure(conn)):
                for version in ('0001', '0002', '0003'):
                    migration(version).upgrade()
            metadata = sa.MetaData()
            metadata.reflect(conn, schema=schema)
            tables = {table.name: table for table in metadata.tables.values()}
            from app.common import utcnow
            now = utcnow()
            conn.execute(tables['staff'].insert(), {'id': 'legacy-operator', 'login': 'legacy-private-login', 'name': 'Legacy Private Name', 'role': 'manager', 'password_hash': 'preserved-argon2-hash', 'is_active': True})
            conn.execute(tables['staff_sessions'].insert(), {'id': 'old-session', 'staff_id': 'legacy-operator', 'token_hash': 'a' * 64, 'expires_at': now})
            customer = {'name': 'Private buyer', 'phone': '+79991234567', 'email': 'private@example.test'}
            receipt = {'id': 'legacy-order', 'number': 'L-LEGACY', 'createdAt': now.isoformat(), 'totalKopecks': 100, 'status': 'new', 'integrationStatus': 'pending_sync'}
            conn.execute(tables['orders'].insert(), {'id': 'legacy-order', 'number': 'L-LEGACY', 'created_at': now, 'customer': customer, 'fulfillment': {'method': 'delivery', 'address': 'Private street'}, 'items': [], 'total_kopecks': 100, 'status': 'new', 'integration_status': 'pending_sync', 'version': 1})
            conn.execute(tables['order_attempts'].insert(), {'idempotency_key': '12345678-1234-1234-1234-123456789abc', 'digest': legacy_digest(LEGACY_ORDER), 'order_id': 'legacy-order', 'receipt': receipt})
            conn.execute(tables['outbox'].insert(), {'id': 'erp-job', 'order_id': 'legacy-order', 'event_type': 'order_export', 'payload': {'customer': customer, 'fulfillment': {'address': 'Private street'}}, 'state': 'queued', 'attempts': 0, 'created_at': now})
            common = dict(created_at=now, updated_at=now, type='request', name='Private buyer', phone='+79991234567', email='private@example.test', messenger={'service': 'Telegram', 'address': '@private'}, message='Private request', assigned_staff_id='legacy-operator', version=1, receipt={'state': 'accepted'})
            conn.execute(tables['feedback'].insert(), {**common, 'id': 'legacy-feedback', 'number': 'F-LEGACY', 'status': 'converted', 'resolution': '', 'order_id': 'legacy-order', 'idempotency_key': '22345678-1234-1234-1234-123456789abc', 'digest': legacy_digest(LEGACY_FEEDBACK), 'conversion_response': {'name': 'Private buyer', 'history': [{'text': 'Private staff note'}]}})
            conn.execute(tables['feedback'].insert(), {**common, 'id': 'legacy-closed', 'number': 'F-CLOSED', 'status': 'closed', 'resolution': 'Private resolution', 'idempotency_key': '32345678-1234-1234-1234-123456789abc', 'digest': 'd' * 64})
            conn.execute(tables['feedback_notes'].insert(), {'id': 'legacy-note', 'feedback_id': 'legacy-feedback', 'staff_id': 'legacy-operator', 'created_at': now, 'text': 'Private staff note', 'contact_method': 'phone'})
            conn.execute(tables['audit_entries'].insert(), {'id': 'legacy-audit', 'staff_id': 'legacy-operator', 'action': 'feedback.claimed', 'entity_id': 'legacy-feedback', 'version': 1, 'created_at': now})
            yield conn, schema
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')


def test_upgrade_encrypts_every_legacy_copy_preserves_ids_and_restores_transaction(legacy_database, monkeypatch):
    conn, schema = legacy_database
    monkeypatch.setenv('PII_MIGRATION_MAINTENANCE', 'true')
    backup = conn.begin_nested()
    with Operations.context(MigrationContext.configure(conn)):
        migration('0004').upgrade()
    rows = {}
    for table in ('users', 'orders', 'order_attempts', 'feedback', 'feedback_notes', 'outbox', 'audit_entries'):
        rows[table] = [dict(row) for row in conn.execute(sa.text(f'SELECT * FROM {schema}.{table}')).mappings()]
    raw = json.dumps(rows, default=str)
    for value in ('legacy-private-login', 'Legacy Private Name', 'Private buyer', '+79991234567', 'private@example.test', 'Private street', 'Private request', 'Private staff note', 'Private resolution', '@private'):
        assert value not in raw
    user = rows['users'][0]
    assert user['id'] == 'legacy-operator' and user['role'] == 'operator'
    assert user['password_hash'] == 'preserved-argon2-hash'
    assert open_text(user['login_ciphertext'], table='users', record_id=user['id'], field='login') == 'legacy-private-login'
    assert user['profile_ciphertext'] is None and user['email_verified_at'] is None
    assert conn.scalar(sa.text(f'SELECT count(*) FROM {schema}.user_sessions')) == 0
    assert rows['orders'][0]['customer_id'] is None
    assert open_json(rows['orders'][0]['customer'], table='orders', record_id='legacy-order', field='customer')['email'] == 'private@example.test'
    attempt = rows['order_attempts'][0]
    assert attempt['actor'] == 'guest' and attempt['digest_version'] == 1 and attempt['digest'] == legacy_digest(LEGACY_ORDER)
    assert open_json(attempt['receipt'], table='order_attempts', record_id=attempt['idempotency_key'], field='receipt')['number'] == 'L-LEGACY'
    assert all(case['customer_id'] is None and case['actor'] == 'guest' and case['digest_version'] == 1 for case in rows['feedback'])
    assert next(case for case in rows['feedback'] if case['id'] == 'legacy-feedback')['digest'] == legacy_digest(LEGACY_FEEDBACK)
    assert rows['audit_entries'][0]['staff_id'] == 'legacy-operator'
    assert open_text(rows['feedback_notes'][0]['text'], table='feedback_notes', record_id='legacy-note', field='text') == 'Private staff note'
    # Replay executes the real services against migrated ORM rows; ERP is never contacted.
    from sqlalchemy.orm import Session
    from starlette.requests import Request
    from app import orders, feedback
    session = Session(conn, join_transaction_mode='create_savepoint')
    receipt, created = orders.accept_order(session, orders.CreateOrderRequest(**LEGACY_ORDER), attempt['idempotency_key'])
    assert created is False and receipt['number'] == 'L-LEGACY'
    case = feedback.accept_feedback(session, feedback.CreateFeedbackRequest(**LEGACY_FEEDBACK), Request({'type': 'http', 'headers': []}), '22345678-1234-1234-1234-123456789abc')
    assert case.receipt == {'state': 'accepted'} and case.customer_id is None
    session.close()
    closed = next(case for case in rows['feedback'] if case['id'] == 'legacy-closed')
    assert open_text(closed['resolution'], table='feedback', record_id='legacy-closed', field='resolution') == 'Private resolution'
    for table in ('audit_entries', 'feedback_notes', 'feedback', 'user_sessions'):
        assert any(fk['referred_table'] == 'users' for fk in sa.inspect(conn).get_foreign_keys(table))
    backup.rollback()
    assert conn.scalar(sa.text(f'SELECT login FROM {schema}.staff')) == 'legacy-private-login'
    assert conn.scalar(sa.text(f'SELECT count(*) FROM {schema}.staff_sessions')) == 1
    assert conn.scalar(sa.text(f'SELECT digest FROM {schema}.order_attempts')) == legacy_digest(LEGACY_ORDER)


def test_migration_refuses_unconfirmed_maintenance_and_missing_keys(legacy_database, monkeypatch):
    from app.config import settings
    conn, schema = legacy_database
    monkeypatch.delenv('PII_MIGRATION_MAINTENANCE', raising=False)
    with Operations.context(MigrationContext.configure(conn)), pytest.raises(RuntimeError):
        migration('0004').upgrade()
    monkeypatch.setenv('PII_MIGRATION_MAINTENANCE', 'true')
    monkeypatch.setattr(settings, 'pii_encryption_keys', {})
    with Operations.context(MigrationContext.configure(conn)), pytest.raises(ValueError):
        migration('0004').upgrade()
    assert conn.scalar(sa.text(f'SELECT login FROM {schema}.staff')) == 'legacy-private-login'


def test_fresh_install_needs_keys_but_not_maintenance(legacy_database, monkeypatch):
    conn, schema = legacy_database
    monkeypatch.delenv('PII_MIGRATION_MAINTENANCE', raising=False)
    for table in ('feedback_notes', 'audit_entries', 'outbox', 'order_attempts', 'feedback', 'orders', 'staff_sessions', 'staff'):
        conn.execute(sa.text(f'DELETE FROM {schema}.{table}'))
    with Operations.context(MigrationContext.configure(conn)):
        migration('0004').upgrade()
    assert conn.scalar(sa.text(f'SELECT count(*) FROM {schema}.users')) == 0
