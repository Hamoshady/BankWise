"""Offline intent evaluation. Never executes banking tools or modifies customer data."""
import argparse,json,time,statistics
from pathlib import Path
from bankwise.agents.planner import plan_with_llm
from bankwise.rag.semantic import SemanticRuntime
p=argparse.ArgumentParser();p.add_argument('--backend',choices=['current','llama'],required=True);p.add_argument('--output',required=True);args=p.parse_args()
root=Path(__file__).resolve().parents[1]
if args.backend=='current': runtime=SemanticRuntime('/rag-models')
else:
    from types import SimpleNamespace
    from threading import Lock
    from bankwise.rag.local_llm import LlamaClient
    runtime=SimpleNamespace(lock=Lock(),external_llm=LlamaClient('http://127.0.0.1:8081'))
cases=json.loads((root/'evaluation/supervisor/cases.json').read_text());results=[]
# Warmup excluded; each measured request is a complete planner call, with identical prompts.
try: plan_with_llm(runtime,'اعرض كشف حسابي')
except Exception: pass
for case in cases:
    start=time.perf_counter();error=None
    try: actual=plan_with_llm(runtime,case['text'])
    except Exception as e: actual=[];error=type(e).__name__
    results.append({**case,'actual':actual,'seconds':round(time.perf_counter()-start,3),'correct':set(actual)==set(case['tools']),'error':error})
    report={'backend':args.backend,'cases':results,'correct':sum(r['correct'] for r in results),'total':len(results),'median_seconds':statistics.median(r['seconds'] for r in results),'mean_seconds':statistics.mean(r['seconds'] for r in results)}
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(case['id'],actual,results[-1]['correct'],results[-1]['seconds'],flush=True)
