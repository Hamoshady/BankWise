"""End-to-end onboarding/ledger tests on disposable PostgreSQL, synthetic images only."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, func
from bankwise.database.models import (User, UserRole, DemoAccount, DemoCustomer, DemoTransaction,
    OnboardingApplication, SignupChallenge, DemoDeposit)
from bankwise.core.security import Principal
from bankwise.api.routers.onboarding import now
from test_api import api, password_hash, PASSWORD

ROOT='/api/v1/onboarding'
DATA=dict(full_name='Synthetic Test Customer',national_id='29901010101010',birth_date='1999-01-01',gender='male',address='Synthetic test address',occupation='Test engineer',expiry_date='2030-01-01',email='synthetic@example.invalid',employment='employed',income_source='Salary',purpose='Test account',expected_activity='under_10000',tax_residency='Egypt',account_type='personal_egp',consent=True)

@pytest.fixture
def setup(api):
    api.app.state.banking_demo=True
    api.app.state.review_key=Fernet.generate_key().decode()
    with api.db.session() as s, s.begin():
        u=s.scalar(select(User).where(User.username=='reader'))
        u.roles=[UserRole(role='identity_reviewer'),UserRole(role='demo_treasurer')]
        staff=Principal(str(u.id),frozenset(['identity_reviewer','demo_treasurer']))
    api.staff={'Authorization':'Bearer '+api.app.state.runtime.service.issue_token(staff)}
    return api

def begin(api,phone='+201055555001'):
    ch=api.client.post(ROOT+'/phone',json={'phone':phone})
    assert ch.status_code==201,ch.text
    cid=ch.json()['challenge_id']
    code=api.client.get(ROOT+'/dev/otp/'+cid).json()['otp']
    response=api.client.post(ROOT+'/phone/verify',json={'challenge_id':cid,'otp':code})
    assert response.status_code==200,response.text
    app=response.json()
    return app,{'X-Application-ID':app['id'],'X-Application-Token':app['application_token']}

def prepare(api,phone='+201055555001',username='new_customer',national_id=None):
    app,h=begin(api,phone)
    response=api.client.post(ROOT+'/images',headers={**h,'X-Evidence-Consent':'true'},files={'front':('f.png',api.image,'image/png'),'back':('b.png',api.image,'image/png')})
    assert response.status_code==200,response.text
    data=dict(DATA)
    if national_id:data['national_id']=national_id
    response=api.client.put(ROOT+'/details',headers=h,json=data)
    assert response.status_code==200,response.text
    response=api.client.post(ROOT+'/credentials',headers=h,json={'username':username,'password':PASSWORD,'confirmation':PASSWORD})
    assert response.status_code==200,response.text
    return app,h

def login(api,username):
    response=api.client.post('/api/v1/auth/login',json={'username':username,'password':PASSWORD})
    assert response.status_code==200,response.text
    cid=response.json()['challenge_id']
    code=api.client.get('/api/v1/auth/dev/otp/'+cid).json()['otp']
    response=api.client.post('/api/v1/auth/login/otp/verify',json={'challenge_id':cid,'otp':code})
    assert response.status_code==200,response.text
    return {'Authorization':'Bearer '+response.json()['access_token']}

def decision(api,app,decision='approved',reason='checked'):
    return api.client.post(ROOT+'/reviews/'+app['id']+'/decision',headers=api.staff,json={'decision':decision,'reason':reason,'confirmed':True})

def test_full_review_activation_and_idempotent_deposit(setup):
    api=setup
    with api.db.session() as s,s.begin():
        old=s.scalar(select(User).where(User.username=='alice'))
        s.add(DemoCustomer(owner_id=old.id,verified=True))
        a=DemoAccount(owner_id=old.id,balance=123456,status='open');s.add(a);s.flush();old_account=a.id
    app,h=prepare(api)
    with api.db.session() as s:
        u=s.scalar(select(User).where(User.username=='new_customer'))
        assert u.phone_verified_at is None
        assert s.get(DemoCustomer,u.id) is None
        assert s.scalar(select(func.count()).select_from(DemoAccount).where(DemoAccount.owner_id==u.id))==0
        owner=u.id
    customer_headers=login(api,'new_customer')
    assert api.client.post('/api/v1/assistant/chat',headers=customer_headers,json={'message':'balance'}).status_code==403
    assert api.client.get(ROOT+'/mine',headers=customer_headers).status_code==200
    # /mine rotates the limited application token; use the returned token.
    mine=api.client.get(ROOT+'/mine',headers=customer_headers).json()
    h['X-Application-Token']=mine['application_token']
    assert api.client.post(ROOT+'/submit',headers=h).status_code==200
    assert api.client.post(ROOT+'/submit',headers=h).status_code==200
    assert api.client.get(ROOT+'/reviews',headers=customer_headers).status_code==403
    assert decision(api,app).status_code==200
    assert decision(api,app).status_code==200
    with api.db.session() as s:
        accounts=s.scalars(select(DemoAccount).where(DemoAccount.owner_id==owner)).all()
        assert len(accounts)==1 and accounts[0].balance==0
        account_id=accounts[0].id
        assert s.get(DemoCustomer,owner).verified
        assert s.get(DemoAccount,old_account).balance==123456
        row=s.get(OnboardingApplication,UUID(app['id']))
        data=__import__('json').loads(Fernet(api.app.state.review_key.encode()).decrypt(row.encrypted_data))
        assert 'front' not in data and 'back' not in data
    payload={'account_id':str(account_id),'amount':'5000.00','operation_id':str(uuid4()),'note':'Synthetic demo funding','confirmed':True}
    assert api.client.post(ROOT+'/deposit',headers=login(api,'new_customer'),json=payload).status_code==403
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:api.client.post(ROOT+'/deposit',headers=api.staff,json=payload),range(2)))
    assert all(r.status_code==201 for r in responses),[r.text for r in responses]
    assert sorted(r.json()['replayed'] for r in responses)==[False,True]
    payload['amount']='6000.00'
    assert api.client.post(ROOT+'/deposit',headers=api.staff,json=payload).status_code==409
    with api.db.session() as s:
        assert s.get(DemoAccount,account_id).balance==500000
        assert s.get(DemoAccount,old_account).balance==123456
        assert s.scalar(select(func.count()).select_from(DemoTransaction).where(DemoTransaction.destination_id==account_id))==1
        assert s.scalar(select(func.count()).select_from(DemoDeposit))==1
        tx=s.scalar(select(DemoTransaction).where(DemoTransaction.destination_id==account_id))
        assert tx.source_id is None and tx.amount==500000

def test_signup_otp_expiry_attempts_purpose_and_session_isolation(setup):
    api=setup
    response=api.client.post(ROOT+'/phone',json={'phone':'+201055555001'})
    cid=response.json()['challenge_id'];code=api.client.get(ROOT+'/dev/otp/'+cid).json()['otp']
    assert api.client.post('/api/v1/auth/login/otp/verify',json={'challenge_id':cid,'otp':code}).status_code==401
    wrong='000000' if code!='000000' else '111111'
    for _ in range(5): assert api.client.post(ROOT+'/phone/verify',json={'challenge_id':cid,'otp':wrong}).status_code==401
    assert api.client.post(ROOT+'/phone/verify',json={'challenge_id':cid,'otp':code}).status_code==401
    assert api.client.post(ROOT+'/phone',json={'phone':'+201055555001'}).status_code==429
    response=api.client.post(ROOT+'/phone',json={'phone':'+201055555002'})
    cid2=response.json()['challenge_id'];code2=api.client.get(ROOT+'/dev/otp/'+cid2).json()['otp']
    with api.db.session() as s,s.begin():
        ch=s.get(SignupChallenge,UUID(cid2));ch.expires_at=now()-timedelta(seconds=1)
    assert api.client.post(ROOT+'/phone/verify',json={'challenge_id':cid2,'otp':code2}).status_code==401
    app,h=begin(api,'+201055555003')
    other,h2=begin(api,'+201055555004')
    h2['X-Application-ID']=app['id']
    assert api.client.get(ROOT+'/application',headers=h2).status_code==401
    assert api.client.post(ROOT+'/submit',headers=h).status_code==409
    api.app.state.dev_sms=False
    assert api.client.get(ROOT+'/dev/otp/'+cid).status_code==404

def test_needs_information_rejection_and_duplicate_identity(setup):
    api=setup
    app,h=prepare(api)
    assert api.client.post(ROOT+'/submit',headers=h).status_code==200
    assert api.client.put(ROOT+'/details',headers=h,json=DATA).status_code==409
    assert decision(api,app,'needs_information','unclear_images').status_code==200
    assert api.client.put(ROOT+'/details',headers=h,json=DATA).status_code==200
    assert api.client.post(ROOT+'/submit',headers=h).status_code==200
    assert decision(api,app,'rejected','inconsistent_data').status_code==200
    assert decision(api,app).status_code==409
    other,h2=begin(api,'+201055555002')
    assert api.client.post(ROOT+'/images',headers={**h2,'X-Evidence-Consent':'true'},files={'front':('f.png',api.image,'image/png'),'back':('b.png',api.image,'image/png')}).status_code==200
    assert api.client.put(ROOT+'/details',headers=h2,json=DATA).status_code==409
    with api.db.session() as s:
        assert s.scalar(select(func.count()).select_from(DemoAccount))==0
    assert api.client.post('/api/v1/auth/signup',json={'username':'bypass','password':PASSWORD}).status_code==404

def test_validation_legacy_preservation_and_no_secrets_in_responses(setup):
    api=setup
    app,h=begin(api)
    assert api.client.post(ROOT+'/images',headers=h,files={'front':('f.png',api.image,'image/png'),'back':('b.png',api.image,'image/png')}).status_code==422
    assert api.client.post(ROOT+'/images',headers={**h,'X-Evidence-Consent':'true'},files={'front':('f.png',api.image,'image/png'),'back':('b.png',api.image,'image/png')}).status_code==200
    for patch in ({'birth_date':'2000-01-01'},{'expiry_date':'2020-01-01'},{'national_id':'29999010101010'}):
        assert api.client.put(ROOT+'/details',headers=h,json={**DATA,**patch}).status_code==422
    assert api.client.put(ROOT+'/details',headers=h,json=DATA).status_code==200
    with api.db.session() as s:
        row=s.get(OnboardingApplication,UUID(app['id']))
        assert DATA['national_id'].encode() not in row.encrypted_data
        assert row.identity_digest != DATA['national_id']
    assert api.client.post(ROOT+'/cancel',headers=h).status_code==200
    with api.db.session() as s:
        assert s.get(OnboardingApplication,UUID(app['id'])).encrypted_data is None
