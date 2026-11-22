"""Keep published fictional policies ingestible without personal data."""
from pathlib import Path
import pytest
from bankwise.core.pii import redact_text

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('path', sorted((ROOT / 'knowledge/policies').glob('*.md')), ids=lambda p: p.stem)
def test_policy_is_bounded_nonpersonal_and_explicitly_fictional(path):
    text = path.read_text()
    assert path.stat().st_size <= 16384
    assert len(text) <= 8000
    assert redact_text(text) == text
    assert 'بنك افتراضي' in text
    assert 'ليست سياسة بنك حقيقي' in text
    assert 'الاستفسار المحلي عن السياسات لا يحتاج تسجيل دخول' in text
    assert '\x00' not in text


def test_policy_chunking_does_not_cut_sentences_or_decimal_numbers():
    import re
    from bankwise.rag.chunker import TextChunker
    from bankwise.rag.models import Document
    sentences = ['رسوم الخدمة 0 جنيه مصري.', 'معدل الاختبار 0.01 شهريًا.',
                 'لا يمكن إغلاق الحساب مع قرض قائم.'] * 8
    text = ' '.join(sentences)
    chunks = TextChunker(chunk_size=140, overlap=30).split(Document('synthetic', text))
    assert all(len(chunk.text) <= 140 for chunk in chunks)
    for chunk in chunks:
        assert all(s in sentences for s in re.split(r'(?<=[.!?؟])\s+', chunk.text))
    assert all(any(sentence in chunk.text for chunk in chunks) for sentence in sentences)
