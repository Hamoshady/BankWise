"""accounts and identity metadata"""
from alembic import op
import sqlalchemy as sa


revision = '0001_accounts_identity'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('password_hash', sa.String(length=512), nullable=False),
    sa.Column('active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("password_hash LIKE '$argon2id$%'", name='ck_users_password_hash'),
    sa.CheckConstraint("username ~ '^[a-z0-9][a-z0-9_.-]{2,63}$'", name='ck_users_username'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username')
    )
    op.create_table('identity_requests',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('idempotency_key', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('decision', sa.String(length=16), nullable=True),
    sa.Column('error_code', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("(status = 'processing' AND completed_at IS NULL AND decision IS NULL AND error_code IS NULL) OR (status = 'completed' AND completed_at IS NOT NULL AND decision IS NOT NULL AND error_code IS NULL) OR (status IN ('failed', 'interrupted') AND completed_at IS NOT NULL AND decision IS NULL AND error_code IS NOT NULL)", name='ck_identity_result'),
    sa.CheckConstraint("decision IN ('VERIFIED', 'REJECTED', 'RETRY', 'MANUAL_REVIEW')", name='ck_identity_decision'),
    sa.CheckConstraint("error_code IN ('validation_failed', 'rate_limited', 'internal_error')", name='ck_identity_error'),
    sa.CheckConstraint("status IN ('processing', 'completed', 'failed', 'interrupted')", name='ck_identity_status'),
    sa.CheckConstraint('expires_at > created_at AND (completed_at IS NULL OR completed_at >= created_at)', name='ck_identity_dates'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_id', 'idempotency_key', name='uq_identity_owner_key')
    )
    op.create_index('ix_identity_expires', 'identity_requests', ['expires_at'], unique=False)
    op.create_index('ix_identity_owner_created', 'identity_requests', ['owner_id', 'created_at'], unique=False)
    op.create_table('user_roles',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.CheckConstraint("role ~ '^[a-z][a-z0-9_]{0,31}$'", name='ck_user_roles_role'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'role')
    )

def downgrade():
    op.drop_table('user_roles')
    op.drop_index('ix_identity_owner_created', table_name='identity_requests')
    op.drop_index('ix_identity_expires', table_name='identity_requests')
    op.drop_table('identity_requests')
    op.drop_table('users')
