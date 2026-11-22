"""Assistant checkpoints, confirmations and isolated demo banking."""
from alembic import op
import sqlalchemy as sa

revision = '0005_assistant'
down_revision = '0004_dynamic_embeddings'
branch_labels = depends_on = None


def upgrade():
    op.create_table('demo_customers',
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('verified', sa.Boolean(), nullable=False))
    op.create_table('demo_accounts',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('balance', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.CheckConstraint('balance >= 0', name='ck_demo_balance'),
        sa.CheckConstraint("status IN ('open', 'closed')", name='ck_demo_account_status'))
    op.create_index('ix_demo_accounts_owner_id', 'demo_accounts', ['owner_id'])
    op.create_table('demo_transactions',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('operation_id', sa.Uuid(), nullable=False, unique=True),
        sa.Column('source_id', sa.Uuid(), sa.ForeignKey('demo_accounts.id')),
        sa.Column('destination_id', sa.Uuid(), sa.ForeignKey('demo_accounts.id'), nullable=False),
        sa.Column('amount', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint('amount > 0', name='ck_demo_tx_amount'))
    op.create_table('demo_loans',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('operation_id', sa.Uuid(), nullable=False, unique=True),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(24), nullable=False))
    op.create_index('ix_demo_loans_owner_id', 'demo_loans', ['owner_id'])
    op.create_table('assistant_conversations',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('state', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index('ix_assistant_conversations_owner_id', 'assistant_conversations', ['owner_id'])
    op.create_table('assistant_operations',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('conversation_id', sa.Uuid(), sa.ForeignKey('assistant_conversations.id'), nullable=False),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('tool', sa.String(40), nullable=False),
        sa.Column('arguments', sa.JSON(), nullable=False),
        sa.Column('otp_hash', sa.String(512), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('result', sa.JSON()),
        sa.CheckConstraint('attempts BETWEEN 0 AND 5', name='ck_assistant_attempts'),
        sa.CheckConstraint("status IN ('pending', 'completed', 'cancelled', 'locked', 'expired')", name='ck_assistant_operation_status'))
    op.create_index('ix_assistant_operations_owner_id', 'assistant_operations', ['owner_id'])
    op.create_index('ix_assistant_operations_conversation_id', 'assistant_operations', ['conversation_id'])
    op.create_table('assistant_audit',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('conversation_id', sa.Uuid(), sa.ForeignKey('assistant_conversations.id'), nullable=False),
        sa.Column('event', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index('ix_assistant_audit_conversation_id', 'assistant_audit', ['conversation_id'])


def downgrade():
    for table in ('assistant_audit', 'assistant_operations', 'assistant_conversations',
                  'demo_loans', 'demo_transactions', 'demo_accounts', 'demo_customers'):
        op.drop_table(table)
