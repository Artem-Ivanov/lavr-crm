"""Physical stock precision is independent from whole-piece reservations and orders."""
from decimal import Decimal
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.catalog import Product
from test_orders import order_product, submit
from test_pii_migration import migration


def test_fractional_piece_stock_preserves_whole_orders_and_reservations(client, db, order_product):
    order_product.stock = Decimal('9.785')
    db.commit()
    assert client.get('/api/catalog/' + order_product.id).json()['product']['stock'] == 9.785
    assert submit(client, order_product, quantity=0.5).status_code == 422
    response = submit(client, order_product, quantity=9)
    assert response.status_code == 201
    assert response.json()['totalKopecks'] == 112500
    db.refresh(order_product)
    assert order_product.stock == Decimal('9.785') and order_product.reserved == 9
    assert client.get('/api/catalog/' + order_product.id).json()['product']['stock'] == 0.785
    unavailable = submit(client, order_product, quantity=1)
    assert unavailable.status_code == 409 and unavailable.json()['code'] == 'OUT_OF_STOCK'
    with pytest.raises(sa.exc.IntegrityError), db.begin_nested():
        db.execute(sa.update(Product).where(Product.id == order_product.id).values(reserved=Decimal('9.5')))


def test_piece_stock_migration_preserves_values_and_refuses_lossy_downgrade(db_engine):
    schema = 'piece_stock_' + uuid4().hex
    with db_engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA {schema}')
    try:
        with db_engine.begin() as conn:
            conn.exec_driver_sql(f'SET LOCAL search_path TO {schema}')
            conn.exec_driver_sql('CREATE TABLE products (id text PRIMARY KEY, stock integer NOT NULL CHECK(stock BETWEEN 0 AND 2147483647), reserved integer NOT NULL CHECK(reserved BETWEEN 0 AND 2147483647))')
            conn.exec_driver_sql("INSERT INTO products VALUES ('piece', 5, 2)")
            with Operations.context(MigrationContext.configure(conn)):
                migration('0007').upgrade()
                before = conn.execute(sa.text('SELECT * FROM products')).one()
                migration('0009').upgrade()
                assert conn.execute(sa.text('SELECT * FROM products')).one() == before
            conn.exec_driver_sql("UPDATE products SET stock=5.125 WHERE id='piece'")
            with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
                conn.exec_driver_sql("UPDATE products SET reserved=2.125 WHERE id='piece'")
            with pytest.raises(ValueError, match='fractional'), conn.begin_nested(), Operations.context(MigrationContext.configure(conn)):
                migration('0009').downgrade()
            assert conn.execute(sa.text('SELECT stock,reserved FROM products')).one() == (Decimal('5.125'), Decimal('2'))
            conn.exec_driver_sql("UPDATE products SET stock=5 WHERE id='piece'")
            with Operations.context(MigrationContext.configure(conn)):
                migration('0009').downgrade()
            assert conn.execute(sa.text('SELECT * FROM products')).one() == before
            with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
                conn.exec_driver_sql("UPDATE products SET stock=5.125 WHERE id='piece'")
    finally:
        with db_engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
