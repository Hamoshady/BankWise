"""Request-scoped PostgreSQL store. Caller owns commit/rollback and audit gates."""
import math
from uuid import UUID
from sqlalchemy import delete, func, select
from bankwise.core.errors import Conflict, NotFound
from bankwise.database.models import RagChunk, RagDocument, User
from bankwise.rag.errors import VectorStoreError
from bankwise.rag.models import Chunk, VectorRecord


class PostgresVectorStore:
    def __init__(self, session, owner_id, shared_only=False):
        self.shared_only = shared_only
        self.session = session
        self.owner_id = UUID(str(owner_id))

    def _lock(self):
        # Serialize mutations for one owner, including creation of a first document.
        if self.session.scalar(select(User.id).where(User.id == self.owner_id).with_for_update()) is None:
            raise NotFound()

    def documents(self):
        return list(self.session.scalars(select(RagDocument).where(
            RagDocument.owner_id == self.owner_id).order_by(RagDocument.source)))

    def validate_model(self, model_id):
        if (not isinstance(model_id, str) or not model_id.strip()
                or model_id != model_id.strip() or len(model_id) > 80):
            raise VectorStoreError('unsupported embedding model')
        if any(row.model_id != model_id for row in self.documents()):
            raise VectorStoreError('stored model mismatch')

    def all(self):
        rows = self.session.execute(select(RagChunk, RagDocument.source).join(RagDocument).where(
            (RagDocument.shared.is_(True) if self.shared_only else RagDocument.owner_id == self.owner_id)).order_by(RagDocument.source, RagChunk.chunk_id).limit(1001)).all()
        if len(rows) > 1000:
            raise Conflict()
        return [VectorRecord(Chunk(c.chunk_id, source, c.text), tuple(c.embedding)) for c, source in rows]

    def replace_source(self, source, records, model_id):
        self._lock()
        self.validate_model(model_id)
        records = list(records)
        if not records or len(records) > 100:
            raise Conflict()
        dimensions = {len(r.embedding) for r in records}
        if len(dimensions) != 1:
            raise VectorStoreError('invalid vector batch')
        dimension = dimensions.pop()
        if not 16 <= dimension <= 4096:
            raise VectorStoreError('invalid vector batch')
        for r in records:
            if r.chunk.source != source or any(not math.isfinite(v) for v in r.embedding):
                raise VectorStoreError('invalid vector batch')
        documents = self.documents()
        stored_dimension = self.session.scalar(select(RagChunk.dimension).join(RagDocument).where(
            RagDocument.owner_id == self.owner_id).limit(1))
        if stored_dimension is not None and stored_dimension != dimension:
            raise VectorStoreError('stored embedding dimension mismatch')
        document = next((d for d in documents if d.source == source), None)
        if document is None:
            if len(documents) >= 100:
                raise Conflict()
            document = RagDocument(owner_id=self.owner_id, source=source, model_id=model_id)
            self.session.add(document)
            self.session.flush()
        self.session.execute(delete(RagChunk).where(RagChunk.document_id == document.id))
        count = self.session.scalar(select(func.count()).select_from(RagChunk).join(RagDocument).where(
            RagDocument.owner_id == self.owner_id))
        if count + len(records) > 1000:
            raise Conflict()
        document.updated_at = func.now()
        self.session.add_all([RagChunk(document_id=document.id, chunk_id=r.chunk.chunk_id,
                                      text=r.chunk.text, embedding=list(r.embedding),
                                      dimension=dimension) for r in records])
        self.session.flush()
        return len(records)

    def delete_source(self, source):
        self._lock()
        document = self.session.scalar(select(RagDocument).where(
            RagDocument.owner_id == self.owner_id, RagDocument.source == source))
        if document is None:
            raise NotFound()
        count = self.session.scalar(select(func.count()).select_from(RagChunk).where(RagChunk.document_id == document.id))
        self.session.delete(document)
        self.session.flush()
        return count
