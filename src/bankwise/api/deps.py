from fastapi import Depends, Request
from fastapi.security import HTTPBearer

# OpenAPI declaration only; SecurityService performs actual authentication and lockout checks.
bearer_scheme = HTTPBearer(auto_error=False)


def get_database(request: Request):
    return request.app.state.database


def get_session(database=Depends(get_database)):
    with database.session() as session:
        yield session


def get_security(request: Request):
    return request.app.state.runtime.service


def get_identity(request: Request):
    return request.app.state.identity


def client_key(request: Request):
    # Uvicorn must run with --no-proxy-headers unless a trusted proxy is configured explicitly.
    return "ip:" + (request.client.host if request.client else "unknown")


def authorization(request: Request):
    values = request.headers.getlist("authorization")
    return values[0] if len(values) == 1 else None


def read_principal(request: Request, security=Depends(get_security)):
    return security.guard(authorization(request), "identity:read", client_key(request), rate_policy="identity_submit")
