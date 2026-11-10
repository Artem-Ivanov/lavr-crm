"""Run the forward migration on historical normal-unit stock in real PostgreSQL."""
from decimal import Decimal
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from test_pii_migration import migration


def test_exact_quantity_migration_preserves_old_units_snapshots_and_bounds(db_engine):
    schema = 'quantity_migration_' + uuid4().hex
    with db_engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA {schema}')
    try:
        with db_engine.begin() as conn:
            conn.exec_driver_sql(f'SET LOCAL search_path TO {schema}')
            conn.exec_driver_sql('CREATE TABLE products (id text PRIMARY KEY, stock integer NOT NULL CHECK(stock BETWEEN 0 AND 2147483647), reserved integer NOT NULL CHECK(reserved BETWEEN 0 AND 2147483647))')
            conn.exec_driver_sql("INSERT INTO products VALUES ('old', 5, 2), ('max', 2147483647, 0)")
            conn.exec_driver_sql('CREATE TABLE orders (items jsonb, receipt jsonb, digest text)')
            conn.exec_driver_sql('''INSERT INTO orders VALUES ('[{"productId":"old","quantity":2}]', '{"totalKopecks":202}', 'legacy-unchanged')''')
            snapshot = conn.execute(sa.text('SELECT * FROM orders')).one()
            with Operations.context(MigrationContext.configure(conn)):
                migration('0007').upgrade()
            row = conn.execute(sa.text("SELECT stock, reserved, unit, stock_observed_at FROM products WHERE id='old'")).one()
            assert row == (Decimal('5.000'), Decimal('2.000'), 'шт', None)
            assert row.stock - row.reserved == 3
            assert conn.scalar(sa.text("SELECT stock FROM products WHERE id='max'")) == 2147483647
            assert conn.execute(sa.text('SELECT * FROM orders')).one() == snapshot
            columns = {column['name']: column for column in sa.inspect(conn).get_columns('products', schema=schema)}
            for column in ('stock', 'reserved'):
                assert (columns[column]['type'].precision, columns[column]['type'].scale) == (13, 3)
            for statement in ["UPDATE products SET stock=5.125 WHERE id='old'", "UPDATE products SET stock=2147483647.001 WHERE id='max'", "UPDATE products SET unit='bad' WHERE id='old'"]:
                with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
                    conn.exec_driver_sql(statement)
            conn.exec_driver_sql("UPDATE products SET unit='кг', stock=5.125, reserved=2.001 WHERE id='old'")
            assert conn.execute(sa.text("SELECT stock, reserved FROM products WHERE id='old'")).one() == (Decimal('5.125'), Decimal('2.001'))
            with pytest.raises(ValueError, match='fractional'), conn.begin_nested(), Operations.context(MigrationContext.configure(conn)):
                migration('0007').downgrade()
            conn.exec_driver_sql("UPDATE products SET stock=5, reserved=2 WHERE id='old'")
            for unit in ('кг', 'л', 'м', None):
                conn.execute(sa.text("UPDATE products SET unit=:unit WHERE id='old'"), {'unit': unit})
                with pytest.raises(ValueError, match='unit'), conn.begin_nested(), Operations.context(MigrationContext.configure(conn)):
                    migration('0007').downgrade()
                assert conn.execute(sa.text("SELECT stock, reserved, unit FROM products WHERE id='old'")).one() == (Decimal('5.000'), Decimal('2.000'), unit)
                columns = {column['name']: column for column in sa.inspect(conn).get_columns('products', schema=schema)}
                assert {'unit', 'stock_observed_at'} <= columns.keys()
                assert (columns['stock']['type'].precision, columns['stock']['type'].scale) == (13, 3)
                assert conn.execute(sa.text('SELECT * FROM orders')).one() == snapshot
            conn.exec_driver_sql("UPDATE products SET unit='шт' WHERE id='old'")
            with Operations.context(MigrationContext.configure(conn)):
                migration('0007').downgrade()
            assert conn.execute(sa.text("SELECT stock, reserved FROM products WHERE id='old'")).one() == (5, 2)
            assert conn.execute(sa.text('SELECT * FROM orders')).one() == snapshot
    finally:
        with db_engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')


def test_quantity_downgrade_blocks_unit_writes_while_checking_legacy_representability(db_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    schema = 'quantity_downgrade_lock_' + uuid4().hex
    started, finished = Event(), Event()
    with db_engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA {schema}')
        conn.exec_driver_sql(f'SET LOCAL search_path TO {schema}')
        conn.exec_driver_sql('CREATE TABLE products (id text PRIMARY KEY, stock integer NOT NULL, reserved integer NOT NULL)')
        conn.exec_driver_sql("INSERT INTO products VALUES ('piece', 1, 0)")
        with Operations.context(MigrationContext.configure(conn)):
            migration('0007').upgrade()
    def write_unit():
        with db_engine.begin() as writer:
            started.set()
            writer.exec_driver_sql(f"UPDATE {schema}.products SET unit='кг' WHERE id='piece'")
        finished.set()
    future = None
    try:
        with ThreadPoolExecutor(max_workers=1) as pool, db_engine.connect() as conn:
            transaction = conn.begin()
            conn.exec_driver_sql(f'SET LOCAL search_path TO {schema}')
            def after_guard(connection, cursor, statement, parameters, context, executemany):
                nonlocal future
                if 'stock <> trunc(stock)' in statement:
                    future = pool.submit(write_unit)
                    assert started.wait(5)
                    assert not finished.wait(.2), 'A unit write slipped between the downgrade guard and DDL'
            sa.event.listen(conn, 'after_cursor_execute', after_guard)
            try:
                with Operations.context(MigrationContext.configure(conn)):
                    migration('0007').downgrade()
            finally:
                transaction.rollback()
            future.result(timeout=5)
        with db_engine.connect() as conn:
            assert conn.exec_driver_sql(f'SELECT unit, stock FROM {schema}.products').one() == ('кг', Decimal('1.000'))
    finally:
        with db_engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
