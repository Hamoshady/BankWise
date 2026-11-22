from uuid import uuid4
from typing import TypedDict
import pytest
from sqlalchemy import create_engine
from langgraph.graph import StateGraph, START, END
from bankwise.database.graph_schema import checkpoints, writes
from bankwise.agents.checkpoints import DatabaseSaver
from bankwise.agents.planner import validate_plan
from bankwise.core.errors import ServiceUnavailable


class State(TypedDict):
    count: int


def test_native_checkpoints_survive_failure_and_new_saver(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'checkpoints.db'))
    with engine.begin() as c:
        checkpoints.create(c); writes.create(c)
    owner,conversation,thread=uuid4(),uuid4(),str(uuid4())
    saver=DatabaseSaver(engine,owner,conversation,thread)
    builder=StateGraph(State)
    builder.add_node('first',lambda state:{'count':state['count']+1})
    def fail(state):raise RuntimeError('interrupted')
    builder.add_node('second',fail)
    builder.add_edge(START,'first');builder.add_edge('first','second');builder.add_edge('second',END)
    config={'configurable':{'thread_id':thread}}
    with pytest.raises(RuntimeError):builder.compile(checkpointer=saver).invoke({'count':0},config,durability='sync')
    engine.dispose()
    recovered=DatabaseSaver(create_engine('sqlite:///'+str(tmp_path/'checkpoints.db')),owner,conversation,thread)
    history=list(recovered.list(config))
    assert len(history)>=3
    assert history[0].checkpoint['channel_values']['count']==1
    assert any(w[1]=='__error__' for w in history[0].pending_writes)
    # A different authenticated owner cannot inspect this thread.
    other=DatabaseSaver(engine,uuid4(),conversation,thread)
    assert other.get_tuple(config) is None
    with pytest.raises(ValueError): recovered.get_tuple({'configurable':{'thread_id':'other'}})
    assert len(list(recovered.list(config,limit=1)))==1


def test_plans_allow_only_known_tools_and_one_sensitive_action():
    assert validate_plan(['execute_transfer','get_statement'])==['get_statement','execute_transfer']
    for value in [[],['run_sql'],['open_account','close_account'],['get_statement','get_statement'],[{}]]:
        with pytest.raises(ServiceUnavailable): validate_plan(value)
