"""Wizard navigation, limited applicant view and explicit deposit confirmation."""
import sys
from pathlib import Path
import pytest
pytest.importorskip('streamlit')
from streamlit.testing.v1 import AppTest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'ui'))
from client import Client

def button(at,label): return next(x for x in at.button if x.label==label)

@pytest.fixture
def signup(monkeypatch):
    calls=[]
    state={'id':'app-test','reference':'BW-TEST','status':'draft','reason':None,'credentials_created':False,'has_images':False,'fields':{},'kyc':{},'ocr':{},'phone_verified':False}
    def request(self,method,path,**kwargs):
        calls.append((method,path,kwargs))
        if path.endswith('/phone'):
            return {'challenge_id':'signup-test','development_otp':True}
        if '/dev/otp/' in path:return {'otp':'123456'}
        if path.endswith('/phone/verify'):return {**state,'application_token':'limited-test-token'}
        if path.endswith('/application'):return dict(state)
        if path.endswith('/details'):
            payload=kwargs['payload'];keys={'full_name','national_id','birth_date','gender','address','occupation','expiry_date'}
            state['fields']={k:v for k,v in payload.items() if k in keys}
            state['kyc']={k:v for k,v in payload.items() if k not in keys}
            return dict(state)
        if path.endswith('/credentials'):
            state['credentials_created']=True;state['username']=kwargs['payload']['username'];return dict(state)
        if path.endswith('/submit'):state['status']='pending_review';return dict(state)
        raise AssertionError(path)
    monkeypatch.setattr(Client,'request',request)
    at=AppTest.from_string('import onboarding\nonboarding.wizard()').run()
    return at,state,calls

def verify(at):
    at.text_input[0].set_value('01055555001')
    button(at,'إرسال كود التسجيل').click().run()
    button(at,'OTP').click().run()
    assert at.code[0].value=='123456'
    next(x for x in at.text_input if x.label=='كود التسجيل · 6 أرقام').set_value('123456')
    button(at,'تأكيد الموبايل والمتابعة').click().run()
    assert not at.exception

def test_phone_otp_then_evidence_widgets(signup):
    at,state,calls=signup
    verify(at)
    assert len(at.get('file_uploader'))==2
    assert not any(x[1].endswith('/credentials') for x in calls)
    assert 'token' not in at.session_state

def test_details_credentials_summary_and_pending_view(signup):
    at,state,calls=signup
    verify(at)
    state['has_images']=True
    state['ocr']={'fields':{'first_name':'Synthetic','other_names':'Customer','national_id':'29901010101010','birth_date':'1999-01-01','expiry_date':'2030-01-01','job':'Engineer','address_line1':'Test address'}}
    at.run()
    for label,value in [('البريد الإلكتروني','customer@example.invalid'),('مصدر الدخل','Salary'),('الغرض من فتح الحساب','Testing'),('دولة / دول الإقامة الضريبية','Egypt')]:
        next(x for x in at.text_input if x.label==label).set_value(value)
    next(c for c in at.checkbox if c.label.startswith('أقر')).check()
    button(at,'حفظ البيانات والمتابعة').click().run()
    assert not at.exception
    assert state['fields']['national_id']=='29901010101010'
    next(x for x in at.text_input if x.label=='اسم المستخدم').set_value('synthetic_customer')
    next(x for x in at.text_input if x.label=='كلمة المرور').set_value('Synthetic-password-123')
    next(x for x in at.text_input if x.label=='تأكيد كلمة المرور').set_value('Synthetic-password-123')
    button(at,'حفظ بيانات الدخول').click().run()
    assert not at.exception
    assert state['credentials_created']
    assert button(at,'تقديم طلب فتح الحساب').disabled
    next(c for c in at.checkbox if c.label.startswith('راجعت بياناتي')).check().run()
    button(at,'تقديم طلب فتح الحساب').click().run()
    assert not at.exception
    assert state['status']=='pending_review'
    assert not any(b.label=='تقديم طلب فتح الحساب' for b in at.button)
    assert 'token' not in at.session_state

def test_deposit_requires_preview_and_confirmation(monkeypatch):
    calls=[]
    def request(self,method,path,**kwargs):
        if path.endswith('/accounts'):return {'accounts':[{'id':'test-account','account_number':'100100000019','username':'approved_customer','balance':'0.00'}]}
        calls.append(kwargs['payload']);return {'transaction_id':'test-tx','reference':'BW-20261120-000008','amount':'100.00','replayed':False}
    monkeypatch.setattr(Client,'request',request)
    at=AppTest.from_string('import onboarding\nfrom client import Client\nonboarding.deposits(Client("staff"))').run()
    at.text_input[0].set_value('Demo test funding')
    button(at,'مراجعة الإيداع').click().run()
    assert not calls
    assert any('100100000019' in item.value for item in at.markdown)
    assert button(at,'تنفيذ الإيداع').disabled
    at.checkbox[0].check().run()
    button(at,'تنفيذ الإيداع').click().run()
    assert not at.exception and len(calls)==1
    assert calls[0]['amount']=='100.00' and calls[0]['confirmed']
    assert at.success
    assert any('BW-20261120-000008' in item.value for item in at.success)


def test_wrong_signup_otp_keeps_challenge_for_retry(signup,monkeypatch):
    from client import APIError
    at,state,calls=signup
    original=Client.request
    def request(self,method,path,**kwargs):
        if path.endswith('/phone/verify'):
            raise APIError(401,'رمز التحقق غير صحيح',code='otp_failed',reason='invalid_otp')
        return original(self,method,path,**kwargs)
    monkeypatch.setattr(Client,'request',request)
    at.text_input[0].set_value('01055555001')
    button(at,'إرسال كود التسجيل').click().run()
    next(x for x in at.text_input if x.label=='كود التسجيل · 6 أرقام').set_value('000000')
    button(at,'تأكيد الموبايل والمتابعة').click().run()
    assert not at.exception and at.error
    assert 'ob_challenge' in at.session_state
    assert any(x.label=='كود التسجيل · 6 أرقام' for x in at.text_input)


def test_uncertain_ocr_fields_are_visible_before_submission(signup):
    at, state, _ = signup
    verify(at)
    state['has_images'] = True
    state['ocr'] = {'fields': {'first_name': 'Synthetic'},
                    'summary': {'field_status': ['first_name:low_confidence']}}
    at.run()
    warnings = ' '.join(x.value for x in at.warning)
    assert 'الاسم الأول' in warnings and 'تاريخ الانتهاء' in warnings
    assert 'صور أوضح' in warnings
    assert not at.exception
