"""Synchronous durable native saver. Each write uses its own DB transaction.

Graph threads are per request, bound to owner + conversation by the server. This
retains every superstep without enabling replay of financial actions. No pickle,
OTP, JWT or raw user message is persisted. Results are owner-scoped banking data.
"""
import json
from threading import RLock
from uuid import UUID
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, WRITES_IDX_MAP
from sqlalchemy import select, delete
from bankwise.database.graph_schema import checkpoints, writes


class DatabaseSaver(BaseCheckpointSaver):
    def __init__(self, engine, owner, conversation, thread):
        super().__init__()
        self.engine,self.owner,self.conversation,self.thread=engine,UUID(str(owner)),UUID(str(conversation)),thread
        self.lock=RLock()

    def scope(self,config):
        c=config['configurable']
        if c['thread_id']!=self.thread or c.get('checkpoint_ns','')!='':
            raise ValueError('checkpoint scope mismatch')
        return c

    def put(self,config,checkpoint,metadata,new_versions):
        c=self.scope(config)
        # Fail closed on unexpected serialization types; no executable deserialization.
        snapshot=json.loads(json.dumps(checkpoint))
        meta=json.loads(json.dumps(metadata))
        with self.lock,self.engine.begin() as conn:
            conn.execute(checkpoints.insert().values(thread_id=self.thread,checkpoint_ns='',checkpoint_id=checkpoint['id'],
                owner_id=self.owner,conversation_id=self.conversation,parent_id=c.get('checkpoint_id'),checkpoint=snapshot,meta=meta))
        return {'configurable':{'thread_id':self.thread,'checkpoint_ns':'','checkpoint_id':checkpoint['id']}}

    def put_writes(self,config,values,task_id,task_path=''):
        c=self.scope(config)
        with self.lock,self.engine.begin() as conn:
            for index,(channel,value) in enumerate(values):
                index=WRITES_IDX_MAP.get(channel,index)
                if channel=='__error__': value='node_failed'
                value=json.loads(json.dumps(value))
                match=(writes.c.thread_id==self.thread,writes.c.checkpoint_ns=='',writes.c.checkpoint_id==c['checkpoint_id'],writes.c.task_id==task_id,writes.c.idx==index)
                exists=conn.execute(select(writes.c.idx).where(*match)).first()
                if exists:
                    if index<0: conn.execute(writes.update().where(*match).values(channel=channel,value=value))
                else: conn.execute(writes.insert().values(thread_id=self.thread,checkpoint_ns='',checkpoint_id=c['checkpoint_id'],task_id=task_id,idx=index,channel=channel,value=value))

    def get_tuple(self,config):
        c=self.scope(config)
        with self.lock,self.engine.connect() as conn:
            query=select(checkpoints).where(checkpoints.c.thread_id==self.thread,checkpoints.c.owner_id==self.owner)
            if c.get('checkpoint_id'): query=query.where(checkpoints.c.checkpoint_id==c['checkpoint_id'])
            row=conn.execute(query.order_by(checkpoints.c.checkpoint_id.desc()).limit(1)).mappings().first()
            if row is None:return None
            pending=conn.execute(select(writes).where(writes.c.thread_id==self.thread,writes.c.checkpoint_id==row['checkpoint_id']).order_by(writes.c.task_id,writes.c.idx)).mappings().all()
        def cfg(id):return {'configurable':{'thread_id':self.thread,'checkpoint_ns':'','checkpoint_id':id}}
        return CheckpointTuple(config=cfg(row['checkpoint_id']),checkpoint=row['checkpoint'],metadata=row['meta'],
            parent_config=cfg(row['parent_id']) if row['parent_id'] else None,
            pending_writes=[(w['task_id'],w['channel'],w['value']) for w in pending])

    def list(self,config,*,filter=None,before=None,limit=None):
        if config:self.scope(config)
        query=select(checkpoints.c.checkpoint_id,checkpoints.c.meta).where(checkpoints.c.thread_id==self.thread,checkpoints.c.owner_id==self.owner)
        if before: query=query.where(checkpoints.c.checkpoint_id<self.scope(before)['checkpoint_id'])
        with self.engine.connect() as conn:
            rows=conn.execute(query.order_by(checkpoints.c.checkpoint_id.desc())).all()
        count=0
        for id,meta in rows:
            if filter and any(meta.get(k)!=v for k,v in filter.items()):continue
            if limit is not None and count>=limit:break
            yield self.get_tuple({'configurable':{'thread_id':self.thread,'checkpoint_id':id}})
            count+=1

    def delete_thread(self,thread_id):
        if thread_id!=self.thread:raise ValueError('checkpoint scope mismatch')
        with self.lock,self.engine.begin() as conn:
            conn.execute(delete(writes).where(writes.c.thread_id==thread_id))
            conn.execute(delete(checkpoints).where(checkpoints.c.thread_id==thread_id,checkpoints.c.owner_id==self.owner))
