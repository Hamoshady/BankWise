"""Review real local RAG answers; outputs JSONL without changing policies or database."""
import hashlib
import json
from pathlib import Path
import re
import time
from bankwise.rag.semantic import SemanticRuntime, EMPTY
from bankwise.rag.service import RAGService
from bankwise.rag.vector_store import SQLiteVectorStore

ROOT = Path(__file__).resolve().parents[1]


def main():
    cases = []
    for name in ('policy_acceptance_cases.json', 'review_cases.json'):
        cases.extend(json.loads((ROOT / 'evaluation/rag' / name).read_text()))
    runtime = SemanticRuntime('/rag-models')
    passed = 0
    try:
        with SQLiteVectorStore(':memory:') as store:
            service = RAGService(store)
            hashes = {}
            for path in sorted((ROOT / 'knowledge/policies').glob('*.md')):
                hashes[path.stem] = hashlib.sha256(path.read_bytes()).hexdigest()
                service.ingest_text(path.read_text(), path.stem)
            print(json.dumps({'type': 'configuration', 'models': runtime.manifest,
                              'policy_hashes': hashes, 'threshold': runtime.threshold}, ensure_ascii=False), flush=True)
            records = store.all()
            for case in cases:
                started = time.monotonic()
                result = runtime.answer(store, case['query'], 5)
                sources = [c.source for c in result.citations]
                issues = []
                if case['source'] is None:
                    if result.citations or result.answer != EMPTY:
                        issues.append('expected_abstention')
                elif case['source'] not in sources:
                    issues.append('wrong_or_missing_source')
                for expected in case.get('expected', []):
                    if expected not in result.answer:
                        issues.append('missing_fact: ' + expected)
                for citation in result.citations:
                    if not any(citation.source == r.chunk.source and citation.chunk_id == r.chunk.chunk_id
                               and citation.excerpt == r.chunk.text for r in records):
                        issues.append('invalid_citation')
                if result.citations:
                    for part in result.answer.split('\n\n'):
                        match = re.fullmatch(r'(.+) \[(\d+)\]', part)
                        if not match or not (1 <= int(match[2]) <= len(result.citations)):
                            issues.append('invalid_reference')
                        elif match[1] not in result.citations[int(match[2]) - 1].excerpt:
                            issues.append('unsupported_answer_text')
                passed += not issues
                print(json.dumps(dict(case, answer=result.answer, sources=sources, issues=issues,
                                      passed=not issues, seconds=round(time.monotonic()-started, 2)),
                                 ensure_ascii=False), flush=True)
            print(json.dumps({'type': 'summary', 'passed': passed, 'failed': len(cases)-passed,
                              'total': len(cases)}, ensure_ascii=False), flush=True)
    finally:
        runtime.close()
    return 0 if passed == len(cases) else 1


if __name__ == '__main__':
    raise SystemExit(main())
