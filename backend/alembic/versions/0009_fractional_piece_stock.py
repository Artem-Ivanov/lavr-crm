"""Preserve physical stock thousandths while piece reservations remain whole."""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    schema = op.get_bind().scalar(sa.text('SELECT current_schema()'))
    op.drop_constraint('product_piece_quantities', 'products', type_='check', schema=schema)
    op.create_check_constraint('product_piece_quantities', 'products',
                               "unit IS DISTINCT FROM 'шт' OR reserved = trunc(reserved)", schema=schema)


def downgrade():
    conn = op.get_bind()
    schema = conn.scalar(sa.text('SELECT current_schema()'))
    quoted = conn.dialect.identifier_preparer.quote_schema(schema)
    conn.execute(sa.text(f'LOCK TABLE {quoted}.products IN ACCESS EXCLUSIVE MODE'))
    if conn.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {quoted}.products WHERE unit='шт' AND stock <> trunc(stock))")):
        raise ValueError('Cannot downgrade fractional piece stock without losing precision.')
    op.drop_constraint('product_piece_quantities', 'products', type_='check', schema=schema)
    op.create_check_constraint('product_piece_quantities', 'products',
                               "unit IS DISTINCT FROM 'шт' OR (stock = trunc(stock) AND reserved = trunc(reserved))", schema=schema)
