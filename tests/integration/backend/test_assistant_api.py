"""Real PostgreSQL, JWT, owner isolation, atomic confirmation and concurrent retries."""
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from sqlalchemy import func, select

from bankwise.core.security import Principal
from bankwise.database.models import DemoAccount, DemoCustomer, DemoTransaction, UserRole
from test_api import api, password_hash


@pytest.fixture
def assistant(api):
    api.app.state.banking_demo = True
    from bankwise.agents.workflow import route
    api.app.state.supervisor_planner = route  # Deterministic test double, not production fallback.
    accounts = {}
    with api.db.session() as session, session.begin():
        for who in ('alice', 'bob'):
            owner = UUID(api.users[who].subject_id)
            session.add(UserRole(user_id=owner, role='customer'))
            session.add(DemoCustomer(owner_id=owner, verified=True))
            account = DemoAccount(owner_id=owner, balance=1000000, status='open')
            session.add(account)
            session.flush()
            accounts[who] = str(account.id)
            api.users[who] = Principal(str(owner), api.users[who].roles | {'customer'})
    api.demo_accounts = accounts
    return api


def prepare(api):
    result = api.client.post('/api/v1/assistant/chat', headers=api.headers(), json={
        'message': 'حوّل', 'tool': 'execute_transfer', 'arguments': {'account_id': api.demo_accounts['alice'],
        'beneficiary_id': api.demo_accounts['bob'], 'amount': '100.00'}})
    assert result.status_code == 200, result.text
    return result.json()


def test_confirmation_concurrency_and_isolation(assistant):
    api = assistant
    response = prepare(api)
    op_id = response['operation']['id']
    url = f'/api/v1/assistant/operations/{op_id}/confirm'
    otp_url = f'/api/v1/assistant/demo/otp/{op_id}'
    assert api.client.get(otp_url, headers=api.headers('bob')).status_code == 404
    assert api.client.get('/api/v1/assistant/conversations/' + response['conversation_id'], headers=api.headers('bob')).status_code == 404
    code = api.client.get(otp_url, headers=api.headers()).json()['otp']
    assert api.client.post(url, headers=api.headers('bob'), json={'approved': True, 'otp': code}).status_code == 404
    headers = api.headers()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: api.client.post(url, headers=headers, json={'approved': True, 'otp': code}), range(2)))
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json()['result'] == results[1].json()['result']
    with api.db.session() as session:
        assert session.scalar(select(func.count()).select_from(DemoTransaction)) == 1
        assert session.get(DemoAccount, UUID(api.demo_accounts['alice'])).balance == 990000
        assert session.get(DemoAccount, UUID(api.demo_accounts['bob'])).balance == 1010000


def test_jwt_and_rbac_gate_precedes_graph(assistant):
    api = assistant
    assert api.client.post('/api/v1/assistant/chat', json={'message': 'رصيد'}).status_code == 401
    assert api.client.post('/api/v1/assistant/chat', headers=api.headers('reader'), json={'message': 'رصيد'}).status_code == 403
    assert api.client.post('/api/v1/assistant/chat', headers=api.headers(), json={'message': 'رصيد', 'owner_id': api.users['bob'].subject_id}).status_code == 422


def test_concurrent_operations_cannot_exceed_daily_limit(assistant):
    api = assistant
    with api.db.session() as session, session.begin():
        session.get(DemoAccount, UUID(api.demo_accounts['alice'])).balance = 10000000
    operations = []
    headers = api.headers()
    for _ in range(2):
        response = api.client.post('/api/v1/assistant/chat', headers=headers, json={
            'message': 'transfer', 'tool': 'execute_transfer', 'arguments': {
                'account_id': api.demo_accounts['alice'], 'beneficiary_id': api.demo_accounts['bob'], 'amount': '30000'}})
        assert response.status_code == 200, response.text
        operation = response.json()['operation']['id']
        code = api.client.get('/api/v1/assistant/demo/otp/' + operation, headers=headers).json()['otp']
        operations.append((operation, code))
    def confirm(item):
        return api.client.post('/api/v1/assistant/operations/' + item[0] + '/confirm',
            headers=headers, json={'approved': True, 'otp': item[1]})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(confirm, operations))
    assert sorted(r.status_code for r in responses) == [200, 409]
    with api.db.session() as session:
        assert session.get(DemoAccount, UUID(api.demo_accounts['alice'])).balance == 7000000
        assert session.scalar(select(func.count()).select_from(DemoTransaction)) == 1


def test_local_otp_disabled_and_statement_pdf(assistant):
    api = assistant
    result = api.client.get('/api/v1/assistant/statement.pdf', headers=api.headers())
    assert result.status_code == 200 and result.content.startswith(b'%PDF')
    assert result.headers['cache-control'] == 'no-store'
    assert api.client.get('/api/v1/assistant/statement.pdf?account_id=' + api.demo_accounts['bob'], headers=api.headers()).status_code == 404
    response = prepare(api)
    api.app.state.banking_demo = False
    assert api.client.get('/api/v1/assistant/demo/otp/' + response['operation']['id'], headers=api.headers()).status_code == 404
    assert api.client.post('/api/v1/assistant/chat', headers=api.headers(), json={'message': 'رصيد'}).status_code == 503


def test_attempts_persist_and_audit_failure_prevents_commit(assistant, monkeypatch):
    api = assistant
    response = prepare(api)
    op_id = response['operation']['id']
    code = api.client.get('/api/v1/assistant/demo/otp/' + op_id, headers=api.headers()).json()['otp']
    url = '/api/v1/assistant/operations/' + op_id + '/confirm'
    wrong = '111111' if code != '111111' else '222222'
    for remaining in range(4, -1, -1):
        result = api.client.post(url, headers=api.headers(), json={'approved': True, 'otp': wrong})
        assert result.json()['attempts_remaining'] == remaining
    assert api.client.post(url, headers=api.headers(), json={'approved': True, 'otp': code}).status_code == 409
    def broken(*args):
        raise RuntimeError('private audit failure')
    headers = api.headers()
    monkeypatch.setattr(api.app.state.runtime.audit_sink, 'write', broken)
    # Existing security handler fails before any graph/tool is invoked.
    result = api.client.post('/api/v1/assistant/chat', headers=headers, json={'message': 'رصيد'})
    assert result.status_code == 500 and 'private audit failure' not in result.text


def test_native_checkpoint_history_and_audit_are_owner_scoped(assistant):
    api=assistant
    result=prepare(api)
    conversation=result['conversation_id']
    history=api.client.get('/api/v1/assistant/checkpoints?conversation_id='+conversation,headers=api.headers())
    assert history.status_code==200
    nodes={r['node'] for r in history.json()['checkpoints']}
    assert {'security','supervisor','transfer','confirmation_gate','final_response','audit_log','checkpointed_state'} <= nodes
    other=api.client.get('/api/v1/assistant/checkpoints?conversation_id='+conversation,headers=api.headers('bob'))
    assert other.json()['checkpoints']==[]
    events=api.client.get('/api/v1/assistant/audit?conversation_id='+conversation,headers=api.headers()).json()['events']
    assert any(e['event']=='confirmation_requested' for e in events)
    assert api.client.get('/api/v1/assistant/audit?conversation_id='+conversation,headers=api.headers('bob')).json()['events']==[]
    assert api.client.get('/api/v1/assistant/checkpoints').status_code==401
    assert api.client.get('/api/v1/assistant/audit',headers=api.headers('reader')).status_code==403
    # Snapshot storage contains neither OTP nor raw request text.
    from bankwise.database.graph_schema import checkpoints
    import json
    with api.db.session() as session:
        stored=json.dumps(session.execute(select(checkpoints.c.checkpoint)).scalars().all(),ensure_ascii=False)
    assert 'حوّل' not in stored and 'otp_hash' not in stored and 'password' not in stored


def test_beneficiaries_do_not_expose_balances(assistant):
    response=assistant.client.get('/api/v1/assistant/demo/beneficiaries',headers=assistant.headers())
    assert response.status_code==200
    assert response.json()['beneficiaries']
    assert all(set(row)=={'id','account_number','label'} for row in response.json()['beneficiaries'])


def test_transfer_by_public_number_preserves_authorization_and_otp(assistant):
    api = assistant
    with api.db.session() as session:
        numbers = {who: session.get(DemoAccount, UUID(id)).account_number
                   for who, id in api.demo_accounts.items()}
    assert all(len(n) == 12 and n.isascii() and n.isdigit() for n in numbers.values())
    args = {'account_id': numbers['alice'], 'beneficiary_id': numbers['bob'], 'amount': '12.34'}
    # A public number never grants ownership of the source account.
    assert api.client.post('/api/v1/assistant/chat', headers=api.headers('bob'), json={
        'message': 'transfer', 'tool': 'execute_transfer', 'arguments': args}).status_code == 404
    response = api.client.post('/api/v1/assistant/chat', headers=api.headers(), json={
        'message': 'transfer', 'tool': 'execute_transfer', 'arguments': args})
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'awaiting_confirmation'
    op = response.json()['operation']['id']
    with api.db.session() as session:
        assert session.scalar(select(func.count()).select_from(DemoTransaction)) == 0
    otp = api.client.get('/api/v1/assistant/demo/otp/' + op, headers=api.headers()).json()['otp']
    confirmation = api.client.post('/api/v1/assistant/operations/' + op + '/confirm',
        headers=api.headers(), json={'approved': True, 'otp': otp})
    assert confirmation.status_code == 200, confirmation.text
    with api.db.session() as session:
        tx = session.scalar(select(DemoTransaction))
        assert str(tx.source_id) == api.demo_accounts['alice']
        assert str(tx.destination_id) == api.demo_accounts['bob']
        assert tx.amount == 1234
    statement = api.client.post('/api/v1/assistant/chat', headers=api.headers(), json={
        'message': 'statement', 'tool': 'get_statement', 'arguments': {'account_id': numbers['alice']}})
    assert statement.json()['results'][0]['data']['accounts'][0]['account_number'] == numbers['alice']
    assert api.client.get('/api/v1/assistant/statement.pdf?account_id=' + numbers['alice'], headers=api.headers()).status_code == 200


def test_account_number_backfill_preserves_ledger_and_constraints(assistant):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    api = assistant
    with api.db.session() as session, session.begin():
        from uuid import uuid4
        session.add(DemoTransaction(operation_id=uuid4(), source_id=UUID(api.demo_accounts['alice']),
            destination_id=UUID(api.demo_accounts['bob']), amount=100))
    config = Config(str(Path(__file__).resolve().parents[3] / 'alembic.ini'))
    command.downgrade(config, '0010_onboarding')
    with api.db.engine.connect() as conn:
        before_accounts = conn.execute(text('SELECT id, owner_id, balance, status FROM demo_accounts ORDER BY id')).all()
        before_tx = conn.execute(text('SELECT id, operation_id, source_id, destination_id, amount, created_at FROM demo_transactions ORDER BY id')).all()
    command.upgrade(config, 'head')
    with api.db.engine.connect() as conn:
        assert conn.execute(text('SELECT id, owner_id, balance, status FROM demo_accounts ORDER BY id')).all() == before_accounts
        assert conn.execute(text('SELECT id, operation_id, source_id, destination_id, amount, created_at FROM demo_transactions ORDER BY id')).all() == before_tx
        numbers = conn.execute(text('SELECT account_number FROM demo_accounts')).scalars().all()
        assert len(set(numbers)) == len(numbers) and all(len(n) == 12 for n in numbers)
    for invalid in [numbers[1], '123', 'a'*12]:
        with pytest.raises(IntegrityError), api.db.engine.begin() as conn:
            conn.execute(text('UPDATE demo_accounts SET account_number=:n WHERE id=:id'),
                         {'n': invalid, 'id': api.demo_accounts['alice']}) if invalid not in numbers else conn.execute(
                             text('UPDATE demo_accounts SET account_number=:n'), {'n': invalid})


def test_concurrent_new_account_numbers_are_unique(assistant):
    api = assistant
    def create(_):
        with api.db.session() as session, session.begin():
            account = DemoAccount(owner_id=UUID(api.users['alice'].subject_id), balance=0)
            session.add(account)
            session.flush()
            return account.account_number
    with ThreadPoolExecutor(max_workers=4) as pool:
        numbers = list(pool.map(create, range(8)))
    assert len(set(numbers)) == 8
    assert all(len(n) == 12 and n.isdigit() for n in numbers)


def test_transaction_references_backfill_and_concurrent_dates(assistant):
    from datetime import datetime, timezone
    from pathlib import Path
    from uuid import uuid4
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    api = assistant
    config = Config(str(Path(__file__).resolve().parents[3] / 'alembic.ini'))
    command.downgrade(config, '0011_account_numbers')
    with api.db.engine.begin() as conn:
        for stamp in ('2026-11-19 21:00:00+00','2026-11-19 22:30:00+00'):
            conn.execute(text('INSERT INTO demo_transactions (id,operation_id,destination_id,amount,created_at) VALUES (:id,:op,:dest,1234,:stamp)'),
                {'id':uuid4(),'op':uuid4(),'dest':UUID(api.demo_accounts['alice']),'stamp':stamp})
        before = conn.execute(text('SELECT id,operation_id,source_id,destination_id,amount,created_at FROM demo_transactions ORDER BY id')).all()
        balances = conn.execute(text('SELECT id,balance FROM demo_accounts ORDER BY id')).all()
    command.upgrade(config, 'head')
    with api.db.engine.connect() as conn:
        assert conn.execute(text('SELECT id,operation_id,source_id,destination_id,amount,created_at FROM demo_transactions ORDER BY id')).all() == before
        assert conn.execute(text('SELECT id,balance FROM demo_accounts ORDER BY id')).all() == balances
        assert set(conn.execute(text('SELECT reference FROM demo_transactions')).scalars()) == {'BW-20261119-000001','BW-20261120-000001'}
    def insert(_):
        with api.db.session() as session, session.begin():
            tx = DemoTransaction(operation_id=uuid4(), destination_id=UUID(api.demo_accounts['alice']),
                                 amount=1, created_at=datetime(2026,11,20,12,tzinfo=timezone.utc))
            session.add(tx); session.flush()
            return tx.reference
    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = list(pool.map(insert, range(8)))
    assert set(refs) == {f'BW-20261120-{n:06d}' for n in range(2,10)}


def test_replayed_legacy_transfer_receipt_gets_reference(assistant):
    from bankwise.database.models import AssistantOperation
    api = assistant
    result = prepare(api)
    op = result['operation']['id']
    otp = api.client.get('/api/v1/assistant/demo/otp/' + op, headers=api.headers()).json()['otp']
    url = '/api/v1/assistant/operations/' + op + '/confirm'
    first = api.client.post(url, headers=api.headers(), json={'approved':True,'otp':otp}).json()
    reference = first['result']['reference']
    with api.db.session() as session, session.begin():
        operation = session.get(AssistantOperation, UUID(op))
        operation.result = {k:v for k,v in operation.result.items() if k != 'reference'}
    replay = api.client.post(url, headers=api.headers(), json={'approved':True,'otp':otp}).json()
    assert replay['result']['reference'] == reference
    with api.db.session() as session:
        assert session.scalar(select(func.count()).select_from(DemoTransaction)) == 1
