"""Real PostgreSQL + real security + real retrieval; no model/network mocks."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import UUID
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError, IntegrityError
from bankwise.core.security import Principal
from bankwise.database.models import RagChunk, RagDocument, UserRole
from bankwise.rag.embedder import HashingEmbedder
from bankwise.rag.postgres_store import PostgresVectorStore
from test_api import api, password_hash  # reuse the existing real-security API fixture


@pytest.fixture
def rag(api):
    with api.db.session() as session, session.begin():
        for who in ('alice', 'bob'):
            session.add(UserRole(user_id=UUID(api.users[who].subject_id), role='knowledge_user'))
            api.users[who] = Principal(api.users[who].subject_id, api.users[who].roles | {'knowledge_user'})
    return api


def put(rag, source='policy', value='Savings interest is paid monthly.', who='alice'):
    return rag.client.put('/api/v1/rag/documents/' + source, json={'text': value}, headers=rag.headers(who))


def ask(rag, query='When is savings interest paid?', who='alice'):
    return rag.client.post('/api/v1/rag/query', json={'query': query}, headers=rag.headers(who))


def test_complete_journey_persists_replaces_deletes_and_cites(rag):
    first = put(rag)
    assert first.status_code == 200, first.text
    assert first.json()['chunks'] == 1
    assert put(rag).json()['id'] == first.json()['id']
    answer = ask(rag)
    assert answer.status_code == 200, answer.text
    assert 'monthly' in answer.json()['answer']
    assert answer.json()['citations'][0]['source'] == 'policy'
    assert answer.json()['mode'] == 'extractive'
    with rag.db.session() as session:
        assert session.scalar(select(func.count()).select_from(RagDocument)) == 1
        assert session.scalar(select(func.count()).select_from(RagChunk)) == 1
    assert put(rag, value='Savings interest is paid annually.').status_code == 200
    assert 'annually' in ask(rag).json()['answer']
    assert rag.client.get('/api/v1/rag/documents', headers=rag.headers()).json()[0]['source'] == 'policy'
    assert rag.client.delete('/api/v1/rag/documents/policy', headers=rag.headers()).status_code == 204
    assert ask(rag).json()['citations'] == []
    with rag.db.session() as session:
        assert session.scalar(select(func.count()).select_from(RagChunk)) == 0


def test_owner_isolation_and_no_client_owner_override(rag):
    assert put(rag).status_code == 200
    assert ask(rag, who='bob').json()['citations'] == []
    assert rag.client.get('/api/v1/rag/documents', headers=rag.headers('bob')).json() == []
    assert rag.client.delete('/api/v1/rag/documents/policy', headers=rag.headers('bob')).status_code == 404
    assert put(rag, value='Savings interest is paid weekly.', who='bob').status_code == 200
    assert 'monthly' in ask(rag).json()['answer']
    assert 'weekly' in ask(rag, who='bob').json()['answer']
    r = rag.client.post('/api/v1/rag/query', json={'query': 'interest', 'owner_id': rag.users['alice'].subject_id}, headers=rag.headers('bob'))
    assert r.status_code == 422


def test_auth_and_permission_gate_precedes_embedding(rag, monkeypatch):
    def forbidden(*args):
        pytest.fail('unauthorized request reached embedding')
    monkeypatch.setattr(HashingEmbedder, 'embed', forbidden)
    assert rag.client.put('/api/v1/rag/documents/policy', json={'text': 'interest'}).status_code == 401
    assert put(rag, who='reader').status_code == 403
    assert ask(rag, who='processor').status_code == 403


@pytest.mark.parametrize('body', [{'query': ''}, {'query': ' '}, {'query': 'x', 'top_k': 11}, {'query': 'x', 'top_k': True}, {'query': 'x', 'path': '/etc/passwd'}])
def test_query_schema_rejects_invalid_input(rag, body):
    assert rag.client.post('/api/v1/rag/query', json=body, headers=rag.headers()).status_code == 422


def test_upload_bounds_and_no_local_path_ingestion(rag):
    assert put(rag, value=' ').status_code == 422
    assert put(rag, value='x' * 8001).status_code == 422
    assert put(rag, value='x' * 17000).status_code == 413
    r = rag.client.put('/api/v1/rag/documents/policy', json={'path': '/etc/passwd'}, headers=rag.headers())
    assert r.status_code == 422
    assert '/etc/passwd' not in r.text


def test_rag_rate_limit_is_enforced(rag):
    for _ in range(10):
        assert put(rag).status_code == 200
    response = put(rag)
    assert response.status_code == 429
    assert 'retry-after' in response.headers


def test_audit_failure_rolls_back_and_prevents_disclosure(rag, monkeypatch):
    assert put(rag).status_code == 200
    headers = rag.headers()
    def broken(event):
        raise RuntimeError('PRIVATE_AUDIT_DATA')
    monkeypatch.setattr(rag.app.state.runtime.audit_sink, 'write', broken)
    for path, body in [('/documents/policy', {'text': 'Replacement secret.'}), ('/query', {'query': 'interest'})]:
        method = rag.client.put if path.startswith('/documents') else rag.client.post
        r = method('/api/v1/rag' + path, json=body, headers=headers)
        assert r.status_code == 500
        assert 'PRIVATE_AUDIT_DATA' not in r.text and 'monthly' not in r.text
    with rag.db.session() as session:
        assert 'monthly' in session.scalar(select(RagChunk.text))


def test_database_failure_sanitized_and_rolls_back(rag, monkeypatch):
    assert put(rag).status_code == 200
    original = PostgresVectorStore.replace_source
    def broken(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise OperationalError('PRIVATE_SQL', {}, Exception('PRIVATE_PASSWORD'))
    monkeypatch.setattr(PostgresVectorStore, 'replace_source', broken)
    r = put(rag, value='New replacement content.')
    assert r.status_code == 503 and 'PRIVATE' not in r.text
    with rag.db.session() as session:
        assert 'monthly' in session.scalar(select(RagChunk.text))


def test_arabic_answer_and_irrelevant_query_abstention(rag):
    assert put(rag, value='حساب التوفير يتيح سحب الأموال يومياً.').status_code == 200
    result = ask(rag, 'حساب التوفير').json()
    assert 'سحب الأموال' in result['answer'] and result['citations']
    assert ask(rag, 'astronaut telescope galaxies').json()['citations'] == []


def test_no_content_or_query_in_logs_or_audit(rag, caplog):
    assert put(rag, value='SECRET_KNOWLEDGE Savings interest is monthly.').status_code == 200
    assert ask(rag, 'SECRET_QUERY interest').status_code == 200
    assert 'SECRET_KNOWLEDGE' not in caplog.text and 'SECRET_QUERY' not in caplog.text
    for path in rag.root.rglob('*.jsonl'):
        data = path.read_text()
        assert 'SECRET_KNOWLEDGE' not in data and 'SECRET_QUERY' not in data


def test_concurrent_replacement_has_one_complete_document(rag):
    headers = [rag.headers(), rag.headers()]
    def submit(i):
        return rag.client.put('/api/v1/rag/documents/policy', json={'text': f'Interest version {i}.'}, headers=headers[i])
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json()['id'] == responses[1].json()['id']
    with rag.db.session() as session:
        assert session.scalar(select(func.count()).select_from(RagChunk)) == 1


def test_database_constraints_reject_invalid_vectors(rag):
    assert put(rag).status_code == 200
    for vector in ([0.0], [float('nan')] * 384, [float('inf')] * 384, [None] * 384):
        with pytest.raises(IntegrityError), rag.db.session() as session, session.begin():
            chunk = session.scalar(select(RagChunk))
            chunk.embedding = vector


@pytest.mark.parametrize('value', ['national ID 29901010101010', 'password=private-value', 'text\x00invalid'])
def test_sensitive_patterns_and_nul_rejected_before_storage(rag, value):
    r = put(rag, value=value)
    assert r.status_code == 422
    assert value not in r.text
    with rag.db.session() as session:
        assert session.scalar(select(func.count()).select_from(RagDocument)) == 0


def test_revoked_token_cannot_query(rag):
    headers = rag.headers()
    assert rag.client.post('/api/v1/auth/logout', headers=headers).status_code == 204
    assert rag.client.post('/api/v1/rag/query', json={'query': 'interest'}, headers=headers).status_code == 401


def test_api_only_has_no_customer_website(rag):
    for path in ['/', '/ui/app.js', '/ui/style.css', '/ui/../../configs/app.yaml']:
        assert rag.client.get(path).status_code == 404
    assert rag.client.get('/docs').status_code == 200
    assert rag.client.get('/health/live').status_code == 200
    assert rag.client.get('/api/v1/rag/documents').status_code == 401


def test_policy_import_is_idempotent_and_shared_without_private_leak(rag, tmp_path):
    from bankwise.rag.import_policies import import_policies
    folder = tmp_path / 'policies'
    folder.mkdir()
    (folder / 'loans.md').write_text('Loan applications require review.', encoding='utf8')
    assert import_policies(rag.db, rag.app.state.runtime, folder) == 1
    assert import_policies(rag.db, rag.app.state.runtime, folder) == 0
    assert put(rag, value='Private savings information.').status_code == 200
    with rag.db.session() as session:
        shared = PostgresVectorStore(session, rag.users['bob'].subject_id, shared_only=True).all()
        assert len(shared) == 1 and shared[0].chunk.source == 'loans'
    (folder / 'loans.md').write_text('Loan applications require updated review.', encoding='utf8')
    assert import_policies(rag.db, rag.app.state.runtime, folder) == 1


def test_customer_reads_shared_library_but_cannot_publish(rag, tmp_path):
    from bankwise.rag.import_policies import import_policies
    folder = tmp_path / 'shared'
    folder.mkdir()
    (folder / 'loans.md').write_text('Loan applications require review.', encoding='utf8')
    import_policies(rag.db, rag.app.state.runtime, folder)
    with rag.db.session() as session, session.begin():
        session.query(UserRole).filter(UserRole.user_id == UUID(rag.users['bob'].subject_id)).delete()
        session.add(UserRole(user_id=UUID(rag.users['bob'].subject_id), role='customer'))
    rag.users['bob'] = Principal(rag.users['bob'].subject_id, frozenset({'customer'}))
    assert put(rag, who='bob').status_code == 403
    response = ask(rag, 'Loan applications', who='bob')
    assert response.status_code == 200
    assert response.json()['citations'][0]['source'] == 'loans'
    assert rag.client.delete('/api/v1/rag/documents/loans', headers=rag.headers('bob')).status_code == 403


def test_semantic_route_preserves_auth_scope_and_reports_mode(rag):
    from bankwise.rag.models import RAGAnswer
    calls = []
    class Semantic:
        def answer(self, store, question, top_k):
            calls.append((str(store.owner_id), store.shared_only))
            return RAGAnswer('لا توجد معلومات كافية.', ())
    rag.app.state.semantic = Semantic()
    assert rag.client.post('/api/v1/rag/query', json={'query':'test'}).status_code == 401
    assert calls == []
    response = ask(rag)
    assert response.status_code == 200 and response.json()['mode'] == 'semantic_grounded'
    assert calls == [(rag.users['alice'].subject_id, False)]


def test_semantic_failure_is_sanitized(rag):
    from bankwise.core.errors import ServiceUnavailable
    class Semantic:
        def answer(self, *args):
            raise ServiceUnavailable('PRIVATE_MODEL_PATH')
    rag.app.state.semantic = Semantic()
    response = ask(rag)
    assert response.status_code == 503 and 'PRIVATE_MODEL_PATH' not in response.text


def test_customer_with_knowledge_role_keeps_private_documents_visible(rag):
    with rag.db.session() as session, session.begin():
        session.add(UserRole(user_id=UUID(rag.users['alice'].subject_id), role='customer'))
    rag.users['alice'] = Principal(rag.users['alice'].subject_id, rag.users['alice'].roles | {'customer'})
    assert put(rag, value='Private savings information.').status_code == 200
    response = ask(rag, 'Private savings')
    assert response.status_code == 200
    assert [c['source'] for c in response.json()['citations']] == ['policy']


def test_policy_import_can_force_reindex_and_prune(rag, tmp_path):
    from bankwise.rag.import_policies import import_policies
    folder = tmp_path / 'policies'
    folder.mkdir()
    (folder / 'loans.md').write_text('Loan review one.', encoding='utf8')
    (folder / 'cards.md').write_text('Card review one.', encoding='utf8')
    assert import_policies(rag.db, rag.app.state.runtime, folder) == 2
    assert import_policies(rag.db, rag.app.state.runtime, folder) == 0
    assert import_policies(rag.db, rag.app.state.runtime, folder, force=True) == 2
    (folder / 'cards.md').unlink()
    assert import_policies(rag.db, rag.app.state.runtime, folder) == 0
    with rag.db.session() as session:
        shared = PostgresVectorStore(session, rag.users['bob'].subject_id, shared_only=True).all()
        assert sorted({record.chunk.source for record in shared}) == ['cards', 'loans']
    assert import_policies(rag.db, rag.app.state.runtime, folder, prune=True) == 0
    with rag.db.session() as session:
        shared = PostgresVectorStore(session, rag.users['bob'].subject_id, shared_only=True).all()
        assert sorted({record.chunk.source for record in shared}) == ['loans']


def test_postgres_store_persists_non_default_embedding_dimension(rag):
    from bankwise.rag.service import RAGService
    with rag.db.session() as session, session.begin():
        store = PostgresVectorStore(session, rag.users['alice'].subject_id)
        service = RAGService(store, embedder=HashingEmbedder(dimensions=16))
        assert service.ingest_text('Fixed deposit interest is five percent.', 'tiny') == 1
        answer = service.answer('deposit interest', top_k=1)
        assert [citation.source for citation in answer.citations] == ['tiny']
        assert session.scalar(select(RagChunk)).dimension == 16
