"""Run local semantic policy Q&A without an account, HTTP server or database."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def interactive(directory, model_directory):
    from bankwise.core.pii import redact_text
    from bankwise.rag.service import RAGService
    from bankwise.rag.vector_store import SQLiteVectorStore
    from bankwise.rag.semantic import SemanticRuntime

    files = sorted(directory.glob('*.md'))
    if not files:
        print('لم يتم العثور على ملفات سياسات.')
        return 1
    runtime = None
    try:
        with SQLiteVectorStore(':memory:') as store:
            service = RAGService(store)
            for path in files:
                if path.stat().st_size > 16384:
                    raise ValueError('policy exceeds limit')
                content = path.read_text(encoding='utf-8')
                if not content.strip() or len(content) > 8000 or '\x00' in content or redact_text(content) != content:
                    raise ValueError('policy rejected')
                service.ingest_text(content, path.stem)
            print('جارٍ تحميل النموذج الدلالي والـLLM محليًا…', flush=True)
            runtime = SemanticRuntime(str(model_directory))
            print('جاهز. اكتب سؤالك أو exit للخروج. الوضع: semantic_grounded', flush=True)
            while True:
                question = input('\nالسؤال: ').strip()
                if question.lower() in {'exit', 'quit'}:
                    break
                if not question:
                    continue
                if len(question) > 1000 or '\x00' in question or redact_text(question) != question:
                    print('اكتب سؤالًا حتى 1000 حرف بدون بيانات شخصية أو أسرار.')
                    continue
                answer = runtime.answer(store, question, 5)
                print('\n' + redact_text(answer.answer), flush=True)
                for index, citation in enumerate(answer.citations, 1):
                    print(f'\n[{index}] {citation.source}\n{redact_text(citation.excerpt)}', flush=True)
    except (EOFError, KeyboardInterrupt):
        print('\nتم إنهاء الجلسة.')
    except Exception:
        print('تعذر تشغيل السؤال المحلي. تحقق من السياسات وصورة Docker وملفات الموديلات.')
        return 1
    finally:
        if runtime is not None:
            runtime.close()
    return 0


def main():
    if sys.argv[1:] == ['--inside-container']:
        return interactive(Path('/policies'), Path('/rag-models'))
    if sys.argv[1:]:
        print('الاستخدام: python3 scripts/ask_rag.py')
        return 1
    command = ['docker', 'run', '--rm', '-i']
    if sys.stdin.isatty():
        command.append('-t')
    command += ['--network', 'none', '-e', 'HF_HUB_OFFLINE=1', '-e', 'TRANSFORMERS_OFFLINE=1',
                '-e', 'HF_HUB_DISABLE_TELEMETRY=1',
                '-v', 'bankwise-rag-models:/rag-models:ro',
                '-v', str(ROOT / 'knowledge/cib') + ':/policies:ro',
                '-v', str(Path(__file__).resolve()) + ':/ask_rag.py:ro',
                'bankwise-ai:semantic', 'python', '/ask_rag.py', '--inside-container']
    try:
        return subprocess.call(command)
    except FileNotFoundError:
        print('Docker غير متاح. راجع إعداد التشغيل المحلي.')
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
