"""Exercise the real numbering migration in isolated PostgreSQL schemas."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from test_pii_migration import migration
from app.crypto import open_json, seal_json


@pytest.mark.parametrize('populated', [False, True])
def test_feedback_number_migration_orders_rows_preserves_snapshots_and_next_number(db_engine, populated):
    schema = 'number_migration_' + uuid4().hex
    with db_engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA {schema}')
    try:
        with db_engine.begin() as conn:
            conn.exec_driver_sql(f'SET LOCAL search_path TO {schema}')
            # Only the columns touched by 0006; snapshot values are deliberately opaque.
            conn.exec_driver_sql('CREATE TABLE feedback (id text PRIMARY KEY, number varchar(80) UNIQUE NOT NULL, created_at timestamptz NOT NULL, receipt jsonb NOT NULL, conversion_response jsonb)')
            conn.exec_driver_sql('CREATE TABLE outbox (id text PRIMARY KEY, payload jsonb NOT NULL)')
            if populated:
                for identifier, created in [('b', '2026-01-02'), ('c', '2026-01-01'), ('a', '2026-01-01')]:
                    snapshot = seal_json({'number': 'F-original', 'history': [{'text': 'Original conversion'}]}, table='feedback', record_id=identifier, field='conversion_response')
                    conn.execute(sa.text("INSERT INTO feedback VALUES (:id, :number, :created, CAST(:receipt AS jsonb), CAST(:conversion AS jsonb))"), {'id': identifier, 'number': 'F-' + identifier, 'created': created, 'receipt': '{"number":"F-original"}', 'conversion': json.dumps(snapshot)})
                conn.exec_driver_sql("INSERT INTO outbox VALUES ('a', '{\"relay_request\":{\"text\":\"old immutable message\"}}')")
            before = list(conn.execute(sa.text('SELECT id, receipt, conversion_response FROM feedback ORDER BY id')))
            outbox = list(conn.execute(sa.text('SELECT * FROM outbox')))
            with Operations.context(MigrationContext.configure(conn)):
                migration('0006').upgrade()
            assert list(conn.execute(sa.text('SELECT id, receipt, conversion_response FROM feedback ORDER BY id'))) == before
            for identifier, _, snapshot in before:
                assert open_json(snapshot, table='feedback', record_id=identifier, field='conversion_response')['number'] == 'F-original'
            assert list(conn.execute(sa.text('SELECT * FROM outbox'))) == outbox
            assert list(conn.execute(sa.text('SELECT id, number FROM feedback ORDER BY number'))) == ([('a', 1), ('c', 2), ('b', 3)] if populated else [])
            assert sa.inspect(conn).get_columns('feedback')[1]['type'].__class__ is sa.BIGINT
        # Real independent database transactions prove the sequence default allocates safely.
        def allocate(index):
            with db_engine.begin() as conn:
                conn.exec_driver_sql(f'SET LOCAL search_path TO {schema}')
                return conn.scalar(sa.text("INSERT INTO feedback (id, created_at, receipt) VALUES (:id, :created, '{}') RETURNING number"), {'id': str(index), 'created': datetime.now(timezone.utc)})
        with ThreadPoolExecutor(max_workers=5) as workers:
            assert sorted(workers.map(allocate, range(5))) == list(range(4 if populated else 1, 9 if populated else 6))
        with db_engine.begin() as conn:
            conn.exec_driver_sql(f'SET LOCAL search_path TO {schema}')
            snapshots = list(conn.execute(sa.text('SELECT id, receipt, conversion_response FROM feedback ORDER BY id')))
            with Operations.context(MigrationContext.configure(conn)):
                migration('0006').downgrade()
            assert list(conn.execute(sa.text('SELECT id, receipt, conversion_response FROM feedback ORDER BY id'))) == snapshots
            assert all(isinstance(number, str) for number in conn.scalars(sa.text('SELECT number FROM feedback')))
    finally:
        with db_engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
