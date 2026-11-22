"""Persistent SQLite storage for chunks and their embeddings."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
from pathlib import Path
from typing import Iterable, List, Optional

from bankwise.rag.errors import VectorStoreError
from bankwise.rag.models import Chunk, Vector, VectorRecord


class SQLiteVectorStore:
    """A small-corpus vector store; similarity ranking is performed by ``Retriever``."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        if str(database_path) != ":memory:":
            self.database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._connection: Optional[sqlite3.Connection] = sqlite3.connect(
            str(database_path), check_same_thread=False
        )
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_chunks (
                chunk_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                text TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                embedding_json TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS rag_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self._connection.commit()
        if str(database_path) != ":memory:" and self.database_path.exists():
            os.chmod(self.database_path, 0o600)

    def add(self, records: Iterable[VectorRecord], model_id: str) -> int:
        return self._write(records, model_id, replace_source=None)

    def replace_source(self, source: str, records: Iterable[VectorRecord], model_id: str) -> int:
        if not isinstance(source, str) or not source:
            raise VectorStoreError("source must be a non-empty string")
        records = list(records)
        if not records:
            raise VectorStoreError("replacement records must not be empty")
        if any(record.chunk.source != source for record in records):
            raise VectorStoreError("all replacement records must belong to the requested source")
        return self._write(records, model_id, replace_source=source)

    def _write(
        self, records: Iterable[VectorRecord], model_id: str, replace_source: Optional[str]
    ) -> int:
        if not isinstance(model_id, str) or not model_id.strip():
            raise VectorStoreError("model_id must be a non-empty string")
        prepared = []
        dimension = None
        for record in records:
            vector = tuple(float(value) for value in record.embedding)
            if not vector or any(not math.isfinite(value) for value in vector):
                raise VectorStoreError("embeddings must be non-empty finite vectors")
            if dimension is None:
                dimension = len(vector)
            elif len(vector) != dimension:
                raise VectorStoreError("all embeddings in a batch must have the same dimension")
            try:
                metadata_json = json.dumps(record.chunk.metadata, ensure_ascii=False, allow_nan=False)
                embedding_json = json.dumps(vector, allow_nan=False)
            except (TypeError, ValueError) as error:
                raise VectorStoreError("chunk metadata and embeddings must be JSON serializable") from error
            if not record.chunk.chunk_id or not record.chunk.source or not record.chunk.text.strip():
                raise VectorStoreError("each record requires a chunk id, source, and non-empty text")
            prepared.append(
                (record.chunk.chunk_id, record.chunk.source, record.chunk.text, metadata_json, embedding_json)
            )

        if not prepared:
            return 0
        with self._lock:
            connection = self._require_connection()
            existing = connection.execute(
                "SELECT value FROM rag_settings WHERE key = 'embedding_dimension'"
            ).fetchone()
            stored_model = connection.execute(
                "SELECT value FROM rag_settings WHERE key = 'embedding_model'"
            ).fetchone()
            if existing and int(existing[0]) != dimension:
                raise VectorStoreError(
                    f"embedding dimension mismatch: store has {existing[0]}, received {dimension}"
                )
            if stored_model and stored_model[0] != model_id:
                raise VectorStoreError(
                    f"embedding model mismatch: store uses {stored_model[0]}, received {model_id}"
                )
            with connection:
                if not existing:
                    connection.execute(
                        "INSERT INTO rag_settings (key, value) VALUES ('embedding_dimension', ?)",
                        (str(dimension),),
                    )
                if not stored_model:
                    connection.execute(
                        "INSERT INTO rag_settings (key, value) VALUES ('embedding_model', ?)",
                        (model_id,),
                    )
                if replace_source is not None:
                    connection.execute("DELETE FROM rag_chunks WHERE source = ?", (replace_source,))
                connection.executemany(
                    """
                    INSERT INTO rag_chunks (chunk_id, source, text, metadata_json, embedding_json)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_id) DO UPDATE SET
                        source = excluded.source,
                        text = excluded.text,
                        metadata_json = excluded.metadata_json,
                        embedding_json = excluded.embedding_json
                    """,
                    prepared,
                )
            return len(prepared)

    def validate_model(self, model_id: str) -> None:
        with self._lock:
            row = self._require_connection().execute(
                "SELECT value FROM rag_settings WHERE key = 'embedding_model'"
            ).fetchone()
        if row and row[0] != model_id:
            raise VectorStoreError(
                f"embedding model mismatch: store uses {row[0]}, query uses {model_id}"
            )

    def all(self) -> List[VectorRecord]:
        with self._lock:
            rows = self._require_connection().execute(
                """
                SELECT chunk_id, source, text, metadata_json, embedding_json
                FROM rag_chunks ORDER BY source, chunk_id
                """
            ).fetchall()
        records = []
        for chunk_id, source, text, metadata_json, embedding_json in rows:
            try:
                metadata = json.loads(metadata_json)
                embedding = tuple(float(value) for value in json.loads(embedding_json))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise VectorStoreError(f"invalid stored record for chunk {chunk_id}") from error
            records.append(VectorRecord(Chunk(chunk_id, source, text, metadata), embedding))
        return records

    def delete_source(self, source: str) -> int:
        if not isinstance(source, str) or not source:
            raise VectorStoreError("source must be a non-empty string")
        with self._lock:
            connection = self._require_connection()
            with connection:
                cursor = connection.execute("DELETE FROM rag_chunks WHERE source = ?", (source,))
            return cursor.rowcount

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def __enter__(self) -> "SQLiteVectorStore":
        self._require_connection()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise VectorStoreError("vector store is closed")
        return self._connection
