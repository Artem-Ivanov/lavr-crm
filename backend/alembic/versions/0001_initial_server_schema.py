"""initial server schema"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('categories',
    sa.Column('id', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('normalized_name', sa.String(length=240), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('is_visible', sa.Boolean(), nullable=False),
    sa.Column('show_in_menu', sa.Boolean(), nullable=False),
    sa.Column('version', sa.BigInteger(), nullable=False),
    sa.CheckConstraint('position BETWEEN 0 AND 1000000'),
    sa.CheckConstraint('version BETWEEN 1 AND 9007199254740991'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('normalized_name')
    )
    op.create_table('integration_state',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('catalog_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_order_export_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('state', sa.String(length=30), nullable=False),
    sa.Column('error_code', sa.String(length=80), nullable=True),
    sa.CheckConstraint('id = 1'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('login_throttles',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('failures', sa.Integer(), nullable=False),
    sa.Column('window_start', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('media',
    sa.Column('id', sa.String(length=128), nullable=False),
    sa.Column('filename', sa.String(length=160), nullable=False),
    sa.Column('mime_type', sa.String(length=20), nullable=False),
    sa.Column('alt', sa.String(length=240), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('state', sa.String(length=10), nullable=False),
    sa.CheckConstraint("state = 'ready'"),
    sa.CheckConstraint('size_bytes > 0 AND size_bytes <= 5242880'),
    sa.CheckConstraint('width > 0 AND width <= 4096 AND height > 0 AND height <= 4096'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('filename')
    )
    op.create_index(op.f('ix_media_created_at'), 'media', ['created_at'], unique=False)
    op.create_table('orders',
    sa.Column('id', sa.String(length=128), nullable=False),
    sa.Column('number', sa.String(length=80), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('customer', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('fulfillment', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('items', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('total_kopecks', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('integration_status', sa.String(length=30), nullable=False),
    sa.Column('version', sa.BigInteger(), nullable=False),
    sa.Column('external_document_id', sa.String(length=128), nullable=True),
    sa.CheckConstraint("integration_status IN ('not_connected', 'pending_sync', 'exporting', 'exported', 'error', 'cancelled_before_sync')"),
    sa.CheckConstraint("status IN ('new', 'confirmed', 'ready', 'completed', 'cancelled')"),
    sa.CheckConstraint('total_kopecks >= 0 AND total_kopecks <= 9007199254740991'),
    sa.CheckConstraint('version BETWEEN 1 AND 9007199254740991'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('number')
    )
    op.create_index(op.f('ix_orders_created_at'), 'orders', ['created_at'], unique=False)
    op.create_index(op.f('ix_orders_integration_status'), 'orders', ['integration_status'], unique=False)
    op.create_index(op.f('ix_orders_status'), 'orders', ['status'], unique=False)
    op.create_table('products',
    sa.Column('id', sa.String(length=128), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=False),
    sa.Column('sku', sa.String(length=80), nullable=False),
    sa.Column('brand', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=240), nullable=False),
    sa.Column('price_kopecks', sa.BigInteger(), nullable=False),
    sa.Column('stock', sa.Integer(), nullable=False),
    sa.Column('reserved', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('delivery_days', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('search_normalized', sa.Text(), nullable=False),
    sa.CheckConstraint('delivery_days BETWEEN 0 AND 365'),
    sa.CheckConstraint('price_kopecks BETWEEN 0 AND 9007199254740991'),
    sa.CheckConstraint('reserved BETWEEN 0 AND 2147483647'),
    sa.CheckConstraint('stock BETWEEN 0 AND 2147483647'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('external_id')
    )
    op.create_index(op.f('ix_products_search_normalized'), 'products', ['search_normalized'], unique=False)
    op.create_table('publications',
    sa.Column('id', sa.String(length=128), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('title', sa.String(length=240), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('image_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('target', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('button_text', sa.String(length=80), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.BigInteger(), nullable=False),
    sa.CheckConstraint("kind IN ('offer', 'announcement', 'hot')"),
    sa.CheckConstraint("status IN ('draft', 'published', 'archived')"),
    sa.CheckConstraint('position >= 0 AND position <= 1000000'),
    sa.CheckConstraint('starts_at IS NULL OR ends_at IS NULL OR starts_at < ends_at'),
    sa.CheckConstraint('version BETWEEN 1 AND 9007199254740991'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_publications_status'), 'publications', ['status'], unique=False)
    op.create_table('site_configuration',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('hero', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('menu', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('version', sa.BigInteger(), nullable=False),
    sa.CheckConstraint('id = 1'),
    sa.CheckConstraint('version BETWEEN 1 AND 9007199254740991'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('staff',
    sa.Column('id', sa.String(length=128), nullable=False),
    sa.Column('login', sa.String(length=120), nullable=False),
    sa.Column('name', sa.String(length=240), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('login')
    )
    op.create_table('audit_entries',
    sa.Column('id', sa.String(length=128), nullable=False),
    sa.Column('staff_id', sa.String(length=128), nullable=False),
    sa.Column('action', sa.String(length=128), nullable=False),
    sa.Column('entity_id', sa.String(length=128), nullable=False),
    sa.Column('version', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_entries_staff_id'), 'audit_entries', ['staff_id'], unique=False)
    op.create_table('order_attempts',
    sa.Column('idempotency_key', sa.String(length=36), nullable=False),
    sa.Column('digest', sa.String(length=64), nullable=False),
    sa.Column('order_id', sa.String(length=128), nullable=False),
    sa.Column('receipt', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('idempotency_key'),
    sa.UniqueConstraint('order_id')
    )
    op.create_table('outbox',
    sa.Column('id', sa.String(length=128), nullable=False),
    sa.Column('order_id', sa.String(length=128), nullable=True),
    sa.Column('event_type', sa.String(length=40), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('state', sa.String(length=30), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("state IN ('queued', 'running', 'done', 'error', 'uncertain', 'rejected', 'cancelled')"),
    sa.CheckConstraint('attempts >= 0'),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('order_id', 'event_type')
    )
    op.create_index(op.f('ix_outbox_event_type'), 'outbox', ['event_type'], unique=False)
    op.create_index(op.f('ix_outbox_order_id'), 'outbox', ['order_id'], unique=False)
    op.create_index(op.f('ix_outbox_state'), 'outbox', ['state'], unique=False)
    op.create_table('product_presentations',
    sa.Column('product_id', sa.String(length=128), nullable=False),
    sa.Column('category_id', sa.String(length=128), nullable=False),
    sa.Column('website_title', sa.String(length=240), nullable=True),
    sa.Column('website_description', sa.Text(), nullable=True),
    sa.Column('image_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('version', sa.BigInteger(), nullable=False),
    sa.CheckConstraint('version BETWEEN 1 AND 9007199254740991'),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], ),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('product_id')
    )
    op.create_index(op.f('ix_product_presentations_category_id'), 'product_presentations', ['category_id'], unique=False)
    op.create_table('staff_sessions',
    sa.Column('id', sa.String(length=128), nullable=False),
    sa.Column('staff_id', sa.String(length=128), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_staff_sessions_expires_at'), 'staff_sessions', ['expires_at'], unique=False)
    op.create_index(op.f('ix_staff_sessions_staff_id'), 'staff_sessions', ['staff_id'], unique=False)
    op.create_table('integration_commands',
    sa.Column('scope', sa.String(length=512), nullable=False),
    sa.Column('idempotency_key', sa.String(length=36), nullable=False),
    sa.Column('job_id', sa.String(length=128), nullable=False),
    sa.Column('response_state', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['outbox.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('scope', 'idempotency_key')
    )


def downgrade():
    op.drop_table('integration_commands')
    op.drop_index(op.f('ix_staff_sessions_staff_id'), table_name='staff_sessions')
    op.drop_index(op.f('ix_staff_sessions_expires_at'), table_name='staff_sessions')
    op.drop_table('staff_sessions')
    op.drop_index(op.f('ix_product_presentations_category_id'), table_name='product_presentations')
    op.drop_table('product_presentations')
    op.drop_index(op.f('ix_outbox_state'), table_name='outbox')
    op.drop_index(op.f('ix_outbox_order_id'), table_name='outbox')
    op.drop_index(op.f('ix_outbox_event_type'), table_name='outbox')
    op.drop_table('outbox')
    op.drop_table('order_attempts')
    op.drop_index(op.f('ix_audit_entries_staff_id'), table_name='audit_entries')
    op.drop_table('audit_entries')
    op.drop_table('staff')
    op.drop_table('site_configuration')
    op.drop_index(op.f('ix_publications_status'), table_name='publications')
    op.drop_table('publications')
    op.drop_index(op.f('ix_products_search_normalized'), table_name='products')
    op.drop_table('products')
    op.drop_index(op.f('ix_orders_status'), table_name='orders')
    op.drop_index(op.f('ix_orders_integration_status'), table_name='orders')
    op.drop_index(op.f('ix_orders_created_at'), table_name='orders')
    op.drop_table('orders')
    op.drop_index(op.f('ix_media_created_at'), table_name='media')
    op.drop_table('media')
    op.drop_table('login_throttles')
    op.drop_table('integration_state')
    op.drop_table('categories')
