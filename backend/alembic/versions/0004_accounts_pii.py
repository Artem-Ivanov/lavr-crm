"""Preserve principals and guest receipts while encrypting every persisted PII copy."""
import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from app.crypto import lookup_digest, open_json, open_text, seal_json, seal_text, validate_configuration

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def _table(name):
    return sa.Table(name, sa.MetaData(), autoload_with=op.get_bind())


def _encrypt(value, *, table, record_id, field, text=False):
    context = dict(table=table, record_id=record_id, field=field)
    encrypted = (seal_text if text else seal_json)(value, **context)
    if (open_text if text else open_json)(encrypted, **context) != value:
        raise RuntimeError('PII backfill verification failed')
    return encrypted


def _owner_columns(table):
    op.add_column(table, sa.Column('customer_id', sa.String(128), nullable=True))
    op.create_foreign_key(f'{table}_customer_id_fkey', table, 'users', ['customer_id'], ['id'])
    for column in ('customer_id', 'phone_lookup', 'email_lookup'):
        if column != 'customer_id':
            op.add_column(table, sa.Column(column, sa.String(64), nullable=True))
        op.create_index(f'ix_{table}_{column}', table, [column])


def _attempt_columns(table):
    op.add_column(table, sa.Column('actor', sa.String(128), nullable=False, server_default='guest'))
    op.add_column(table, sa.Column('digest_version', sa.Integer(), nullable=False, server_default='1'))
    op.alter_column(table, 'actor', server_default=None)
    op.alter_column(table, 'digest_version', server_default=None)


def upgrade():
    validate_configuration()
    conn = op.get_bind()
    existing = any(conn.scalar(sa.select(sa.literal(True)).select_from(_table(name)).limit(1)) for name in ('staff', 'orders', 'feedback', 'outbox'))
    if existing and os.environ.get('PII_MIGRATION_MAINTENANCE') != 'true':
        raise RuntimeError('Stop writers/workers, verify a protected backup, then set PII_MIGRATION_MAINTENANCE=true')
    feedback = _table('feedback')
    for row in conn.execute(sa.select(feedback.c.id, feedback.c.resolution).where(feedback.c.status == 'closed')).mappings():
        if not row['resolution'].strip():
            raise RuntimeError('Closed legacy feedback requires a nonempty resolution before migration')

    op.rename_table('staff', 'users')
    op.rename_table('staff_sessions', 'user_sessions')
    # PostgreSQL preserves every referencing FK through table and column renames.
    conn.execute(sa.text('DELETE FROM user_sessions'))
    op.alter_column('user_sessions', 'staff_id', new_column_name='user_id', existing_type=sa.String(128))
    op.add_column('user_sessions', sa.Column('auth_version', sa.Integer(), nullable=False, server_default='1'))
    op.alter_column('user_sessions', 'auth_version', server_default=None)
    conn.execute(sa.text('ALTER INDEX ix_staff_sessions_staff_id RENAME TO ix_user_sessions_user_id'))
    conn.execute(sa.text('ALTER INDEX ix_staff_sessions_expires_at RENAME TO ix_user_sessions_expires_at'))
    for unique in sa.inspect(conn).get_unique_constraints('users'):
        if unique['column_names'] == ['login']:
            op.drop_constraint(unique['name'], 'users', type_='unique')
    op.alter_column('users', 'login', new_column_name='login_ciphertext', type_=sa.Text(), existing_type=sa.String(120), nullable=True)
    op.alter_column('users', 'name', new_column_name='name_ciphertext', type_=sa.Text(), existing_type=sa.String(240))
    op.add_column('users', sa.Column('login_lookup', sa.String(64), nullable=True))
    op.add_column('users', sa.Column('email_lookup', sa.String(64), nullable=True))
    op.add_column('users', sa.Column('phone_lookup', sa.String(64), nullable=True))
    op.create_unique_constraint('users_login_lookup_key', 'users', ['login_lookup'])
    op.create_unique_constraint('users_email_lookup_key', 'users', ['email_lookup'])
    op.create_index('ix_users_phone_lookup', 'users', ['phone_lookup'])
    op.add_column('users', sa.Column('auth_version', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('users', sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.alter_column('users', 'auth_version', server_default=None)
    op.alter_column('users', 'created_at', server_default=None)
    op.add_column('users', sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('profile_ciphertext', JSONB(), nullable=True))
    conn.execute(sa.text("UPDATE users SET role='operator' WHERE role='manager'"))
    op.create_check_constraint('users_role_check', 'users', "role IN ('partner', 'operator', 'admin')")
    op.add_column('audit_entries', sa.Column('details', JSONB(), nullable=True, server_default=sa.text("'{}'::jsonb")))
    op.alter_column('audit_entries', 'details', server_default=None)
    users = _table('users')
    for row in conn.execute(sa.select(users)).mappings():
        conn.execute(users.update().where(users.c.id == row['id']).values(
            login_ciphertext=_encrypt(row['login_ciphertext'], table='users', record_id=row['id'], field='login', text=True),
            name_ciphertext=_encrypt(row['name_ciphertext'], table='users', record_id=row['id'], field='name', text=True),
            login_lookup=lookup_digest('legacy_login', row['login_ciphertext'])))

    _owner_columns('orders')
    _owner_columns('feedback')
    _attempt_columns('order_attempts')
    _attempt_columns('feedback')
    for check in sa.inspect(conn).get_check_constraints('feedback'):
        if 'resolution' in check['sqltext']:
            op.drop_constraint(check['name'], 'feedback', type_='check')
    op.create_check_constraint('feedback_closed_resolution_check', 'feedback', "status != 'closed' OR resolution <> ''")
    for field, length in (('name', 100), ('phone', 30), ('email', 254)):
        op.alter_column('feedback', field, type_=sa.Text(), existing_type=sa.String(length))

    orders = _table('orders')
    for row in conn.execute(sa.select(orders)).mappings():
        customer = row['customer']
        conn.execute(orders.update().where(orders.c.id == row['id']).values(
            customer=_encrypt(customer, table='orders', record_id=row['id'], field='customer'),
            fulfillment=_encrypt(row['fulfillment'], table='orders', record_id=row['id'], field='fulfillment'),
            phone_lookup=lookup_digest('phone', customer['phone']) if customer.get('phone') else None,
            email_lookup=lookup_digest('email', customer['email'].strip().lower()) if customer.get('email') else None))
    attempts = _table('order_attempts')
    for row in conn.execute(sa.select(attempts)).mappings():
        conn.execute(attempts.update().where(attempts.c.idempotency_key == row['idempotency_key']).values(
            receipt=_encrypt(row['receipt'], table='order_attempts', record_id=row['idempotency_key'], field='receipt')))
    feedback = _table('feedback')
    for row in conn.execute(sa.select(feedback)).mappings():
        values = {field: _encrypt(row[field], table='feedback', record_id=row['id'], field=field, text=True)
                  for field in ('name', 'phone', 'email', 'message', 'resolution')}
        values['messenger'] = _encrypt(row['messenger'], table='feedback', record_id=row['id'], field='messenger')
        if row['conversion_response'] is not None:
            values['conversion_response'] = _encrypt(row['conversion_response'], table='feedback', record_id=row['id'], field='conversion_response')
        values['phone_lookup'] = lookup_digest('phone', row['phone']) if row['phone'] else None
        values['email_lookup'] = lookup_digest('email', row['email'].strip().lower()) if row['email'] else None
        conn.execute(feedback.update().where(feedback.c.id == row['id']).values(**values))
    notes = _table('feedback_notes')
    for row in conn.execute(sa.select(notes)).mappings():
        conn.execute(notes.update().where(notes.c.id == row['id']).values(text=_encrypt(row['text'], table='feedback_notes', record_id=row['id'], field='text', text=True)))
    outbox = _table('outbox')
    for row in conn.execute(sa.select(outbox)).mappings():
        conn.execute(outbox.update().where(outbox.c.id == row['id']).values(payload=_encrypt(row['payload'], table='outbox', record_id=row['id'], field='payload')))


def downgrade():
    raise RuntimeError('PII migration has no automatic plaintext downgrade. Restore the verified protected backup before reopening writes; otherwise deploy a compatible fix.')
