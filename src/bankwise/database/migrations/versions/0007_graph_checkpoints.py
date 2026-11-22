"""Durable native LangGraph checkpoints and pending writes."""
from alembic import op
import sqlalchemy as sa
revision='0007_graph_checkpoints'
down_revision='0006_financial'
branch_labels=depends_on=None

def upgrade():
    op.create_table('assistant_graph_checkpoints',
        sa.Column('thread_id',sa.String(80),primary_key=True),sa.Column('checkpoint_ns',sa.String(80),primary_key=True),
        sa.Column('checkpoint_id',sa.String(80),primary_key=True),sa.Column('owner_id',sa.Uuid(),nullable=False),
        sa.Column('conversation_id',sa.Uuid(),nullable=False),sa.Column('parent_id',sa.String(80)),
        sa.Column('checkpoint',sa.JSON(),nullable=False),sa.Column('meta',sa.JSON(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False))
    op.create_index('ix_assistant_graph_checkpoints_owner_id','assistant_graph_checkpoints',['owner_id'])
    op.create_index('ix_assistant_graph_checkpoints_conversation_id','assistant_graph_checkpoints',['conversation_id'])
    op.create_table('assistant_graph_writes',
        sa.Column('thread_id',sa.String(80),primary_key=True),sa.Column('checkpoint_ns',sa.String(80),primary_key=True),
        sa.Column('checkpoint_id',sa.String(80),primary_key=True),sa.Column('task_id',sa.String(80),primary_key=True),
        sa.Column('idx',sa.Integer,primary_key=True),sa.Column('channel',sa.String(120),nullable=False),sa.Column('value',sa.JSON()))

def downgrade():
    op.drop_table('assistant_graph_writes')
    op.drop_table('assistant_graph_checkpoints')
