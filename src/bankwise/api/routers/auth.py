"""Authentication endpoints. Login is two-step: credentials create an OTP challenge (no token); only OTP
verification issues a role-scoped access token. New signup uses the reviewed onboarding router; logout is unchanged.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field, SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bankwise.api.deps import authorization, bearer_scheme, client_key, get_security, get_session
from bankwise.api.schemas import (LoginRequest, OtpChallengeResponse, OtpResendRequest, OtpVerifyRequest,
                                  TokenResponse)
from bankwise.core.errors import AuthenticationError, Conflict, NotFound, OtpError
from bankwise.core.otp import generate_otp, hash_otp
from bankwise.database.accounts import create_user
from bankwise.database.models import DemoAccount, DemoCustomer, User
from bankwise.database.otp import OtpChallengeStore

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=OtpChallengeResponse)
def login(body: LoginRequest, request: Request, security=Depends(get_security), session=Depends(get_session)):
    """Step 1: verify username/password, then send a code to the number already on file. No token is issued."""
    principal = security.login(body.username, body.password.get_secret_value(), client_key(request),
                               request.app.state.accounts.verify)
    user = session.get(User, UUID(principal.subject_id))
    # A missing/unverified destination is indistinguishable from a bad login: no enumeration, no token.
    if user is None or not user.active or (not request.app.state.dev_sms and (not user.phone_verified_at or not user.phone_e164)):
        raise AuthenticationError()
    client = client_key(request)
    security.limit_otp(client, principal.subject_id, "otp_generation")
    code = generate_otp()
    challenge = OtpChallengeStore(session).start(user.id, user.phone_e164 or "", hash_otp(code))
    request.app.state.sms.send(user.phone_e164 or "", code, reference=str(challenge.challenge_id))
    security.record_access("auth.otp_sent", principal.subject_id)
    return OtpChallengeResponse(challenge_id=challenge.challenge_id, expires_in=challenge.expires_in,
                                resend_after_seconds=challenge.resend_after_seconds,
                                development_otp=request.app.state.dev_sms)


@router.post("/login/otp/verify", response_model=TokenResponse)
def verify_otp(body: OtpVerifyRequest, request: Request, security=Depends(get_security),
               session=Depends(get_session)):
    """Step 2: validate the single-use challenge and only then issue a full access token."""
    security.limit_otp_challenge(client_key(request), str(body.challenge_id), "otp_verify")
    result = OtpChallengeStore(session).verify(body.challenge_id, body.otp, security.issue_token,
                                                       development_mock=request.app.state.dev_sms)
    if request.app.state.dev_sms:
        request.app.state.sms.forget(str(body.challenge_id))
    customer = session.scalar(select(DemoCustomer).join(User, User.id == DemoCustomer.owner_id)
                              .where(User.id == UUID(result.subject_id)))
    return TokenResponse(kyc_pending=customer is not None and not customer.verified,
                         access_token=result.token,
                         expires_in=request.app.state.runtime.config.auth.access_token_ttl_seconds)


@router.post("/login/otp/resend", response_model=OtpChallengeResponse)
def resend_otp(body: OtpResendRequest, request: Request, security=Depends(get_security),
               session=Depends(get_session)):
    """Replace the current challenge after the cooldown, invalidating every earlier code in the flow."""
    store = OtpChallengeStore(session)
    owner = store.owner_of(body.challenge_id)
    if owner is None:
        raise OtpError(reason="invalid_challenge")
    security.limit_otp(client_key(request), str(owner), "otp_resend")
    user = session.get(User, owner)
    if user is None or not user.active or (not request.app.state.dev_sms and (not user.phone_verified_at or not user.phone_e164)):
        raise OtpError(reason="invalid_challenge")
    code = generate_otp()
    challenge = store.resend(owner, body.challenge_id, hash_otp(code))
    request.app.state.sms.send(user.phone_e164 or "", code, reference=str(challenge.challenge_id))
    security.record_access("auth.otp_resent", str(owner))
    return OtpChallengeResponse(challenge_id=challenge.challenge_id, expires_in=challenge.expires_in,
                                resend_after_seconds=challenge.resend_after_seconds,
                                development_otp=request.app.state.dev_sms)


@router.get("/dev/otp/{challenge_id}", include_in_schema=False)
def dev_otp(challenge_id: UUID, request: Request):
    """Development-only read of a delivered mock code. Always 404 in production (mock provider is not built)."""
    if not request.app.state.dev_sms:
        raise NotFound()
    code = request.app.state.sms.code_for(str(challenge_id))
    if code is None:
        raise NotFound()
    return {"challenge_id": challenge_id, "otp": code}


@router.post("/logout", status_code=204, dependencies=[Depends(bearer_scheme)])
def logout(request: Request, security=Depends(get_security)):
    header = authorization(request)
    security.authenticate(header, client_key(request))  # preserves lockout and active-account checks
    security.revoke(header)
    return Response(status_code=204)


class SignupRequest(LoginRequest):
    password: SecretStr = Field(min_length=12, max_length=256)


@router.post('/signup', status_code=201)
def signup(body: SignupRequest, request: Request, session=Depends(get_session), security=Depends(get_security)):
    # Legacy registration cannot bypass the reviewed onboarding workflow.
    raise NotFound()
