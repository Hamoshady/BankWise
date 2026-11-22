"""Allow more than one embedding model by storing each chunk's vector dimension.

Existing rows are hash embeddings (384 dimensions); the default fills them in before
the column becomes required. Model identity stays on the document row because one
document always uses one model/dimension pair.
"""
from alembic import op
import sqlalchemy as sa

revision = '0004_dynamic_embeddings'
down_revision = '0003_shared_policies'
branch_labels = depends_on = None


def upgrade():
    op.drop_constraint('ck_rag_model', 'rag_documents', type_='check')
    op.create_check_constraint(
        'ck_rag_model', 'rag_documents',
        "char_length(model_id) BETWEEN 1 AND 80 AND model_id = btrim(model_id)")
    op.add_column('rag_document_chunks', sa.Column(
        'dimension', sa.Integer(), nullable=False, server_default='384'))
    op.alter_column('rag_document_chunks', 'dimension', server_default=None)
    op.drop_constraint('ck_rag_vector', 'rag_document_chunks', type_='check')
    op.create_check_constraint(
        'ck_rag_vector', 'rag_document_chunks',
        'array_ndims(embedding) = 1 AND cardinality(embedding) = dimension '
        'AND array_position(embedding, NULL) IS NULL')
    op.create_check_constraint(
        'ck_rag_dimension', 'rag_document_chunks',
        'dimension BETWEEN 16 AND 4096')


def downgrade():
    # Reject rows that the old fixed-width schema cannot represent before tightening it.
    op.execute("DELETE FROM rag_document_chunks WHERE dimension <> 384")
    op.execute("DELETE FROM rag_documents WHERE id NOT IN "
               "(SELECT DISTINCT document_id FROM rag_document_chunks)")
    op.drop_constraint('ck_rag_dimension', 'rag_document_chunks', type_='check')
    op.drop_constraint('ck_rag_vector', 'rag_document_chunks', type_='check')
    op.create_check_constraint(
        'ck_rag_vector', 'rag_document_chunks',
        'array_ndims(embedding) = 1 AND cardinality(embedding) = 384 '
        'AND array_position(embedding, NULL) IS NULL')
    op.drop_column('rag_document_chunks', 'dimension')
    op.drop_constraint('ck_rag_model', 'rag_documents', type_='check')
    op.create_check_constraint('ck_rag_model', 'rag_documents', "model_id = 'hashing-v1-384'")
