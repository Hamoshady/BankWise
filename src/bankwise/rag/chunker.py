"""Split source documents into stable, overlapping text chunks."""

from __future__ import annotations

import hashlib
import re
from typing import List

from bankwise.rag.errors import RagError
from bankwise.rag.models import Chunk, Document


class TextChunker:
    def __init__(self, chunk_size: int = 1000, overlap: int = 150) -> None:
        if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size < 1:
            raise RagError("chunk_size must be a positive integer")
        if isinstance(overlap, bool) or not isinstance(overlap, int) or overlap < 0:
            raise RagError("overlap must be a non-negative integer")
        if overlap >= chunk_size:
            raise RagError("overlap must be smaller than chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap

    def split(self, document: Document) -> List[Chunk]:
        if not document.text.strip():
            raise RagError("cannot chunk an empty document")
        text = document.text.strip()
        chunks: List[Chunk] = []
        start = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            if end < len(text):
                # Prefer a complete sentence/paragraph over cutting its final word.
                boundaries = [m.end() for m in re.finditer(r"(?<=[.!?؟])\s+|\n+", text[start:end])]
                if boundaries:
                    end = start + boundaries[-1]
                else:
                    boundary = text.rfind(" ", start, end)
                    if boundary > start:
                        end = boundary
            piece = text[start:end].strip()
            if piece:
                index = len(chunks)
                identity = f"{document.source}\0{index}\0{piece}".encode("utf-8")
                chunk_id = hashlib.sha256(identity).hexdigest()
                chunks.append(Chunk(chunk_id, document.source, piece, dict(document.metadata)))
            if end >= len(text):
                break
            target = end - self.overlap
            boundaries = [start + m.end() for m in re.finditer(
                r"(?<=[.!?؟])\s+|\n+", text[start:end]) if start + m.end() <= target]
            if boundaries:
                start = boundaries[-1]
            elif end - start < self.chunk_size:
                start = end
            else:
                # A single oversized sentence still needs bounded chunks. Start at
                # a word boundary when possible, never in the middle of a word.
                boundary = text.find(" ", max(start + 1, target), end)
                start = boundary + 1 if boundary >= 0 else end

        return chunks
