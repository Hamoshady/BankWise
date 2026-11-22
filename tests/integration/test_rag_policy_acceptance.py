"""Acceptance on actual fictional policies; requires local E5 and Qwen weights."""
import json
import os
from pathlib import Path
import re

import pytest
from bankwise.rag.semantic import SemanticRuntime, EMPTY
from bankwise.rag.service import RAGService
from bankwise.rag.vector_store import SQLiteVectorStore

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / 'evaluation/rag/policy_acceptance_cases.json').read_text())
pytestmark = pytest.mark.skipif(os.environ.get('BANKWISE_TEST_SEMANTIC') != '1', reason='requires local pretrained E5/Qwen models')


@pytest.fixture(scope='module')
def policies():
    runtime = SemanticRuntime(os.environ.get('BANKWISE_RAG_MODEL_DIR', '/rag-models'))
    try:
        with SQLiteVectorStore(':memory:') as store:
            service = RAGService(store)
            for path in sorted((ROOT / 'knowledge/policies').glob('*.md')):
                service.ingest_text(path.read_text(), path.stem)
            yield runtime, store
    finally:
        runtime.close()


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['id'])
def test_arabic_policy_answer(policies, case):
    runtime, store = policies
    answer = runtime.answer(store, case['query'], 5)
    if case['source'] is None:
        assert not answer.citations, answer.answer
        assert answer.answer == EMPTY
        return
    assert case['source'] in [c.source for c in answer.citations], answer.answer
    for expected in case['expected']:
        assert expected in answer.answer, answer.answer
    # Check answer content itself, not merely that a source name was returned.
    for part in answer.answer.split('\n\n'):
        match = re.fullmatch(r'(.+) \[(\d+)\]', part)
        assert match, part
        reference = int(match[2])
        assert 1 <= reference <= len(answer.citations)
        assert match[1] in answer.citations[reference - 1].excerpt
    records = store.all()
    for citation in answer.citations:
        assert any(citation.source == r.chunk.source and citation.chunk_id == r.chunk.chunk_id
                   and citation.excerpt == r.chunk.text for r in records)
