"""Cosine-similarity retrieval over stored chunk vectors."""

from __future__ import annotations

import math
from typing import List

from bankwise.rag.arabic import terms
from bankwise.rag.embedder import Embedder
from bankwise.rag.errors import RagError, VectorStoreError
from bankwise.rag.models import RetrievedChunk
from bankwise.rag.store import VectorStore


class Retriever:
    def __init__(self, embedder: Embedder, store: VectorStore) -> None:
        self.embedder = embedder
        self.store = store

    def search(self, query: str, top_k: int = 5, min_score: float = 0.0) -> List[RetrievedChunk]:
        if not isinstance(query, str) or not query.strip():
            raise RagError("query must be a non-empty string")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise RagError("top_k must be a positive integer")
        if (
            isinstance(min_score, bool)
            or not isinstance(min_score, (int, float))
            or not math.isfinite(min_score)
            or min_score < -1.0
            or min_score > 1.0
        ):
            raise RagError("min_score must be between -1 and 1")
        self.store.validate_model(self.embedder.model_id)
        query_vectors = self.embedder.embed([query])
        if len(query_vectors) != 1 or not query_vectors[0]:
            raise RagError("embedder must return one non-empty vector for the query")
        try:
            query_vector = tuple(float(value) for value in query_vectors[0])
        except (TypeError, ValueError, OverflowError) as error:
            raise RagError("query embedding must contain finite numeric values") from error
        if any(not math.isfinite(value) for value in query_vector):
            raise RagError("query embedding must contain finite numeric values")
        ranked = []
        query_terms = set(terms(query))
        records = self.store.all()
        frequencies = [self._frequencies(record.chunk.text) for record in records]
        vocabularies = [set(frequency) for frequency in frequencies]
        weights = {term: 1 + math.log((len(records) + 1) / (1 + sum(term in vocab for vocab in vocabularies))) for term in query_terms}
        norm = max(1, sum(weights.values()))
        for record, frequency in zip(records, frequencies):
            if len(record.embedding) != len(query_vector):
                raise VectorStoreError("query and stored embedding dimensions do not match")
            score = self._cosine(query_vector, record.embedding)
            # Lexical evidence gates hash collisions; aliases support Arabic dialect queries.
            # Saturated term frequency distinguishes policy chunks that repeat the requested
            # subject from chunks that mention it once in passing.
            coverage = sum(weights[t] * (1.0 + math.log(frequency[t]))
                           for t in query_terms & frequency.keys()) / norm
            score = 0.75 * coverage + 0.25 * score
            if coverage > 0 and score > min_score:
                ranked.append(RetrievedChunk(record.chunk, score))
        ranked.sort(key=lambda result: (-result.score, result.chunk.source, result.chunk.chunk_id))
        return ranked[:top_k]

    @staticmethod
    def _frequencies(text: str) -> dict[str, int]:
        frequency: dict[str, int] = {}
        for term in terms(text):
            frequency[term] = frequency.get(term, 0) + 1
        return frequency

    @staticmethod
    def _cosine(left, right) -> float:
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)
