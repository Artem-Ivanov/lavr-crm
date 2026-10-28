"""Persist Telegram destination cooldowns."""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'telegram_delivery_state',
        sa.Column('destination_key', sa.String(length=64), nullable=False),
        sa.Column('blocked_until', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('destination_key'),
    )


def downgrade():
    op.drop_table('telegram_delivery_state')
