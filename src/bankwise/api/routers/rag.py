"""Private knowledge API; shared security gates precede all RAG processing."""
from typing import Annotated
import json
from pathlib import Path
from uuid import UUID
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from bankwise.api.deps import authorization, bearer_scheme, client_key, get_security, get_session
from bankwise.core.errors import InvalidInputError
from bankwise.core.pii import redact_text
from bankwise.rag.errors import RagError
from bankwise.rag.postgres_store import PostgresVectorStore
from bankwise.rag.service import RAGService

Source = Annotated[str, StringConstraints(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$', max_length=80)]


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=8000)


class QueryInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=10, strict=True)


class DocumentResult(BaseModel):
    id: UUID
    source: str
    chunks: int


class DocumentSummary(BaseModel):
    id: UUID
    source: str


class CitationResult(BaseModel):
    source: str
    chunk_id: str
    score: float
    excerpt: str
    url: str | None = None
    locator: str | None = None
    reviewed_at: str | None = None


class AnswerResult(BaseModel):
    answer: str
    citations: list[CitationResult]
    mode: str = 'extractive'


def principal_for(permission, policy):
    def dependency(request: Request, security=Depends(get_security)):
        return security.guard(authorization(request), permission, client_key(request), rate_policy=policy)
    return dependency


read_principal = principal_for('rag:read', 'rag_read')
write_principal = principal_for('rag:write', 'rag_write')
router = APIRouter(prefix='/api/v1/rag', tags=['rag'], dependencies=[Depends(bearer_scheme)])


@router.put('/documents/{source}', response_model=DocumentResult)
def ingest(source: Source, body: DocumentInput, principal=Depends(write_principal),
           session=Depends(get_session), security=Depends(get_security)):
    if "\x00" in body.text or redact_text(body.text) != body.text or redact_text(source) != source:
        raise InvalidInputError()
    store = PostgresVectorStore(session, principal.subject_id)
    try:
        count = RAGService(store).ingest_text(body.text, source)
    except RagError:
        raise InvalidInputError() from None
    document = next(d for d in store.documents() if d.source == source)
    # An authorization record, not a claim that the subsequent transaction committed.
    security.record_access('rag.write_authorized', principal.subject_id)
    session.commit()
    return DocumentResult(id=document.id, source=source, chunks=count)


@router.get('/documents', response_model=list[DocumentSummary])
def documents(principal=Depends(read_principal), session=Depends(get_session), security=Depends(get_security)):
    rows = PostgresVectorStore(session, principal.subject_id).documents()
    security.record_access('rag.list', principal.subject_id)
    return [DocumentSummary(id=d.id, source=d.source) for d in rows]


@router.delete('/documents/{source}', status_code=204)
def remove(source: Source, principal=Depends(write_principal), session=Depends(get_session), security=Depends(get_security)):
    PostgresVectorStore(session, principal.subject_id).delete_source(source)
    security.record_access('rag.delete_authorized', principal.subject_id)
    session.commit()
    return Response(status_code=204)


def source_metadata(source):
    path = Path('configs/cib_sources.json')
    if not path.is_file(): return {}
    data = json.loads(path.read_text())['documents'].get(source, {})
    return {key: data[key] for key in ('url', 'locator', 'reviewed_at') if key in data}


@router.get('/catalog')
def catalog(principal=Depends(read_principal)):
    path = Path('configs/cib_sources.json')
    return json.loads(path.read_text()) if path.is_file() else {'documents': {}}


def shared_library_only(principal) -> bool:
    """Customers see only the shared policy library; any knowledge role keeps private scope."""
    return 'customer' in principal.roles and 'knowledge_user' not in principal.roles


@router.post('/query', response_model=AnswerResult)
def query(body: QueryInput, request: Request, principal=Depends(read_principal), session=Depends(get_session), security=Depends(get_security)):
    try:
        store = PostgresVectorStore(session, principal.subject_id, shared_only=shared_library_only(principal))
        semantic = request.app.state.semantic
        if semantic is not None:
            result = semantic.answer(store, body.query, body.top_k)
        else:
            result = RAGService(store).answer(body.query, body.top_k, min_score=0.15)
    except RagError:
        raise InvalidInputError() from None
    security.record_access('rag.query', principal.subject_id)
    return AnswerResult(mode="semantic_grounded" if semantic is not None else "extractive", answer=redact_text(result.answer), citations=[CitationResult(source=c.source, chunk_id=c.chunk_id,
                        score=c.score, excerpt=redact_text(c.excerpt), **(source_metadata(c.source) if shared_library_only(principal) else {})) for c in result.citations])
