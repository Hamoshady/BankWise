from datetime import date
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from bankwise.database.financial_schema import TABLES, grants, imports
from bankwise.agents.historical import HistoricalBanking
from bankwise.agents.contracts import ChatInput
from bankwise.core.errors import NotFound, InvalidInputError


@pytest.fixture
def history():
    engine = create_engine('sqlite://')
    owner, other = uuid4(), uuid4()
    with engine.begin() as c:
        c.execute(text('CREATE TABLE users (id CHAR(32) PRIMARY KEY)'))
        for t in (*TABLES.values(), imports, grants): t.create(c)
        c.execute(TABLES['district'].insert(), {'district_id':1})
        c.execute(TABLES['client'].insert(), [{'client_id':1,'district_id':1},{'client_id':2,'district_id':1}])
        c.execute(TABLES['account'].insert(), [{'account_id':1,'district_id':1,'date':date(1993,1,1)},{'account_id':2,'district_id':1,'date':date(1993,1,1)}])
        c.execute(TABLES['disp'].insert(), [{'disp_id':1,'client_id':1,'account_id':1},{'disp_id':2,'client_id':2,'account_id':2}])
        c.execute(grants.insert(),{'owner_id':owner,'client_id':1})
        c.execute(TABLES['trans'].insert(),[{'trans_id':i,'account_id':1,'date':date(1998,1,1),'type':'PRIJEM' if i==1 else 'VYDAJ','amount':100,'balance':1000-i} for i in range(1,106)])
    with Session(engine) as s: yield HistoricalBanking(s,owner), HistoricalBanking(s,other)


def test_grants_currency_pagination_and_full_totals(history):
    service, other = history
    result = service.read()
    assert result['currency']=='CZK' and not result['demo']
    assert len(result['transactions'])==100 and result['total_transactions']==105
    assert result['outgoing_total']=='10400.00' and result['incoming_total']=='100.00'
    assert len(service.read(offset=100)['transactions'])==5
    assert service.read('get_last_deposits')['total_transactions']==1
    assert service.read('get_last_withdrawals')['total_transactions']==104
    with pytest.raises(NotFound): other.read()
    with pytest.raises(NotFound): service.read(args={'historical_account_id':2})


def test_empty_and_invalid_dates(history):
    service,_=history
    result=service.read(start=date(2000,1,1))
    assert result['transactions']==[] and result['incoming_total']=='0.00'
    with pytest.raises(InvalidInputError): service.read(start=date(2000,1,1),end=date(1990,1,1))


def test_historical_mutations_rejected_before_prepare():
    from bankwise.agents.workflow import AssistantWorkflow
    from bankwise.database.models import AssistantConversation, AssistantAudit
    engine=create_engine('sqlite://')
    with engine.begin() as c:
        c.execute(text('CREATE TABLE users (id CHAR(32) PRIMARY KEY)'))
        AssistantConversation.__table__.create(c)
        AssistantAudit.__table__.create(c)
    with Session(engine) as s:
        service=AssistantWorkflow(s,uuid4(),demo=True,knowledge=lambda *a:{},audit=lambda *a:None)
        with pytest.raises(InvalidInputError):
            service.chat(ChatInput(message='فتح',dataset='berka',tool='open_account'))


def test_cib_summaries_match_catalog_and_official_sources():
    import hashlib, json
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]
    catalog=json.loads((root/'configs/cib_sources.json').read_text())
    assert len(catalog['documents'])==5
    for name, source in catalog['documents'].items():
        content=(root/'knowledge/cib'/f'{name}.md').read_bytes()
        assert hashlib.sha256(content).hexdigest()==source['sha256']
        assert source['url'].startswith('https://www.cibeg.com/')
        assert source['representation']=='reviewed_arabic_summary'
