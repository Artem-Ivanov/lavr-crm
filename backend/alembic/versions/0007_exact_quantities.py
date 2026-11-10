"""Exact sale-unit quantities without rescaling historical stock or snapshots."""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    schema = op.get_bind().scalar(sa.text('SELECT current_schema()'))
    for column in ('stock', 'reserved'):
        op.alter_column('products', column, type_=sa.Numeric(13, 3), postgresql_using=f'{column}::numeric(13,3)', schema=schema)
    op.add_column('products', sa.Column('unit', sa.String(8), nullable=True, server_default='шт'), schema=schema)
    op.add_column('products', sa.Column('stock_observed_at', sa.DateTime(timezone=True), nullable=True), schema=schema)
    op.create_check_constraint('product_unit', 'products', "unit IS NULL OR unit IN ('шт', 'кг', 'л', 'м')", schema=schema)
    op.create_check_constraint('product_piece_quantities', 'products', "unit IS DISTINCT FROM 'шт' OR (stock = trunc(stock) AND reserved = trunc(reserved))", schema=schema)


def downgrade():
    conn = op.get_bind()
    schema = conn.scalar(sa.text('SELECT current_schema()'))
    quoted = conn.dialect.identifier_preparer.quote_schema(schema)
    # Keep validation and DDL atomic with respect to concurrent stock/unit writes.
    conn.execute(sa.text(f'LOCK TABLE {quoted}.products IN ACCESS EXCLUSIVE MODE'))
    # PostgreSQL's integer cast rounds fractions, so refuse a lossy downgrade.
    if conn.scalar(sa.text(f'SELECT EXISTS (SELECT 1 FROM {quoted}.products WHERE stock <> trunc(stock) OR reserved <> trunc(reserved))')):
        raise ValueError('Cannot downgrade fractional product quantities to integers.')
    if conn.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {quoted}.products WHERE unit IS DISTINCT FROM 'шт')")):
        raise ValueError('Cannot downgrade nonpiece or unconfirmed product units to legacy pieces.')
    op.drop_constraint('product_piece_quantities', 'products', type_='check', schema=schema)
    op.drop_constraint('product_unit', 'products', type_='check', schema=schema)
    op.drop_column('products', 'stock_observed_at', schema=schema)
    op.drop_column('products', 'unit', schema=schema)
    for column in ('stock', 'reserved'):
        op.alter_column('products', column, type_=sa.Integer(), postgresql_using=f'{column}::integer', schema=schema)
