"""Local smoke verification using existing private demo credentials. Never prints secrets."""
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE='http://127.0.0.1:8000'

def call(path, token=None, body=None, expected=200, binary=False):
    headers={'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    request=Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
    try:
        with urlopen(request,timeout=180) as response:
            assert response.status==expected
            content=response.read()
            return content if binary else json.loads(content) if content else None
    except HTTPError as e:
        if e.code!=expected:raise AssertionError(f'{path}: HTTP {e.code}') from None
        return None


def main():
    credentials=json.loads((Path.home()/'.config/bankwise_ai/demo-logins.json').read_text())
    tokens={}
    for user in credentials:
        tokens[user['username']]=call('/api/v1/auth/login',body={k:user[k] for k in ('username','password')})['access_token']
    a,b=tokens['demo_alice'],tokens['demo_bob']
    try:
        call('/api/v1/assistant/history',expected=401)
        history=call('/api/v1/assistant/history',a)
        assert history['dataset_counts']['trans']==1056320
        assert history['currency']=='CZK' and history['as_of'].startswith('1998')
        call('/api/v1/assistant/history?account_id=1',b,expected=404)
        page=call('/api/v1/assistant/history?account_id=1&offset=100',a)
        assert page['incoming_total']==history['incoming_total']
        assert {t['id'] for t in page['transactions']}.isdisjoint(t['id'] for t in history['transactions'])
        pdf=call('/api/v1/assistant/history/statement.pdf?account_id=1',a,binary=True)
        assert pdf.startswith(b'%PDF')
        Path('outputs/research-statement.pdf').write_bytes(pdf)
        result=call('/api/v1/assistant/chat',a,{'message':'اعرض كشف الحساب','dataset':'berka'})
        assert result['results'][0]['data']['historical'] and 'info' in result['trace']
        saved=call('/api/v1/assistant/conversations/'+result['conversation_id'],a)
        assert saved['dataset']=='berka' and saved['trace']==result['trace']
        call('/api/v1/assistant/chat',a,{'message':'افتح حساب','dataset':'berka','tool':'open_account'},expected=422)
        catalog=call('/api/v1/rag/catalog',a)
        assert len(catalog['documents'])==5
        for question,expected in [('ما الحد الأدنى لفتح حساب Classic بالجنيه؟','20000'),('ما شروط إغلاق حساب CIB؟','فرع'),('ما الحد الأدنى للسن للقرض الشخصي؟','21'),('ما الحد اليومي لحساب بداية؟','90000'),('كم الحد الشهري لحساب بداية؟','300000'),('ما أقصى رصيد لحساب بداية؟','750000')]:
            answer=call('/api/v1/rag/query',a,{'query':question})
            print('RAG:',question,answer['answer'],flush=True)
            assert expected in answer['answer'], (question,answer['answer'])
            assert answer['citations'] and all(c.get('url','').startswith('https://www.cibeg.com/') for c in answer['citations'])
        # Test the gate without posting any transfer or changing account balances.
        pending=call('/api/v1/assistant/chat',a,{'message':'قدم على قرض','tool':'create_loan_request','arguments':{'amount':'1000','months':12}})
        assert pending['status']=='awaiting_confirmation'
        op=pending['operation']['id']
        assert len(call('/api/v1/assistant/demo/otp/'+op,a)['otp'])==6
        cancelled=call('/api/v1/assistant/operations/'+op+'/confirm',a,{'approved':False,'otp':''})
        assert cancelled['status']=='cancelled'
        print('PASS: authenticated historical API, grant isolation, pagination, PDF, graph/checkpoint, CIB retrieval and confirmation gate.',flush=True)
    finally:
        for token in tokens.values(): call('/api/v1/auth/logout',token,{},expected=204)


if __name__=='__main__':main()
