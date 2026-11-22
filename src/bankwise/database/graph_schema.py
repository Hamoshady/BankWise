"""Native LangGraph checkpoints. No FK to an uncommitted financial transaction."""
import sqlalchemy as sa
from bankwise.database.models import Base

checkpoints=sa.Table('assistant_graph_checkpoints',Base.metadata,
    sa.Column('thread_id',sa.String(80),primary_key=True),
    sa.Column('checkpoint_ns',sa.String(80),primary_key=True),
    sa.Column('checkpoint_id',sa.String(80),primary_key=True),
    sa.Column('owner_id',sa.Uuid,nullable=False,index=True),
    sa.Column('conversation_id',sa.Uuid,nullable=False,index=True),
    sa.Column('parent_id',sa.String(80)),sa.Column('checkpoint',sa.JSON,nullable=False),
    sa.Column('meta',sa.JSON,nullable=False),
    sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False))
writes=sa.Table('assistant_graph_writes',Base.metadata,
    sa.Column('thread_id',sa.String(80),primary_key=True),sa.Column('checkpoint_ns',sa.String(80),primary_key=True),
    sa.Column('checkpoint_id',sa.String(80),primary_key=True),sa.Column('task_id',sa.String(80),primary_key=True),
    sa.Column('idx',sa.Integer,primary_key=True),sa.Column('channel',sa.String(120),nullable=False),sa.Column('value',sa.JSON))
