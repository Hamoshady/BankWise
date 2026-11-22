import pytest

from bankwise.rag.chunker import TextChunker
from bankwise.rag.embedder import HashingEmbedder
from bankwise.rag.errors import RagError, VectorStoreError
from bankwise.rag.models import Document
from bankwise.rag.parser import DocumentParser
from bankwise.rag.service import RAGService
from bankwise.rag.vector_store import SQLiteVectorStore


def test_parser_reads_utf8_text_and_rejects_unsupported_or_empty_files(tmp_path):
    parser = DocumentParser()
    text_file = tmp_path / "policy.txt"
    text_file.write_text("\ufeffحساب التوفير متاح للعملاء.", encoding="utf-8")
    document = parser.parse_file(text_file)
    assert document.source == "policy.txt"
    assert document.text == "حساب التوفير متاح للعملاء."
    assert document.metadata == {"file_type": "txt"}

    unsupported = tmp_path / "policy.docx"
    unsupported.write_text("not parsed", encoding="utf-8")
    with pytest.raises(RagError, match="unsupported"):
        parser.parse_file(unsupported)
    with pytest.raises(RagError, match="non-empty"):
        parser.parse_text("  ")


def test_chunker_obeys_size_and_overlap_settings_and_creates_stable_ids():
    document = Document("source.md", "one two three four five six seven eight nine ten")
    chunker = TextChunker(chunk_size=14, overlap=3)
    chunks = chunker.split(document)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 14 for chunk in chunks)
    assert chunks == chunker.split(document)
    assert all(chunk.source == "source.md" for chunk in chunks)
    with pytest.raises(RagError):
        TextChunker(chunk_size=10, overlap=10)


def test_hashing_embedder_returns_normalized_vectors_and_supports_empty_text():
    embedder = HashingEmbedder(dimensions=64)
    vectors = embedder.embed(["savings account interest", "savings account interest", ""])
    assert vectors[0] == vectors[1]
    assert sum(value * value for value in vectors[0]) == pytest.approx(1.0)
    assert vectors[2] == (0.0,) * 64


def test_rag_service_ingests_retrieves_persists_and_cites_source(tmp_path):
    database = tmp_path / "private" / "rag.sqlite3"
    store = SQLiteVectorStore(database)
    service = RAGService(store, chunker=TextChunker(chunk_size=160, overlap=20))
    count = service.ingest_text(
        "Savings accounts pay interest every month. Customers can withdraw their funds.",
        source="savings-policy.txt",
    )
    assert count == 1

    answer = service.answer("When is interest paid?")
    assert "every month" in answer.answer
    assert answer.citations[0].source == "savings-policy.txt"
    assert database.stat().st_mode & 0o777 == 0o600

    store.close()
    reopened = SQLiteVectorStore(database)
    try:
        assert len(reopened.all()) == 1
        assert RAGService(reopened).answer("When is interest paid?").citations
        assert reopened.delete_source("savings-policy.txt") == 1
        assert reopened.all() == []
    finally:
        reopened.close()


def test_rag_service_abstains_when_no_documents_match(tmp_path):
    with SQLiteVectorStore(tmp_path / "rag.sqlite3") as store:
        answer = RAGService(store).answer("What is the loan rate?")
        assert answer.answer == "I could not find an answer in the available documents."
        assert answer.citations == ()


def test_reingesting_a_source_replaces_its_old_chunks(tmp_path):
    with SQLiteVectorStore(tmp_path / "rag.sqlite3") as store:
        service = RAGService(store)
        service.ingest_text("Legacy product has a silver card.", source="catalog.txt")
        service.ingest_text("Current product has a blue card.", source="catalog.txt")
        records = store.all()
        assert len(records) == 1
        assert "Current" in records[0].chunk.text
        assert "Legacy" not in records[0].chunk.text


def test_vector_store_rejects_embedding_dimension_mismatch(tmp_path):
    store = SQLiteVectorStore(tmp_path / "rag.sqlite3")
    try:
        service = RAGService(store, embedder=HashingEmbedder(dimensions=32))
        service.ingest_text("A document about deposits.", source="deposits.txt")
        other_service = RAGService(store, embedder=HashingEmbedder(dimensions=64))
        with pytest.raises(VectorStoreError, match="dimension mismatch"):
            other_service.ingest_text("Another document.", source="other.txt")
    finally:
        store.close()


def test_vector_store_rejects_mixing_embedding_models_with_same_dimension(tmp_path):
    store = SQLiteVectorStore(tmp_path / "rag.sqlite3")
    try:
        first = RAGService(store, embedder=HashingEmbedder(dimensions=32))
        first.ingest_text("A document about deposits.", source="deposits.txt")

        class AnotherEmbedder(HashingEmbedder):
            @property
            def model_id(self):
                return "another-model-32"

        second = RAGService(store, embedder=AnotherEmbedder(dimensions=32))
        with pytest.raises(VectorStoreError, match="embedding model mismatch"):
            second.answer("What are deposits?")
    finally:
        store.close()


def test_repeated_passage_sentences_reuse_the_same_citation():
    from bankwise.rag.generator import ExtractiveGenerator
    from bankwise.rag.models import Chunk, RetrievedChunk
    passages = [RetrievedChunk(Chunk('a', 'policy', 'Interest is monthly. Interest is taxable.'), .8),
                RetrievedChunk(Chunk('b', 'terms', 'Interest requires a deposit.'), .7)]
    result = ExtractiveGenerator().generate('Interest', passages)
    assert result.answer.count('[1]') == 2
    assert result.answer.count('[2]') == 1
    assert '[3]' not in result.answer
    assert len(result.citations) == 2
