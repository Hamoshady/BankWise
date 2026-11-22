"""Live, read-only Streamlit policy regression and latency measurement (no browser automation)."""
import json,os,time
from pathlib import Path
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parents[1]
CASES=[('ما الحد اليومي لحساب بداية؟','90000'),('كم الحد الشهري لحساب بداية؟','300000'),('ما أقصى رصيد لحساب بداية؟','750000'),('ما الحد الأدنى لفتح حساب Classic بالجنيه؟','20000'),('ما الحد الأدنى للسن للقرض الشخصي؟','21'),('ما شروط إغلاق حساب CIB؟','فرع')]
u=next(x for x in json.loads(Path(os.environ['BANKWISE_TEST_LOGINS']).read_text()) if x['username']=='demo_alice')
at=AppTest.from_file(str(ROOT/'ui/app.py')).run(timeout=30)
at.text_input[0].set_value(u['username']);at.text_input[1].set_value(u['password'])
next(b for b in at.button if b.label=='دخول إلى حسابي  ←').click().run(timeout=60)
assert not at.exception
at.radio[0].set_value('المساعد الذكي').run()
rows=[]
try:
 for phase in ['first','repeat']:
  for question,expected in CASES:
   started=time.perf_counter();at.chat_input[0].set_value(question).run(timeout=240);elapsed=time.perf_counter()-started
   assert not at.exception
   result=at.session_state['history'][-1]['response']
   assert result['status']=='completed' and result['planner']=='local_llm'
   assert [r['tool'] for r in result['results']]==['search_knowledge_base']
   evidence=result['results'][0]['data']
   assert expected in evidence['answer'],(question,evidence['answer'])
   assert evidence['citations'] and all(c.get('url','').startswith('https://www.cibeg.com/') for c in evidence['citations'])
   rows.append({'phase':phase,'question':question,'seconds':round(elapsed,3),'answer':evidence['answer'],'citations':evidence['citations']})
   Path(os.environ['BANKWISE_POLICY_REPORT']).write_text(json.dumps({'complete':False,'queries':rows},ensure_ascii=False,indent=2))
   print(phase,question,round(elapsed,3),'PASS',flush=True)
finally:
 next(b for b in at.button if b.label=='تسجيل الخروج').click().run(timeout=30)
Path(os.environ['BANKWISE_POLICY_REPORT']).write_text(json.dumps({'complete':True,'queries':rows},ensure_ascii=False,indent=2))
