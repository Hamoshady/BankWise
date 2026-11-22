"""Local retrieval-augmented generation building blocks."""

from bankwise.rag.chunker import TextChunker
from bankwise.rag.embedder import HashingEmbedder, SentenceTransformerEmbedder
from bankwise.rag.generator import ExtractiveGenerator
from bankwise.rag.parser import DocumentParser
from bankwise.rag.retriever import Retriever
from bankwise.rag.service import RAGService
from bankwise.rag.vector_store import SQLiteVectorStore

__all__ = [
    "DocumentParser",
    "ExtractiveGenerator",
    "HashingEmbedder",
    "RAGService",
    "Retriever",
    "SQLiteVectorStore",
    "SentenceTransformerEmbedder",
    "TextChunker",
]
