"""Small deterministic regression benchmark, not a production accuracy estimate."""
import json
from pathlib import Path
from bankwise.rag.service import RAGService
from bankwise.rag.vector_store import SQLiteVectorStore
root = Path(__file__).resolve().parents[1]
cases = json.loads((root / 'evaluation/rag/arabic_cases.json').read_text())
with SQLiteVectorStore(':memory:') as store:
    service = RAGService(store)
    for path in sorted((root / 'knowledge/policies').glob('*.md')):
        service.ingest_text(path.read_text(), path.stem)
    correct = 0
    for case in cases:
        result = service.answer(case['query'], top_k=1, min_score=.15)
        actual = result.citations[0].source if result.citations else None
        matched = actual == case['source']
        correct += matched
        print(json.dumps(dict(case, actual=actual, passed=matched), ensure_ascii=False))
    print(f'{correct}/{len(cases)} cases passed')
    raise SystemExit(0 if correct == len(cases) else 1)
