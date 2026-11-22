"""Add onboarding and deposit records. No changes to existing users, accounts or balances."""
from alembic import op
import sqlalchemy as sa
revision = '0010_onboarding'
down_revision = '0009_two_factor_login'
branch_labels = depends_on = None

def upgrade():
    op.create_table('onboarding_applications',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id'), unique=True),
        sa.Column('phone_digest', sa.String(64), nullable=False, unique=True),
        sa.Column('identity_digest', sa.String(64), unique=True),
        sa.Column('session_digest', sa.String(64)),
        sa.Column('session_expires_at', sa.DateTime(timezone=True)),
        sa.Column('phone_verified', sa.Boolean(), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('encrypted_data', sa.LargeBinary()),
        sa.Column('reason', sa.String(40)),
        sa.Column('reviewer_id', sa.Uuid(), sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('draft','pending_review','needs_information','rejected','active','cancelled','expired')", name='ck_onboarding_status'))
    op.create_table('signup_challenges',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('application_id', sa.Uuid(), sa.ForeignKey('onboarding_applications.id', ondelete='CASCADE'), nullable=False),
        sa.Column('otp_hash', sa.String(512), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('used', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('attempts BETWEEN 0 AND 5', name='ck_signup_attempts'))
    op.create_index('ix_signup_challenges_application_id', 'signup_challenges', ['application_id'])
    op.create_table('demo_deposits',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('transaction_id', sa.Uuid(), sa.ForeignKey('demo_transactions.id'), nullable=False, unique=True),
        sa.Column('actor_id', sa.Uuid(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('note', sa.String(200), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))

def downgrade():
    op.drop_table('demo_deposits')
    op.drop_table('signup_challenges')
    op.drop_table('onboarding_applications')
