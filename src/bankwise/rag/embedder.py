"""Embedding interfaces and local embedding implementations."""

from __future__ import annotations

import hashlib
import math
import re
from typing import List, Protocol, Sequence, Tuple

from bankwise.rag.errors import RagError
from bankwise.rag.models import Vector

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


class Embedder(Protocol):
    @property
    def model_id(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> List[Vector]: ...


class HashingEmbedder:
    """Dependency-free multilingual hashing vectors for demos and small local indexes.

    This is not a substitute for a trained semantic embedding model in production.
    """

    def __init__(self, dimensions: int = 384) -> None:
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions < 16:
            raise RagError("embedding dimensions must be an integer of at least 16")
        self.dimensions = dimensions

    @property
    def model_id(self) -> str:
        return f"hashing-v1-{self.dimensions}"

    def embed(self, texts: Sequence[str]) -> List[Vector]:
        vectors: List[Vector] = []
        for text in texts:
            if not isinstance(text, str):
                raise RagError("all embedding inputs must be strings")
            vector = [0.0] * self.dimensions
            tokens = [match.group(0).casefold() for match in _TOKEN_RE.finditer(text)]
            for token in tokens:
                self._add(vector, "w:" + token, 1.0)
                if len(token) >= 3:
                    padded = f"^{token}$"
                    for index in range(len(padded) - 2):
                        self._add(vector, "c:" + padded[index : index + 3], 0.25)
            norm = math.sqrt(sum(value * value for value in vector))
            if norm:
                vector = [value / norm for value in vector]
            vectors.append(tuple(vector))
        return vectors

    def _add(self, vector: List[float], feature: str, weight: float) -> None:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest, "big") % self.dimensions
        vector[index] += weight


class SentenceTransformerEmbedder:
    """Optional adapter for a locally available Sentence Transformers model."""

    def __init__(self, model_name: str, device: str = "cpu", model=None) -> None:
        if not isinstance(model_name, str) or not model_name.strip():
            raise RagError("model_name must be a non-empty string")
        if model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as error:
                raise RagError(
                    "semantic embeddings require the optional 'sentence-transformers' package"
                ) from error
            model = SentenceTransformer(model_name, device=device, local_files_only=True)
        self._model = model
        self._model_id = f"sentence-transformers:{model_name}"

    @property
    def model_id(self) -> str:
        return self._model_id

    def embed(self, texts: Sequence[str]) -> List[Vector]:
        if any(not isinstance(text, str) for text in texts):
            raise RagError("all embedding inputs must be strings")
        encoded = self._model.encode(list(texts), normalize_embeddings=True)
        return [tuple(float(value) for value in row) for row in encoded]
