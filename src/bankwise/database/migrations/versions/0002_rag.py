"""Private knowledge documents and bounded vectors; preserves all existing tables."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, DOUBLE_PRECISION
revision = '0002_rag'
down_revision = '0001_accounts_identity'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('rag_documents',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('owner_id', sa.UUID(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source', sa.String(80), nullable=False),
        sa.Column('model_id', sa.String(80), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('owner_id', 'source', name='uq_rag_owner_source'),
        sa.CheckConstraint("source ~ '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$'", name='ck_rag_source'),
        sa.CheckConstraint("model_id = 'hashing-v1-384'", name='ck_rag_model'))
    op.create_table('rag_document_chunks',
        sa.Column('document_id', sa.UUID(), sa.ForeignKey('rag_documents.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('chunk_id', sa.String(64), primary_key=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('embedding', ARRAY(DOUBLE_PRECISION), nullable=False),
        sa.CheckConstraint('char_length(text) BETWEEN 1 AND 1000', name='ck_rag_text'),
        sa.CheckConstraint('array_ndims(embedding) = 1 AND cardinality(embedding) = 384 AND array_position(embedding, NULL) IS NULL', name='ck_rag_vector'),
        sa.CheckConstraint("NOT (embedding && ARRAY['NaN'::float8, 'Infinity'::float8, '-Infinity'::float8])", name='ck_rag_vector_finite'))


def downgrade():
    op.drop_table('rag_document_chunks')
    op.drop_table('rag_documents')
