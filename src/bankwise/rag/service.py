"""Orchestrate document ingestion, retrieval, and grounded answers."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from bankwise.rag.chunker import TextChunker
from bankwise.rag.embedder import Embedder, HashingEmbedder
from bankwise.rag.generator import ExtractiveGenerator, Generator
from bankwise.rag.models import Document, RAGAnswer, VectorRecord
from bankwise.rag.parser import DocumentParser
from bankwise.rag.retriever import Retriever
from bankwise.rag.store import VectorStore


class RAGService:
    def __init__(
        self,
        store: VectorStore,
        parser: Optional[DocumentParser] = None,
        chunker: Optional[TextChunker] = None,
        embedder: Optional[Embedder] = None,
        generator: Optional[Generator] = None,
    ) -> None:
        self.store = store
        self.parser = parser or DocumentParser()
        self.chunker = chunker or TextChunker()
        self.embedder = embedder or HashingEmbedder()
        self.generator = generator or ExtractiveGenerator()
        self.retriever = Retriever(self.embedder, self.store)

    def ingest_file(self, path: Path) -> int:
        return self.ingest(self.parser.parse_file(path))

    def ingest_text(self, text: str, source: str = "text") -> int:
        return self.ingest(self.parser.parse_text(text, source))

    def ingest(self, document: Document) -> int:
        chunks = self.chunker.split(document)
        embeddings = self.embedder.embed([chunk.text for chunk in chunks])
        if len(embeddings) != len(chunks):
            raise ValueError("embedder must return one vector for each chunk")
        return self.store.replace_source(
            document.source,
            (VectorRecord(chunk, embedding) for chunk, embedding in zip(chunks, embeddings)),
            model_id=self.embedder.model_id,
        )

    def answer(self, query: str, top_k: int = 5, min_score: float = 0.0) -> RAGAnswer:
        passages = self.retriever.search(query, top_k=top_k, min_score=min_score)
        return self.generator.generate(query, passages)

    def delete_source(self, source: str) -> int:
        return self.store.delete_source(source)
