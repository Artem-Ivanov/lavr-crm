"""Persisted catalog sources, review queue and immutable apply reports."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    schema = op.get_bind().scalar(sa.text('SELECT current_schema()'))
    op.add_column('products', sa.Column('accounting_source', sa.String(160), nullable=True), schema=schema)
    op.add_column('products', sa.Column('accounting_version', sa.BigInteger(), nullable=False, server_default='1'), schema=schema)
    op.create_check_constraint('products_accounting_version', 'products', 'accounting_version BETWEEN 1 AND 9007199254740991', schema=schema)
    op.create_table('catalog_imports',
        sa.Column('id', sa.String(128), primary_key=True),
        sa.Column('namespace', sa.String(160), nullable=False),
        sa.Column('mode', sa.String(16), nullable=False),
        sa.Column('author_id', sa.String(128), nullable=False),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('version', sa.BigInteger(), nullable=False),
        sa.Column('file_name', sa.String(240), nullable=False),
        sa.Column('file_hash', sa.String(64), nullable=False),
        sa.Column('raw_pdf', sa.LargeBinary(), nullable=True),
        sa.Column('upload_key', sa.String(36), nullable=True, unique=True),
        sa.Column('upload_digest', sa.String(64), nullable=True),
        sa.Column('apply_key', sa.String(36), nullable=True, unique=True),
        sa.Column('apply_digest', sa.String(64), nullable=True),
        sa.Column('page_count', sa.Integer(), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('summary', postgresql.JSONB(), nullable=False),
        sa.Column('error', postgresql.JSONB(), nullable=True),
        sa.Column('report', postgresql.JSONB(), nullable=True),
        sa.Column('lease_token', sa.String(36), nullable=True),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rows_expired', sa.Boolean(), nullable=False),
        sa.CheckConstraint('version BETWEEN 1 AND 9007199254740991'), schema=schema)
    op.create_index('ix_catalog_imports_created_at', 'catalog_imports', ['created_at'], schema=schema)
    op.create_index('ix_catalog_imports_status', 'catalog_imports', ['status'], schema=schema)
    op.create_table('catalog_import_rows',
        sa.Column('id', sa.String(128), primary_key=True),
        sa.Column('import_id', sa.String(128), sa.ForeignKey('catalog_imports.id', ondelete='CASCADE'), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('source_data', postgresql.JSONB(), nullable=False),
        sa.Column('identity', sa.String(300), nullable=False),
        sa.Column('duplicate', sa.Boolean(), nullable=False),
        sa.Column('selected', sa.Boolean(), nullable=False),
        sa.Column('unit', sa.String(8), nullable=True),
        sa.Column('category_id', sa.String(128), sa.ForeignKey('categories.id'), nullable=True),
        sa.Column('resolution', sa.String(16), nullable=True),
        sa.Column('target_product_id', sa.String(128), sa.ForeignKey('products.id'), nullable=True),
        sa.Column('acknowledge_standalone', sa.Boolean(), nullable=False),
        sa.Column('baseline', postgresql.JSONB(), nullable=True),
        sa.Column('mapping_product_id', sa.String(128), nullable=True),
        sa.Column('issues', postgresql.JSONB(), nullable=False),
        sa.Column('blocking', sa.Boolean(), nullable=False),
        sa.Column('action', sa.String(16), nullable=False),
        sa.Column('search_text', sa.Text(), nullable=False),
        sa.UniqueConstraint('import_id', 'position'), schema=schema)
    op.create_index('ix_catalog_import_rows_import_id', 'catalog_import_rows', ['import_id'], schema=schema)
    op.create_table('catalog_source_mappings',
        sa.Column('namespace', sa.String(160), primary_key=True),
        sa.Column('identity', sa.String(300), primary_key=True),
        sa.Column('product_id', sa.String(128), sa.ForeignKey('products.id'), nullable=False),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=False), schema=schema)
    op.create_index('ix_catalog_source_mappings_product_id', 'catalog_source_mappings', ['product_id'], schema=schema)
    op.create_table('catalog_accounting_owners',
        sa.Column('stock_scope', sa.String(80), primary_key=True),
        sa.Column('namespace', sa.String(160), nullable=True), schema=schema)


def downgrade():
    schema = op.get_bind().scalar(sa.text('SELECT current_schema()'))
    for table in ('catalog_accounting_owners', 'catalog_source_mappings', 'catalog_import_rows', 'catalog_imports'):
        op.drop_table(table, schema=schema)
    op.drop_constraint('products_accounting_version', 'products', type_='check', schema=schema)
    op.drop_column('products', 'accounting_version', schema=schema)
    op.drop_column('products', 'accounting_source', schema=schema)
