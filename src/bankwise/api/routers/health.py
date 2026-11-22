from fastapi import APIRouter, Depends, Request

from bankwise.api.deps import get_database
from bankwise.api.schemas import HealthResponse
from bankwise.core.errors import ServiceUnavailable

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse)
def live():
    return HealthResponse(status="live")


@router.get("/ready", response_model=HealthResponse)
def ready(request: Request, database=Depends(get_database)):
    try:
        database.ready()
        if not request.app.state.initialized:
            raise ServiceUnavailable()
    except Exception:
        raise ServiceUnavailable() from None
    return HealthResponse(status="ready")
