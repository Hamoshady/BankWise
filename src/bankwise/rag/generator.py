"""Grounded extractive answer generation with explicit citations."""

from __future__ import annotations

import re
from typing import List, Protocol, Sequence, Tuple

from bankwise.rag.arabic import terms as normalized_terms
from bankwise.rag.models import Citation, RAGAnswer, RetrievedChunk

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_SENTENCE_RE = re.compile(r"(?<=[.!?؟])\s+|\n+")
_ARABIC_RE = re.compile(r"[\u0600-\u06ff]")


class Generator(Protocol):
    def generate(self, query: str, passages: Sequence[RetrievedChunk]) -> RAGAnswer: ...


class ExtractiveGenerator:
    """Return relevant source sentences without inventing facts or executing document instructions."""

    def __init__(self, max_sentences: int = 3, max_excerpt_chars: int = 500) -> None:
        if isinstance(max_sentences, bool) or not isinstance(max_sentences, int) or max_sentences < 1:
            raise ValueError("max_sentences must be a positive integer")
        if isinstance(max_excerpt_chars, bool) or not isinstance(max_excerpt_chars, int) or max_excerpt_chars < 1:
            raise ValueError("max_excerpt_chars must be a positive integer")
        self.max_sentences = max_sentences
        self.max_excerpt_chars = max_excerpt_chars

    def generate(self, query: str, passages: Sequence[RetrievedChunk]) -> RAGAnswer:
        if not passages:
            fallback = "لم أجد إجابة في المستندات المتاحة." if _ARABIC_RE.search(query) else (
                "I could not find an answer in the available documents."
            )
            return RAGAnswer(fallback, ())

        query_terms = set(normalized_terms(query))
        candidates: List[Tuple[float, int, str]] = []
        for passage_index, passage in enumerate(passages):
            sentences = [sentence.strip() for sentence in _SENTENCE_RE.split(passage.chunk.text) if sentence.strip()]
            if not sentences:
                sentences = [passage.chunk.text.strip()]
            for sentence_index, sentence in enumerate(sentences):
                if sentence.startswith("#"):
                    continue
                terms = set(normalized_terms(sentence))
                overlap = len(query_terms & terms) / max(1, len(query_terms))
                if overlap > 0:
                    candidates.append((overlap, passage_index, sentence))

        if not candidates:
            return self.generate(query, ())

        candidates.sort(key=lambda item: (-item[0], item[1]))
        selected: List[Tuple[int, str]] = []
        seen = set()
        for _, passage_index, sentence in candidates:
            normalized = sentence.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            selected.append((passage_index, sentence))
            if len(selected) == self.max_sentences:
                break

        answer_parts = []
        citations = []
        cited_passages = {}
        for passage_index, sentence in selected:
            passage = passages[passage_index]
            excerpt = sentence[: self.max_excerpt_chars]
            if passage_index not in cited_passages:
                citations.append(
                    Citation(passage.chunk.source, passage.chunk.chunk_id, passage.score, excerpt)
                )
                cited_passages[passage_index] = len(citations)
            answer_parts.append(f"{excerpt} [{cited_passages[passage_index]}]")
        return RAGAnswer(" ".join(answer_parts), tuple(citations))
