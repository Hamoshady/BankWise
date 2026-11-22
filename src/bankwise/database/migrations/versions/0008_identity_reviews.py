"""Encrypted, time-limited evidence and manual account activation."""
from alembic import op
import sqlalchemy as sa
revision='0008_identity_reviews'
down_revision='0007_graph_checkpoints'
branch_labels=depends_on=None

def upgrade():
    op.create_table('identity_reviews',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('owner_id',sa.Uuid(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),
        sa.Column('encrypted_payload',sa.LargeBinary()),
        sa.Column('reason',sa.String(40)),
        sa.Column('reviewer_id',sa.Uuid(),sa.ForeignKey('users.id')),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.Column('decided_at',sa.DateTime(timezone=True)),
        sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint("status IN ('pending','approved','rejected')",name='ck_review_status'))
    op.create_index('ix_identity_reviews_owner_id','identity_reviews',['owner_id'])

def downgrade():
    op.drop_table('identity_reviews')
