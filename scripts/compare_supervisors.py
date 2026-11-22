"""Summarize complete benchmark runs against the pre-recorded adoption criteria."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/'evaluation/supervisor'

def main():
    old=json.loads((ROOT/'qwen2.5-1.5b.json').read_text())
    new=json.loads((ROOT/'qwen3.5-4b.json').read_text())
    cases=json.loads((ROOT/'cases.json').read_text())
    policy=json.loads((ROOT/'protocol.json').read_text())['adoption']
    ids={c['id'] for c in cases}
    if any({c['id'] for c in r['cases']}!=ids or r['total']!=len(cases) for r in (old,new)):
        raise SystemExit('Incomplete benchmark; no adoption decision')
    indexed={c['id']:c for c in new['cases']}
    checks={'accuracy':new['correct']>=policy['minimum_correct'],
            'improvement':new['correct']>old['correct'],
            'safety_cases':all(indexed[id]['correct'] for id in policy['must_pass_safety_cases']),
            'latency':new['median_seconds']<=old['median_seconds']*policy['maximum_median_latency_ratio']}
    report={'old':{k:v for k,v in old.items() if k!='cases'},'new':{k:v for k,v in new.items() if k!='cases'},
            'checks':checks,'routing_gate_passed':all(checks.values()),
            'median_speedup':round(old['median_seconds']/new['median_seconds'],2),
            'additional_adoption_requirements':policy['additional_requirements']}
    (ROOT/'comparison.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
