"""Durable purpose-bound account challenges and hourly abuse counters."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('user_challenges',
        sa.Column('flow_id', sa.String(128), primary_key=True),
        sa.Column('purpose', sa.String(30), nullable=False),
        sa.Column('continuation_hash', sa.String(64), nullable=False),
        sa.Column('email_lookup', sa.String(64), nullable=False),
        sa.Column('code_hmac', sa.String(64), nullable=True),
        sa.Column('generation', sa.Integer(), nullable=False),
        sa.Column('encrypted_payload', JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_sent_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("purpose IN ('signup','password_reset','profile_completion')", name='user_challenges_purpose_check'),
        sa.CheckConstraint('attempts >= 0 AND generation >= 1', name='user_challenges_counters_check'))
    op.create_index('ix_user_challenges_email_lookup', 'user_challenges', ['email_lookup'])
    op.create_index('ix_user_challenges_created_at', 'user_challenges', ['created_at'])
    op.create_table('challenge_throttles',
        sa.Column('key', sa.String(64), primary_key=True),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.Column('window_start', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_challenge_throttles_window_start', 'challenge_throttles', ['window_start'])


def downgrade():
    op.drop_table('challenge_throttles')
    op.drop_table('user_challenges')
