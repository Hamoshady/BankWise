import logging
from uuid import UUID

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from bankwise.core.errors import (BankWiseError, ErrorCode, ResourceErrorCode, InvalidInputError,
                                 PayloadTooLarge, ServiceUnavailable, to_public_error)
from bankwise.core.logging import get_correlation_id

STATUS_CODES = {ErrorCode.AUTHENTICATION_FAILED.value: 401, ErrorCode.PERMISSION_DENIED.value: 403,
                ErrorCode.RATE_LIMITED.value: 429, ErrorCode.OTP_FAILED.value: 401,
                ErrorCode.VALIDATION_FAILED.value: 422,
                ResourceErrorCode.NOT_FOUND.value: 404, ResourceErrorCode.CONFLICT.value: 409,
                ResourceErrorCode.SERVICE_UNAVAILABLE.value: 503}


def error_response(error, status=None):
    if isinstance(error, PayloadTooLarge):
        status = 413
    public = to_public_error(error)
    headers = {}
    if public["code"] == "authentication_failed":
        headers["WWW-Authenticate"] = "Bearer"
    if "retry_after_seconds" in public:
        headers["Retry-After"] = str(public["retry_after_seconds"])
    resource_id = getattr(error, "request_id", None)
    if isinstance(resource_id, UUID):
        headers["Location"] = f"/api/v1/identity/requests/{resource_id}"
    return JSONResponse({"error": public, "request_id": str(resource_id) if isinstance(resource_id, UUID) else get_correlation_id()},
                        status_code=status or STATUS_CODES.get(public["code"], 500), headers=headers)


def install_error_handlers(app):
    @app.exception_handler(BankWiseError)
    async def bankwise_error(request, error):
        return error_response(error)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Pydantic's default errors can echo passwords, body data and headers.
        return error_response(InvalidInputError())

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, error):
        logging.getLogger("bankwise.api").error("database operation failed")
        return error_response(ServiceUnavailable())

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        from bankwise.core.errors import NotFound
        return error_response(NotFound() if error.status_code == 404 else InvalidInputError(), error.status_code)
