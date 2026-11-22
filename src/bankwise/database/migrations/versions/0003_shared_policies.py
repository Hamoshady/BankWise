from alembic import op
import sqlalchemy as sa
revision = '0003_shared_policies'
down_revision = '0002_rag'
branch_labels = depends_on = None

def upgrade():
    op.add_column('rag_documents', sa.Column('shared', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('rag_documents', sa.Column('content_hash', sa.String(64), nullable=True))

def downgrade():
    op.drop_column('rag_documents', 'content_hash')
    op.drop_column('rag_documents', 'shared')
