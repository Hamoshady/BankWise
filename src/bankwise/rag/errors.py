"""Errors raised by the RAG pipeline."""


class RagError(ValueError):
    """Invalid input or an unavailable RAG dependency."""


class VectorStoreError(RuntimeError):
    """The vector store cannot safely read or write its records."""
