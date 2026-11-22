"""Live AppTest smoke: requires running API, Streamlit, and local demo credentials. No money mutations."""
import json
import os
import time
from pathlib import Path
from streamlit.testing.v1 import AppTest
users=json.loads(Path(os.environ.get('BANKWISE_TEST_LOGINS', str(Path.home()/'.config/bankwise_ai/demo-logins.json'))).read_text())
u=next(x for x in users if x['username']=='demo_alice')
at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'ui/app.py')).run(timeout=30)
at.text_input[0].set_value(u['username']); at.text_input[1].set_value(u['password'])
def button(label): return next(x for x in at.button if x.label==label)
button('دخول إلى حسابي  ←').click().run(timeout=60)
assert not at.exception
at.radio[0].set_value('المساعد الذكي').run()
measurements=[]
for prompt,expected in [('اعرض كشف حسابي','get_statement'),('ما الحد اليومي لحساب بداية؟','search_knowledge_base'),('عايز كشف حساب وشروط القرض','get_statement')]:
    started=time.perf_counter()
    at.chat_input[0].set_value(prompt).run(timeout=240)
    elapsed=round(time.perf_counter()-started,3)
    assert not at.exception
    r=at.session_state['history'][-1]['response']
    print('RESPONSE',prompt,r.get('planner'),r.get('status'),[x['tool'] for x in r['results']],flush=True)
    assert r['planner']=='local_llm'
    if os.environ.get('BANKWISE_EXPECT_MODEL'): assert os.environ['BANKWISE_EXPECT_MODEL'] in r.get('model','')
    measurements.append({'prompt':prompt,'seconds':elapsed,'tools':[x['tool'] for x in r['results']],'model':r.get('model')})
    if os.environ.get('BANKWISE_UI_REPORT'): Path(os.environ['BANKWISE_UI_REPORT']).write_text(json.dumps({'queries':measurements,'complete':False},ensure_ascii=False,indent=2))
    assert expected in [x['tool'] for x in r['results']]
    if 'بداية' in prompt: assert '90000' in str(r)
    if 'وشروط' in prompt: assert 'search_knowledge_base' in [x['tool'] for x in r['results']]
at.radio[0].set_value('سجل العمليات').run(timeout=30)
assert not at.exception
assert len(at.dataframe)>=2
print('PASS live UI LLM routing, multi-tool, Bedaya RAG, audit/checkpoint tables',flush=True)
button('اعرض آخر طلباتي').click().run(timeout=30)
button('استكمل الطلب').click().run(timeout=30)
assert not at.exception
assert at.radio[0].value=='المساعد الذكي'
print('PASS live UI restore conversation',flush=True)
button('تسجيل الخروج').click().run()
assert not at.exception

if os.environ.get('BANKWISE_UI_REPORT'):
    Path(os.environ['BANKWISE_UI_REPORT']).write_text(json.dumps({'queries':measurements,'complete':True},ensure_ascii=False,indent=2))
