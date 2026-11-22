"""Time embedding and evidence-selection stages on the checked-in CIB corpus."""
import argparse,json,time,importlib.util,hashlib
from pathlib import Path
from bankwise.rag.semantic import SemanticRuntime
from bankwise.rag.service import RAGService
from bankwise.rag.vector_store import SQLiteVectorStore
p=argparse.ArgumentParser();p.add_argument('--output',required=True);args=p.parse_args()
# Profile the actual packaged backend, even when source is mounted for the harness.
installed=Path('/app/src/bankwise/rag/semantic.py')
if installed.exists():
 spec=importlib.util.spec_from_file_location('profile_runtime',installed);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);SemanticRuntime=module.SemanticRuntime
 print('runtime_sha256',hashlib.sha256(installed.read_bytes()).hexdigest(),flush=True)
startup=time.perf_counter()
runtime=SemanticRuntime('/rag-models',llm_url='http://127.0.0.1:8081')
print('initialization_seconds',round(time.perf_counter()-startup,3),'prefix_warmup_seconds',getattr(runtime,'warmup_seconds',None),flush=True)
embed=runtime.embed;complete=runtime.external_llm.complete;timings={}
def timed_embed(texts):
 start=time.perf_counter();r=embed(texts);timings['embedding_seconds']+=time.perf_counter()-start;timings['embedding_texts']+=len(texts);return r
def timed_complete(*a,**kw):
 start=time.perf_counter();r=complete(*a,**kw);timings['llm_seconds']+=time.perf_counter()-start;timings['llm_calls']+=1;return r
runtime.embed=timed_embed;runtime.external_llm.complete=timed_complete
rows=[]
with SQLiteVectorStore(':memory:') as store:
 for path in sorted((Path(__file__).resolve().parents[1]/'knowledge/cib').glob('*.md')):
  RAGService(store).ingest_text(path.read_text(),path.stem)
 for phase in ['first','repeat']:
  timings={'embedding_seconds':0.,'embedding_texts':0,'llm_seconds':0.,'llm_calls':0}
  started=time.perf_counter();result=runtime.answer(store,'ما الحد اليومي لحساب بداية؟')
  assert '90000' in result.answer and result.citations
  rows.append({'phase':phase,**timings,'total_seconds':time.perf_counter()-started,'answer':result.answer,'sources':[c.source for c in result.citations]})
Path(args.output).write_text(json.dumps(rows,ensure_ascii=False,indent=2));print(json.dumps(rows,ensure_ascii=False,indent=2))
