"""Global integer feedback numbers; historical receipt/delivery snapshots stay intact."""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None

MAX_SAFE_INTEGER = 9007199254740991


def upgrade():
    conn = op.get_bind()
    schema = conn.scalar(sa.text('SELECT current_schema()'))
    quoted_schema = conn.dialect.identifier_preparer.quote_schema(schema)
    table = f'{quoted_schema}.feedback'
    sequence = f'{quoted_schema}.feedback_number_seq'
    conn.exec_driver_sql(f'LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE')
    conn.exec_driver_sql(f'CREATE SEQUENCE {sequence} AS BIGINT MINVALUE 1 MAXVALUE {MAX_SAFE_INTEGER} START 1')
    # A separate column avoids unique-index collisions even for old numeric strings.
    op.add_column('feedback', sa.Column('_number_backfill', sa.BigInteger()), schema=schema)
    conn.exec_driver_sql(f'WITH numbered AS (SELECT id, row_number() OVER (ORDER BY created_at, id) AS n FROM {table}) UPDATE {table} AS f SET _number_backfill = numbered.n FROM numbered WHERE f.id = numbered.id')
    op.alter_column('feedback', 'number', type_=sa.BigInteger(), postgresql_using='_number_backfill', schema=schema)
    op.drop_column('feedback', '_number_backfill', schema=schema)
    op.create_check_constraint('feedback_number_safe_integer', 'feedback', f'number BETWEEN 1 AND {MAX_SAFE_INTEGER}', schema=schema)
    # SQLAlchemy's literal renderer also handles unusual schema names safely.
    sequence_literal = sa.literal(sequence).compile(dialect=conn.dialect, compile_kwargs={'literal_binds': True})
    conn.exec_driver_sql(f'ALTER SEQUENCE {sequence} OWNED BY {table}.number')
    conn.exec_driver_sql(f'ALTER TABLE {table} ALTER COLUMN number SET DEFAULT nextval({sequence_literal}::regclass)')
    conn.exec_driver_sql(f'SELECT setval({sequence_literal}::regclass, COALESCE(MAX(number), 1), COUNT(*) > 0) FROM {table}')


def downgrade():
    # Preserve canonical numeric values as text; pre-migration labels cannot be recovered.
    conn = op.get_bind()
    schema = conn.scalar(sa.text('SELECT current_schema()'))
    quoted_schema = conn.dialect.identifier_preparer.quote_schema(schema)
    op.alter_column('feedback', 'number', server_default=None, schema=schema)
    op.drop_constraint('feedback_number_safe_integer', 'feedback', type_='check', schema=schema)
    op.alter_column('feedback', 'number', type_=sa.String(80), postgresql_using='number::text', schema=schema)
    conn.exec_driver_sql(f'DROP SEQUENCE {quoted_schema}.feedback_number_seq')
