"""UI interaction tests against a fake transport; never create real money operations."""
import base64
import json
from pathlib import Path
import sys
from uuid import uuid4

import pytest

pytest.importorskip('streamlit')
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ui'))
from client import APIError, Client

ACCOUNT = str(uuid4())
BENEFICIARY = str(uuid4())
ACCOUNT_NUMBER = '100100000001'
BENEFICIARY_NUMBER = '100100000002'
OPERATION = str(uuid4())
CONVERSATION = str(uuid4())


@pytest.fixture
def app(monkeypatch):
    calls = []
    claims = base64.urlsafe_b64encode(json.dumps({'roles': ['customer']}).encode()).decode().rstrip('=')
    token = 'header.' + claims + '.signature'
    def request(self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path.endswith('/identity/status'):
            return {'verified':not (at.session_state['kyc_pending'] if 'kyc_pending' in at.session_state else False),'review':None}
        if path.endswith('/demo/beneficiaries'):
            return {'beneficiaries':[{'id':BENEFICIARY,'account_number':BENEFICIARY_NUMBER,'label':'مستفيد تجريبي'}]}
        if path.endswith('/audit'):
            return {'events':[{'id':OPERATION,'conversation_id':CONVERSATION,'event':'completed','created_at':'2026-11-16T10:00:00Z'}]}
        if path.endswith('/checkpoints'):
            return {'checkpoints':[{'id':'cp1','run_id':'run1','conversation_id':CONVERSATION,'step':1,'node':'supervisor','status':'completed','planner':'local_llm','created_at':'2026-11-16T10:00:00Z'}]}
        if path.endswith('/recent-conversations'):
            return {'conversations':[{'id':CONVERSATION,'status':'needs_input','updated_at':'2026-11-16T10:00:00Z'}]}
        if '/conversations/' in path:
            return {'dataset':'demo','missing':[], 'status':'completed'}
        if path.endswith('/login'):
            return {'challenge_id': 'test-challenge', 'expires_in': 300, 'resend_after_seconds': 60, 'development_otp': True}
        if '/auth/dev/otp/' in path:
            return {'otp': '123456'}
        if path.endswith('/login/otp/verify'):
            return {'access_token': token, 'expires_in': 3600}
        if '/demo/otp/' in path:
            return {'otp': '123456', 'demo': True}
        if path.endswith('/confirm'):
            return {'status': 'completed' if kwargs['payload']['approved'] else 'cancelled', 'conversation_id': CONVERSATION,
                    'result': {'id': OPERATION, 'reference': 'BW-20261120-000007', 'status': 'completed'}}
        if path.endswith('/logout'):
            return None
        raise AssertionError(path)
    def chat(self, message, *, tool=None, arguments=None, conversation_id=None):
        calls.append(('chat', tool, arguments))
        if tool is None:
            return {'status':'completed','conversation_id':CONVERSATION,'planner':'local_llm','trace':['security','supervisor','info'],'results':[], 'missing':[]}
        if tool == 'get_statement':
            return {'results': [{'data': {'accounts': [{'id': ACCOUNT, 'account_number': ACCOUNT_NUMBER, 'balance': '10000.00', 'status': 'open'}], 'transactions': []}}]}
        return {'status': 'awaiting_confirmation', 'conversation_id': CONVERSATION, 'missing': [],
                'operation': {'id': OPERATION, 'tool': tool, 'arguments': arguments or {}, 'expires_at': '2026-11-07T20:00:00Z'}, 'results': []}
    monkeypatch.setattr(Client, 'request', request)
    monkeypatch.setattr(Client, 'chat', chat)
    at = AppTest.from_file(str(ROOT / 'ui/app.py')).run(timeout=10)
    assert not at.exception
    return at, calls


def button(app, label):
    return next(b for b in app.button if b.label == label)


def login(at):
    at.text_input[0].set_value('demo_alice')
    at.text_input[1].set_value('test-password')
    button(at, 'دخول إلى حسابي  ←').click().run()
    assert 'token' not in at.session_state
    button(at, 'OTP').click().run()
    assert at.code[0].value == '123456'
    at.text_input[0].set_value('123456')
    button(at, 'تأكيد وتسجيل الدخول  ←').click().run()
    assert not at.exception
    return at


def test_login_dashboard_and_logout_clear_session(app):
    at, calls = app
    login(at)
    assert at.metric[0].value == '1'
    assert not any(widget.value == 'test-password' for widget in at.text_input)
    button(at, 'تسجيل الخروج').click().run()
    assert 'token' not in at.session_state
    assert any(path == '/api/v1/auth/logout' for method, path, args in calls)
    assert not at.exception


def test_gate_requires_explicit_approval_and_does_not_repeat_on_rerun(app):
    at, calls = app
    login(at)
    at.radio[0].set_value('التحويلات').run()
    next(t for t in at.selectbox if t.label == 'إلى مين؟').select(BENEFICIARY_NUMBER)
    button(at, 'مراجعة التحويل  ←').click().run()
    assert not at.exception
    assert at.session_state['pending']['id'] == OPERATION
    assert at.session_state['pending']['arguments']['account_id'] == ACCOUNT_NUMBER
    assert at.session_state['pending']['arguments']['beneficiary_id'] == BENEFICIARY_NUMBER
    button(at, 'تأكيد التنفيذ').click().run()
    assert not any(path.endswith('/confirm') for method, path, args in calls if isinstance(path, str))
    assert at.error
    button(at, 'عرض رمز التأكيد').click().run()
    next(t for t in at.text_input if 'رمز التحقق' in t.label).set_value('123456')
    at.checkbox[0].check()
    button(at, 'تأكيد التنفيذ').click().run()
    assert not at.exception
    at.run()
    confirmations = [args for method, path, args in calls if isinstance(path, str) and path.endswith('/confirm')]
    assert len(confirmations) == 1
    assert confirmations[0]['payload'] == {'approved': True, 'otp': '123456'}
    assert 'pending' not in at.session_state
    assert any('تم تنفيذ العملية بنجاح' in item.value for item in at.success)
    assert any('BW-20261120-000007' in item.value for item in at.caption)
    assert not at.get('doc_string')


def test_missing_beneficiary_never_reaches_api(app):
    at, calls = app
    login(at)
    at.radio[0].set_value('التحويلات').run()
    at.session_state['beneficiaries'] = []
    at.run()
    button(at, 'مراجعة التحويل  ←').click().run()
    assert at.error
    assert not any(tool == 'execute_transfer' for method, tool, args in calls)


def test_all_pages_render_without_exceptions(app):
    at, _ = app
    login(at)
    for page in ['المساعد الذكي', 'التحويلات', 'القروض', 'إدارة الحسابات', 'التحقق من الهوية', 'المعرفة والسياسات', 'سجل العمليات']:
        at.radio[0].set_value(page).run()
        assert not at.exception, page


def test_api_error_has_no_credentials_and_session_isolation(app, monkeypatch):
    at, _ = app
    login(at)
    fresh = AppTest.from_file(str(ROOT / 'ui/app.py')).run()
    assert 'token' not in fresh.session_state
    def expired(*args, **kwargs):
        raise APIError(401, 'expired')
    monkeypatch.setattr(Client, 'chat', expired)
    button(at, 'تحديث البيانات').click().run()
    assert 'token' not in at.session_state
    assert 'statement' not in at.session_state
    assert not at.exception


def test_transport_rejects_redirect_without_forwarding_token(monkeypatch):
    import httpx
    requests = []
    real_client = httpx.Client
    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={'Location': 'https://untrusted.invalid/'})
    monkeypatch.setattr(httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(APIError):
        Client('synthetic-secret').request('POST', '/api/v1/auth/logout', payload={})
    assert len(requests) == 1
    assert requests[0].url.host == '127.0.0.1'


def test_transport_sanitizes_server_errors(monkeypatch):
    import httpx
    real_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(
        lambda request: httpx.Response(500, text='PRIVATE_DATABASE_URL_AND_PASSWORD')), **kw))
    with pytest.raises(APIError) as error:
        Client('synthetic-secret').request('GET', '/api/v1/assistant/statement.pdf')
    assert 'PRIVATE' not in str(error.value)
    assert 'synthetic-secret' not in str(error.value)


def test_historical_workspace_and_date_validation(app, monkeypatch):
    at, calls = app
    original = Client.request
    def request(self, method, path, **kwargs):
        if path.startswith('/api/v1/assistant/history'):
            calls.append((method,path,kwargs))
            return {'notice':'Historical research only', 'accounts':[{'id':'1','client_id':1}],
                    'dataset_counts':{'trans':1056320,'client':5369,'account':4500,'loan':682},
                    'latest_balance':'1000.00','incoming_total':'200.00','outgoing_total':'50.00',
                    'as_of':'1998-12-31','monthly':[{'month':'1998-12','credit':'200','debit':'50'}],
                    'transactions':[],'total_transactions':0,'offset':0,'loans':[],
                    'selected_account_id':1,'source_url':'https://relational.fel.cvut.cz/dataset/Financial'}
        return original(self,method,path,**kwargs)
    monkeypatch.setattr(Client,'request',request)
    login(at)
    at.radio[0].set_value('البيانات البنكية الحقيقية').run()
    assert not at.exception
    assert any(m.value=='1,056,320' for m in at.metric)
    next(t for t in at.text_input if t.label=='من تاريخ (اختياري)').set_value('invalid')
    button(at,'عرض الحركة').click().run()
    assert at.error and not at.exception
    assert len([p for _,p,_ in calls if isinstance(p,str) and p.startswith('/api/v1/assistant/history')])==1


def test_restore_previous_request(app):
    at, calls = app
    login(at)
    button(at, 'اعرض آخر طلباتي').click().run()
    button(at, 'استكمل الطلب').click().run()
    assert not at.exception
    assert at.session_state['conversation'] == CONVERSATION
    assert at.radio[0].value == 'المساعد الذكي'


def test_chat_response_renders(app):
    at, _ = app
    login(at)
    at.radio[0].set_value('المساعد الذكي').run()
    at.chat_input[0].set_value('اعرض كشف حسابي').run()
    assert not at.exception
    assert len(at.session_state['history']) == 2


def test_signup_starts_with_phone_and_returns_to_login(app):
    at, _ = app
    button(at, 'جديد هنا؟ إنشاء حساب').click().run()
    assert not at.exception
    assert any(t.label == 'رقم الموبايل المصري' for t in at.text_input)
    assert not any(t.label == 'كلمة المرور' for t in at.text_input)
    button(at, 'العودة إلى تسجيل الدخول').click().run()
    assert not at.exception and not at.session_state['signup']


def test_identity_fields_can_be_cleared_from_session(app):
    at, _ = app
    login(at)
    at.session_state['roles'] = ['customer','identity_processor']
    at.session_state['identity_result'] = {'fields':{'first_name':'اختبار'}, 'summary':{'decision':{'status':'MANUAL_REVIEW'}}}
    at.radio[0].set_value('التحقق من الهوية').run()
    assert not at.exception
    assert len(at.get('file_uploader'))==2
    assert 'اختبار' in at.dataframe[0].value.to_string()
    button(at, 'مسح نتيجة البطاقة').click().run()
    assert 'identity_result' not in at.session_state


def test_new_customer_lands_on_identity_without_statement_error(app, monkeypatch):
    at, calls = app
    original = Client.request
    def request(self, method, path, **kwargs):
        result = original(self, method, path, **kwargs)
        if path.endswith('/login/otp/verify'):
            result['kyc_pending'] = True
        return result
    monkeypatch.setattr(Client, 'request', request)
    login(at)
    assert at.session_state['page']=='التحقق من الهوية'
    assert not at.error
    assert not any(method=='chat' and tool=='get_statement' for method,tool,_ in calls)
    at.radio[0].set_value('نظرة عامة').run()
    assert not at.error
    assert any(b.label=='رفع البطاقة' for b in at.button)


def test_pending_customer_sees_status_then_live_activation(app, monkeypatch):
    at,_=app
    original=Client.request
    current={'verified':False,'review':{'id':'request-1','status':'pending','reason':None}}
    def request(self,method,path,**kwargs):
        if path.endswith('/identity/status'): return current.copy()
        return original(self,method,path,**kwargs)
    monkeypatch.setattr(Client,'request',request)
    login(at)
    assert not at.error and any('قيد المراجعة' in i.value for i in at.info)
    at.radio[0].set_value('التحويلات').run()
    assert not at.error and any(b.label=='متابعة طلب الهوية' for b in at.button)
    current['verified']=True
    at.radio[0].set_value('نظرة عامة').run()
    assert not at.error and at.metric[0].value=='1'


def test_reviewer_queue_approval_requires_confirmation(app,monkeypatch):
    import io
    from PIL import Image
    at,calls=app
    output=io.BytesIO();Image.new('RGB',(20,20),'white').save(output,format='PNG')
    encoded=base64.b64encode(output.getvalue()).decode()
    original=Client.request
    pending=[{'id':'request-1','username':'customer_test','created_at':'2026-11-17T10:00:00'}]
    def request(self,method,path,**kwargs):
        if path.endswith('/reviews'): return {'reviews':pending.copy()}
        if path.endswith('/reviews/request-1'):
            return {'id':'request-1','front':encoded,'back':encoded,'result':{'fields':{'first_name':'اختبار'}}}
        if path.endswith('/decision'):
            calls.append((method,path,kwargs));pending.clear();return {'status':'approved','verified':True}
        result=original(self,method,path,**kwargs)
        if path.endswith('/login/otp/verify'):
            claims=base64.urlsafe_b64encode(json.dumps({'roles':['identity_reviewer']}).encode()).decode().rstrip('=')
            result['access_token']='header.'+claims+'.signature'
        return result
    monkeypatch.setattr(Client,'request',request)
    login(at)
    button(at,'عرض الصور والبيانات').click().run()
    button(at,'حفظ القرار').click().run()
    assert at.warning
    assert not any(path.endswith('/decision') for _,path,_ in calls if isinstance(path,str))
    at.checkbox[0].check()
    button(at,'حفظ القرار').click().run()
    assert not at.exception
    assert any(path.endswith('/decision') for _,path,_ in calls if isinstance(path,str))
    assert 'review_evidence' not in at.session_state


def test_real_delivery_hides_development_button(app):
    at, _ = app
    at.session_state['otp_challenge'] = 'real-challenge'
    at.session_state['otp_development'] = False
    at.run()
    assert not at.exception
    assert not any(b.label == 'OTP' for b in at.button)
