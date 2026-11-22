"""Sandbox onboarding. Application sessions never authorize banking operations."""
import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from bankwise.api.deps import authorization, client_key, get_security, get_session, get_identity
from bankwise.api.routers.identity_review import cipher, reviewer
from bankwise.api.routers.rag import principal_for
from bankwise.api.transport import parse_images
from bankwise.core.errors import AuthenticationError, Conflict, InvalidInputError, NotFound, OtpError, PermissionDenied, RateLimitExceeded, ServiceUnavailable
from bankwise.core.otp import generate_otp, hash_otp, verify_otp, OTP_TTL_SECONDS, OTP_MAX_ATTEMPTS, OTP_RESEND_COOLDOWN_SECONDS
from bankwise.database.accounts import create_user
from bankwise.database.models import OnboardingApplication as Application, SignupChallenge, DemoDeposit, DemoCustomer, DemoAccount, DemoTransaction, User, UserRole

router = APIRouter(prefix='/api/v1/onboarding', tags=['onboarding'])
treasurer = principal_for('demo:deposit', 'auth_attempt')

def now(): return datetime.now(timezone.utc)
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def private_digest(request, purpose, value):
    cipher(request)  # require encryption configuration even before accepting a draft
    return hmac.new(request.app.state.review_key.encode(), (purpose + ':' + value).encode(), hashlib.sha256).hexdigest()
def sandbox(request):
    if not request.app.state.banking_demo: raise NotFound()
def encode(request, data): return cipher(request).encrypt(json.dumps(data, ensure_ascii=False).encode())
def decode(request, row):
    if not row.encrypted_data: return {}
    try: return json.loads(cipher(request).decrypt(row.encrypted_data))
    except InvalidToken: raise ServiceUnavailable('review key mismatch') from None

def expire(session):
    session.execute(update(Application).where(Application.status.in_(['draft','pending_review','needs_information']), Application.expires_at <= now())
        .values(status='expired', encrypted_data=None, identity_digest=None, session_digest=None))
    session.execute(update(Application).where(Application.status.in_(['rejected','cancelled']), Application.expires_at <= now()).values(encrypted_data=None, session_digest=None))

def view(row, data=None):
    result = {'id': str(row.id), 'reference': 'BW-' + str(row.id).upper(), 'status': row.status,
              'reason': row.reason, 'expires_at': row.expires_at.isoformat(), 'credentials_created': bool(row.user_id),
              'phone_verified': row.phone_verified}
    if data is not None:
        result.update({'fields': data.get('fields', {}), 'kyc': data.get('kyc', {}), 'ocr': data.get('ocr', {}),
                       'has_images': bool(data.get('front') and data.get('back')), 'flags': data.get('flags', []),
                       'username': data.get('username')})
    return result

def grant(row):
    token = secrets.token_urlsafe(32)
    row.session_digest = digest(token)
    row.session_expires_at = now() + timedelta(minutes=30)
    return token

def own(request, session, *, editable=False):
    sandbox(request)
    try: app_id = UUID(request.headers.get('x-application-id', ''))
    except ValueError: raise AuthenticationError() from None
    token = request.headers.get('x-application-token', '')
    row = session.scalar(select(Application).where(Application.id == app_id).with_for_update())
    if not row or not row.session_digest or not token or not hmac.compare_digest(row.session_digest, digest(token)) or row.session_expires_at <= now():
        raise AuthenticationError()
    if row.status in {'draft','pending_review','needs_information'} and row.expires_at <= now(): raise Conflict()
    if editable and row.status not in {'draft','needs_information'}: raise Conflict()
    return row

class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
class Phone(Input):
    phone: str = Field(min_length=10, max_length=20)
class OTP(Input):
    challenge_id: UUID
    otp: str = Field(pattern=r'^[0-9]{6}$')

def normalized_phone(value):
    value = re.sub(r'[\s()-]', '', value)
    if value.startswith('0020'): value = '+' + value[2:]
    if re.fullmatch(r'01[0125][0-9]{8}', value): value = '+20' + value[1:]
    if not re.fullmatch(r'\+201[0125][0-9]{8}', value): raise InvalidInputError('Egyptian mobile required')
    return value

@router.post('/phone', status_code=201)
def phone(body: Phone, request: Request, session=Depends(get_session), security=Depends(get_security)):
    sandbox(request)
    value = normalized_phone(body.phone)
    key = private_digest(request, 'phone', value)
    security.limit_otp(client_key(request), key, 'otp_generation')
    code = generate_otp()
    try:
        with session.begin():
            expire(session)
            row = session.scalar(select(Application).where(Application.phone_digest == key).with_for_update())
            if row is None:
                row = Application(phone_digest=key, encrypted_data=encode(request, {'phone': value}), expires_at=now()+timedelta(days=7))
                session.add(row); session.flush()
            if row.status == 'expired':
                row.encrypted_data = encode(request, {'phone': value})
            last = session.scalar(select(SignupChallenge).where(SignupChallenge.application_id == row.id).order_by(SignupChallenge.created_at.desc()))
            if last and (now()-last.created_at).total_seconds() < OTP_RESEND_COOLDOWN_SECONDS:
                raise RateLimitExceeded(retry_after_seconds=OTP_RESEND_COOLDOWN_SECONDS)
            session.execute(update(SignupChallenge).where(SignupChallenge.application_id == row.id).values(used=True))
            challenge = SignupChallenge(application_id=row.id, otp_hash=hash_otp(code), expires_at=now()+timedelta(seconds=OTP_TTL_SECONDS))
            session.add(challenge); session.flush()
            # Send before commit: a delivery failure cannot leave a usable challenge.
            request.app.state.sms.send(value, code, reference='signup:' + str(challenge.id))
            security.record_access('onboarding.phone_challenge', str(row.id))
            return {'challenge_id': str(challenge.id), 'expires_in': OTP_TTL_SECONDS,
                    'resend_after_seconds': OTP_RESEND_COOLDOWN_SECONDS, 'development_otp': request.app.state.dev_sms}
    except IntegrityError: raise Conflict() from None

@router.get('/dev/otp/{challenge_id}')
def development_code(challenge_id: UUID, request: Request, session=Depends(get_session)):
    sandbox(request)
    if not request.app.state.dev_sms: raise NotFound()
    row = session.get(SignupChallenge, challenge_id)
    if not row or row.used or row.expires_at <= now() or row.attempts >= OTP_MAX_ATTEMPTS: raise NotFound()
    code = request.app.state.sms.code_for('signup:' + str(challenge_id))
    if not code: raise NotFound()
    return {'otp': code}

@router.post('/phone/verify')
def phone_verify(body: OTP, request: Request, session=Depends(get_session), security=Depends(get_security)):
    sandbox(request)
    security.limit_otp_challenge(client_key(request), str(body.challenge_id), 'otp_verify')
    failure = None
    result = None
    with session.begin():
        ch = session.scalar(select(SignupChallenge).where(SignupChallenge.id == body.challenge_id).with_for_update())
        if not ch: failure = 'invalid_challenge'
        elif ch.used: failure = 'challenge_consumed'
        elif ch.expires_at <= now(): failure = 'expired'
        elif ch.attempts >= OTP_MAX_ATTEMPTS: failure = 'blocked'
        elif not verify_otp(ch.otp_hash, body.otp):
            ch.attempts += 1
            failure = 'exhausted' if ch.attempts >= OTP_MAX_ATTEMPTS else 'invalid_otp'
        else:
            row = session.scalar(select(Application).where(Application.id == ch.application_id).with_for_update())
            ch.used = True
            if row.status == 'expired':
                # A fresh OTP permits restarting the expired application; no previous evidence is restored.
                row.status = 'draft'; row.expires_at = now()+timedelta(days=7)
                # phone was supplied by the verified challenge's start request.
                row.encrypted_data = encode(request, decode(request, row))
            row.phone_verified = not request.app.state.dev_sms
            security.record_access('onboarding.phone_confirmed', str(row.id))
            result = {**view(row, decode(request, row)), 'application_token': grant(row)}
    if failure: raise OtpError(reason=failure)
    if request.app.state.dev_sms: request.app.state.sms.forget('signup:' + str(body.challenge_id))
    return result

@router.get('/mine')
def mine(request: Request, session=Depends(get_session), security=Depends(get_security)):
    sandbox(request)
    principal = security.authenticate(authorization(request), client_key(request))
    with session.begin():
        expire(session)
        row = session.scalar(select(Application).where(Application.user_id == UUID(principal.subject_id)).with_for_update())
        if not row: raise NotFound()
        return {**view(row, decode(request, row)), 'application_token': grant(row)}

@router.get('/application')
def application(request: Request, session=Depends(get_session)):
    with session.begin():
        row = own(request, session)
        return view(row, decode(request, row))

@router.post('/images')
async def images(request: Request, session=Depends(get_session), security=Depends(get_security), service=Depends(get_identity)):
    with session.begin():
        row = own(request, session, editable=True)
        app_id = row.id
        security.limit_otp(client_key(request), str(app_id), 'otp_generation')
    if request.headers.get('x-evidence-consent') != 'true': raise InvalidInputError('consent required')
    body = await request.body()
    pair = parse_images(request.headers.get('content-type',''), body, request.app.state.runtime.config.identity_processing.max_image_bytes)
    result = await run_in_threadpool(service.extract_application_upload, app_id, lambda: pair)
    with session.begin():
        row = own(request, session, editable=True)
        data = decode(request, row)
        data.update({'ocr': result, 'front': base64.b64encode(pair[0]).decode(), 'back': base64.b64encode(pair[1]).decode()})
        # New evidence invalidates the previous reviewed form until the customer saves it again.
        data.pop('fields', None); data.pop('kyc', None); data.pop('flags', None)
        row.identity_digest = None
        row.expires_at = now()+timedelta(days=7)
        row.encrypted_data = encode(request, data)
        return view(row, data)

class Details(Input):
    full_name: str = Field(min_length=5, max_length=150)
    national_id: str = Field(pattern=r'^[23][0-9]{13}$')
    birth_date: date
    gender: Literal['male','female']
    address: str = Field(min_length=5, max_length=300)
    occupation: str = Field(min_length=2, max_length=120)
    expiry_date: date
    email: str = Field(min_length=5, max_length=254, pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    employment: Literal['employed','self_employed','student','retired','unemployed']
    income_source: str = Field(min_length=3, max_length=150)
    purpose: str = Field(min_length=3, max_length=150)
    expected_activity: Literal['under_10000','10000_50000','over_50000']
    tax_residency: str = Field(min_length=2, max_length=100)
    account_type: Literal['personal_egp']
    consent: Literal[True]

def validate_details(body):
    today = now().date()
    try:
        n = body.national_id
        birth = date((1900 if n[0]=='2' else 2000)+int(n[1:3]), int(n[3:5]), int(n[5:7]))
    except ValueError: raise InvalidInputError('invalid national ID date') from None
    if birth != body.birth_date: raise InvalidInputError('national ID and birth date mismatch')
    age = today.year - birth.year - ((today.month,today.day)<(birth.month,birth.day))
    # Explicit sandbox product rule; not a claim about Egyptian bank eligibility.
    if not 21 <= age <= 120 or body.expiry_date < today: raise InvalidInputError('demo eligibility or expiry')
    if ('male' if int(n[12]) % 2 else 'female') != body.gender: raise InvalidInputError('identity mismatch')
    return age

@router.put('/details')
def details(body: Details, request: Request, session=Depends(get_session), security=Depends(get_security)):
    validate_details(body)
    try:
        with session.begin():
            row = own(request, session, editable=True)
            data = decode(request, row)
            if not data.get('front') or not data.get('back'): raise Conflict()
            fingerprint = private_digest(request, 'identity', body.national_id)
            duplicate = session.scalar(select(Application.id).where(Application.identity_digest == fingerprint, Application.id != row.id))
            if duplicate: raise Conflict()
            row.identity_digest = fingerprint
            fields = body.model_dump(mode='json', include={'full_name','national_id','birth_date','gender','address','occupation','expiry_date'})
            raw = data.get('ocr', {}).get('fields', {})
            flags = ['manual_identity_review_required']
            for name in ('national_id','birth_date','expiry_date'):
                if raw.get(name) != fields[name]: flags.append('review_' + name)
            data.update(fields=fields, kyc=body.model_dump(mode='json', exclude=set(fields)), flags=flags)
            row.encrypted_data = encode(request, data)
            security.record_access('onboarding.details_saved', str(row.id))
            return view(row, data)
    except IntegrityError: raise Conflict() from None

class Credentials(Input):
    username: str = Field(min_length=3, max_length=64)
    password: SecretStr = Field(min_length=12, max_length=256)
    confirmation: SecretStr = Field(min_length=12, max_length=256)

@router.post('/credentials')
def credentials(body: Credentials, request: Request, session=Depends(get_session), security=Depends(get_security)):
    if body.password.get_secret_value() != body.confirmation.get_secret_value(): raise InvalidInputError()
    security.limit_public_attempt(client_key(request))
    try:
        with session.begin():
            row = own(request, session, editable=True)
            data = decode(request, row)
            if not data.get('fields') or not data.get('kyc') or not row.identity_digest: raise Conflict()
            if row.user_id:
                return view(row, data)  # retry never changes existing credentials
            user = create_user(session, body.username, body.password.get_secret_value(), ['applicant'], request.app.state.runtime.authorizer.policy)
            user.phone_e164 = data['phone']
            user.phone_verified_at = now() if row.phone_verified else None
            row.user_id = user.id
            data['username'] = user.username
            row.encrypted_data = encode(request, data)
            security.record_access('onboarding.credentials_created', str(user.id))
            return view(row, data)
    except IntegrityError: raise Conflict() from None

@router.post('/submit')
def submit(request: Request, session=Depends(get_session), security=Depends(get_security)):
    with session.begin():
        row = own(request, session)
        if row.status in {'pending_review','active'}: return view(row)
        if row.status not in {'draft','needs_information'}: raise Conflict()
        data = decode(request, row)
        if not row.user_id or not data.get('front') or not data.get('back') or not row.identity_digest: raise Conflict()
        validate_details(Details(**data['fields'], **data['kyc']))
        row.status = 'pending_review'; row.reason = None
        security.record_access('onboarding.submitted', str(row.user_id))
        return view(row)

@router.post('/cancel')
def cancel(request: Request, session=Depends(get_session), security=Depends(get_security)):
    with session.begin():
        row = own(request, session)
        if row.status == 'active': raise Conflict()
        row.status = 'cancelled'; row.encrypted_data = None; row.identity_digest = None
        security.record_access('onboarding.cancelled', str(row.id))
        return view(row)

@router.get('/reviews')
def reviews(request: Request, principal=Depends(reviewer), session=Depends(get_session), security=Depends(get_security)):
    sandbox(request)
    with session.begin():
        expire(session)
        rows = session.execute(select(Application, User.username).join(User, User.id == Application.user_id).where(Application.status.in_(['pending_review','needs_information'])).order_by(Application.created_at).limit(100)).all()
        security.record_access('onboarding.queue_read', principal.subject_id)
        return {'applications': [{**view(r), 'username': name} for r,name in rows]}

@router.get('/reviews/{app_id}')
def review_detail(app_id: UUID, request: Request, principal=Depends(reviewer), session=Depends(get_session), security=Depends(get_security)):
    sandbox(request)
    row = session.get(Application, app_id)
    if not row or row.status not in {'pending_review','needs_information'} or row.expires_at <= now(): raise NotFound()
    if row.user_id == UUID(principal.subject_id): raise PermissionDenied()
    security.record_access('onboarding.evidence_read', principal.subject_id)
    data = decode(request, row)
    return {**view(row, data), 'front': data.get('front'), 'back': data.get('back')}

class Decision(Input):
    decision: Literal['approved','rejected','needs_information']
    reason: Literal['checked','unclear_images','inconsistent_data','expired_card','missing_information']
    confirmed: Literal[True]

@router.post('/reviews/{app_id}/decision')
def decide(app_id: UUID, body: Decision, request: Request, principal=Depends(reviewer), session=Depends(get_session), security=Depends(get_security)):
    sandbox(request)
    if (body.decision == 'approved') != (body.reason == 'checked'): raise InvalidInputError()
    with session.begin():
        row = session.scalar(select(Application).where(Application.id == app_id).with_for_update())
        if not row: raise NotFound()
        if row.user_id == UUID(principal.subject_id): raise PermissionDenied()
        if row.status == 'active' and body.decision == 'approved': return view(row)
        if row.status != 'pending_review' or row.expires_at <= now(): raise Conflict()
        data = decode(request, row)
        if body.decision == 'approved':
            validate_details(Details(**data['fields'], **data['kyc']))
            if not data.get('front') or not data.get('back') or not row.identity_digest: raise Conflict()
            user = session.scalar(select(User).where(User.id == row.user_id).with_for_update())
            if not user or not user.active or {r.role for r in user.roles} != {'applicant'}: raise Conflict()
            if session.get(DemoCustomer, user.id) or session.scalar(select(DemoAccount.id).where(DemoAccount.owner_id == user.id)): raise Conflict()
            # One atomic activation: approval cannot commit without the zero-balance account.
            session.add(DemoCustomer(owner_id=user.id, verified=True))
            session.add(DemoAccount(owner_id=user.id, balance=0, status='open'))
            user.roles = [UserRole(role='customer'), UserRole(role='identity_processor')]
            row.status = 'active'
        else:
            row.status = body.decision
        row.reason = body.reason; row.reviewer_id = UUID(principal.subject_id)
        if body.decision != 'needs_information':
            data.pop('front', None); data.pop('back', None); data.pop('ocr', None)
            row.encrypted_data = encode(request, data)
        security.record_access('onboarding.' + row.status, principal.subject_id)
        return view(row)

@router.get('/deposit/accounts')
def deposit_accounts(request: Request, principal=Depends(treasurer), session=Depends(get_session), security=Depends(get_security)):
    sandbox(request)
    rows = session.execute(select(DemoAccount, User.username).join(User, User.id==DemoAccount.owner_id).join(DemoCustomer, DemoCustomer.owner_id==User.id).where(User.active.is_(True), DemoCustomer.verified.is_(True), DemoAccount.status=='open').order_by(User.username, DemoAccount.id)).all()
    security.record_access('demo.deposit_accounts_read', principal.subject_id)
    return {'accounts': [{'id':str(a.id), 'account_number':a.account_number, 'username':name, 'balance':str(Decimal(a.balance)/100)} for a,name in rows]}

class DepositInput(Input):
    account_id: UUID
    amount: Decimal = Field(gt=0, le=1000000, max_digits=9, decimal_places=2)
    operation_id: UUID
    note: str = Field(min_length=3, max_length=200)
    confirmed: Literal[True]

@router.post('/deposit', status_code=201)
def deposit(body: DepositInput, request: Request, principal=Depends(treasurer), session=Depends(get_session), security=Depends(get_security)):
    sandbox(request)
    amount = int(body.amount*100)
    try:
        with session.begin():
            # Lock owner before account, same order as ordinary banking mutations.
            owner = session.scalar(select(DemoAccount.owner_id).where(DemoAccount.id==body.account_id))
            if not owner: raise NotFound()
            person = session.scalar(select(DemoCustomer).where(DemoCustomer.owner_id==owner).with_for_update())
            account = session.scalar(select(DemoAccount).where(DemoAccount.id==body.account_id).with_for_update())
            prior = session.get(DemoDeposit, body.operation_id)
            if prior:
                tx = session.get(DemoTransaction, prior.transaction_id)
                if tx.destination_id != body.account_id or tx.amount != amount or prior.actor_id != UUID(principal.subject_id) or prior.note != body.note: raise Conflict()
                return {'transaction_id':str(tx.id), 'reference':tx.reference, 'amount':str(Decimal(tx.amount)/100), 'replayed':True}
            user = session.get(User, owner)
            if not person or not person.verified or not user.active or account.status != 'open': raise Conflict()
            if session.scalar(select(DemoTransaction.id).where(DemoTransaction.operation_id == body.operation_id)): raise Conflict()
            tx = DemoTransaction(operation_id=body.operation_id, source_id=None, destination_id=account.id, amount=amount)
            session.add(tx); session.flush()
            account.balance += amount
            session.add(DemoDeposit(id=body.operation_id, transaction_id=tx.id, actor_id=UUID(principal.subject_id), note=body.note))
            security.record_access('demo.deposit_recorded', principal.subject_id)
            return {'transaction_id':str(tx.id), 'reference':tx.reference, 'amount':str(body.amount), 'replayed':False}
    except IntegrityError: raise Conflict() from None


def purge_expired(database):
    """Called periodically while the API is running; also callable by maintenance tooling."""
    with database.session() as session, session.begin():
        expire(session)
