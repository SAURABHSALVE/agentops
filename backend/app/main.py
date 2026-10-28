import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import router
from app.config import APP_VERSION, Settings
from app.errors import AgentOpsError, IncidentNotFoundError, MetricNotAvailableError
from app.repository import InMemoryIncidentRepository
from app.schemas import ErrorDetail, ErrorResponse
from app.simulator.service import Simulator

logger = logging.getLogger("agentops")

_STATUS_BY_CODE = {
    IncidentNotFoundError.code: 404,
    MetricNotAvailableError.code: 404,
}


def _envelope(status: int, detail: ErrorDetail) -> JSONResponse:
    return JSONResponse(status_code=status, content=ErrorResponse(error=detail).model_dump(exclude_none=True))


def create_app(simulator: Simulator | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="AgentOps AI API",
        version=APP_VERSION,
        description=(
            "Incident investigation backend. **All data served is simulated demo telemetry**, "
            "not real production or CloudWatch data."
        ),
    )
    app.state.simulator = simulator or Simulator(InMemoryIncidentRepository())

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allow_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(AgentOpsError)
    async def _domain_error(_: Request, exc: AgentOpsError) -> JSONResponse:
        status = _STATUS_BY_CODE.get(exc.code, 400)
        return _envelope(status, ErrorDetail(code=exc.code, message=exc.message))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Report where and why, but never echo the submitted values back.
        details = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return _envelope(
            422, ErrorDetail(code="invalid_request", message="Request validation failed.", details=details)
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error: %s", type(exc).__name__)
        return _envelope(500, ErrorDetail(code="internal_error", message="Internal server error."))

    app.include_router(router)
    return app


app = create_app()
