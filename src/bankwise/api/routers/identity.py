from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool

from bankwise.api.deps import authorization, bearer_scheme, client_key, get_identity, get_session, read_principal
from bankwise.api.schemas import IdentityResponse
from bankwise.api.transport import parse_images
from bankwise.core.errors import BankWiseError, Conflict, InvalidInputError, NotFound, RateLimitExceeded
from bankwise.core.logging import get_correlation_id
from bankwise.database.identity_requests import RequestPersistence, RequestReplay, owned_request

router = APIRouter(prefix="/api/v1/identity", tags=["identity"], dependencies=[Depends(bearer_scheme)])

UPLOAD_SCHEMA = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object", "required": ["front", "back"], "additionalProperties": False,
    "properties": {"front": {"type": "string", "format": "binary"},
                   "back": {"type": "string", "format": "binary"}}}}}},
    "parameters": [{"name": "Idempotency-Key", "in": "header", "required": True,
                    "schema": {"type": "string", "format": "uuid"}}]}


def verify_request(request, body, session, service):
    keys = request.headers.getlist("idempotency-key")
    try:
        key = UUID(keys[0]) if len(keys) == 1 else None
    except ValueError:
        key = None
    if key is None:
        raise InvalidInputError()
    settings = request.app.state.runtime.config.identity_processing
    observer = RequestPersistence(session, key, request.app.state.database.settings.retention_days)
    try:
        service.process_upload(authorization(request), client_key(request),
            lambda: parse_images(request.headers.get("content-type", ""), body, settings.max_image_bytes),
            request_id=get_correlation_id(), observer=observer)
        return observer.record, False
    except RequestReplay as replay:
        row = replay.record
        if row.status in {"processing", "interrupted"}:
            error = Conflict()
            error.request_id = row.id
            raise error from None
        if row.status == "failed":
            error_class = {"validation_failed": InvalidInputError, "rate_limited": RateLimitExceeded}.get(
                row.error_code, BankWiseError)
            error = error_class()
            error.request_id = row.id
            raise error from None
        # Cached success is a new disclosure: require an audit record before release.
        request.app.state.runtime.service.record_access("identity.replayed", str(row.owner_id))
        return row, True


@router.post("/verify", response_model=IdentityResponse, status_code=201, openapi_extra=UPLOAD_SCHEMA)
async def verify(request: Request, response: Response, session=Depends(get_session), service=Depends(get_identity)):
    body = await request.body()  # bounded by TransportMiddleware; parsing happens after the security gate
    row, replayed = await run_in_threadpool(verify_request, request, body, session, service)
    if replayed:
        response.status_code = 200
    response.headers["Location"] = f"/api/v1/identity/requests/{row.id}"
    return IdentityResponse.from_record(row)


@router.get("/requests/{request_id}", response_model=IdentityResponse)
def retrieve(request_id: UUID, request: Request, principal=Depends(read_principal), session=Depends(get_session)):
    try:
        row = owned_request(session, request_id, principal)
    except NotFound:
        request.app.state.runtime.service.record_denial("identity.read_denied", principal.subject_id)
        raise
    # Fail closed for disclosure, as with processing and reveal_sensitive.
    request.app.state.runtime.service.record_access("identity.read", principal.subject_id)
    return IdentityResponse.from_record(row)


@router.post('/extract')
async def extract_own(request: Request, response: Response, service=Depends(get_identity)):
    body = await request.body()
    settings = request.app.state.runtime.config.identity_processing
    result = await run_in_threadpool(service.extract_own_upload, authorization(request), client_key(request),
        lambda: parse_images(request.headers.get('content-type', ''), body, settings.max_image_bytes))
    return result
