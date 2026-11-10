from uuid import uuid4
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_pii_migration import migration


def test_catalog_import_migration_preserves_legacy_products_and_private_pdf(db_engine):
    schema='catalog_migration_'+uuid4().hex
    with db_engine.begin() as conn:
        conn.exec_driver_sql('CREATE SCHEMA '+schema)
    try:
        with db_engine.begin() as conn:
            conn.exec_driver_sql('SET LOCAL search_path TO '+schema)
            conn.exec_driver_sql('CREATE TABLE products (id varchar(128) PRIMARY KEY, stock numeric(13,3), reserved numeric(13,3))')
            conn.exec_driver_sql('CREATE TABLE categories (id varchar(128) PRIMARY KEY)')
            conn.exec_driver_sql("INSERT INTO products VALUES ('legacy',9.785,1.001)")
            with Operations.context(MigrationContext.configure(conn)):
                migration('0008').upgrade()
            assert conn.execute(sa.text('SELECT stock,reserved,accounting_source,accounting_version FROM products')).one() == (sa.Numeric().python_type('9.785'),sa.Numeric().python_type('1.001'),None,1)
            columns={column['name']:column for column in sa.inspect(conn).get_columns('catalog_imports',schema=schema)}
            assert isinstance(columns['raw_pdf']['type'],sa.LargeBinary)
            tables=sa.inspect(conn).get_table_names(schema=schema)
            assert all(table in tables for table in ['catalog_imports','catalog_import_rows','catalog_source_mappings','catalog_accounting_owners'])
            with Operations.context(MigrationContext.configure(conn)):
                migration('0008').downgrade()
            assert conn.execute(sa.text('SELECT stock,reserved FROM products')).one() == (sa.Numeric().python_type('9.785'),sa.Numeric().python_type('1.001'))
    finally:
        with db_engine.begin() as conn:
            conn.exec_driver_sql('DROP SCHEMA '+schema+' CASCADE')
