"""Shared data types for the RAG pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

Vector = Tuple[float, ...]


@dataclass(frozen=True)
class Document:
    source: str
    text: str
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    source: str
    text: str
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class VectorRecord:
    chunk: Chunk
    embedding: Vector


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    score: float


@dataclass(frozen=True)
class Citation:
    source: str
    chunk_id: str
    score: float
    excerpt: str


@dataclass(frozen=True)
class RAGAnswer:
    answer: str
    citations: Tuple[Citation, ...]
