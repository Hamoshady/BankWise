"""Owner-private knowledge text only; identity/OCR data is never ingested here."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, DOUBLE_PRECISION
from sqlalchemy.orm import Mapped, mapped_column
from bankwise.core.identifiers import new_opaque_uuid
from bankwise.database.models import Base


class RagDocument(Base):
    __tablename__ = "rag_documents"
    __table_args__ = (
        UniqueConstraint('owner_id', 'source', name='uq_rag_owner_source'),
        CheckConstraint("source ~ '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$'", name='ck_rag_source'),
        CheckConstraint("char_length(model_id) BETWEEN 1 AND 80 AND model_id = btrim(model_id)", name='ck_rag_model'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_opaque_uuid)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    shared: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", default=False)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    model_id: Mapped[str] = mapped_column(String(80), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class RagChunk(Base):
    __tablename__ = 'rag_document_chunks'
    __table_args__ = (
        CheckConstraint('char_length(text) BETWEEN 1 AND 1000', name='ck_rag_text'),
        CheckConstraint('array_ndims(embedding) = 1 AND cardinality(embedding) = dimension AND array_position(embedding, NULL) IS NULL', name='ck_rag_vector'),
        CheckConstraint("NOT (embedding && ARRAY['NaN'::float8, 'Infinity'::float8, '-Infinity'::float8])", name='ck_rag_vector_finite'),
        CheckConstraint('dimension BETWEEN 16 AND 4096', name='ck_rag_dimension'),
    )
    document_id: Mapped[UUID] = mapped_column(ForeignKey('rag_documents.id', ondelete='CASCADE'), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(ARRAY(DOUBLE_PRECISION), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
