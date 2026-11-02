import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent.factory import build_provider
from app.agent.provider import LLMProvider
from app.agent.service import InvestigationService
from app.agent.sqlite_store import SqliteInvestigationStore
from app.agent.store import InMemoryInvestigationStore, InvestigationStore
from app.api import router
from app.auth import TokenVerifier, build_verifier
from app.config import APP_VERSION, Settings, load_env_file
from app.errors import (
    AgentOpsError,
    AuthUnavailableError,
    FeedbackNotAllowedError,
    IncidentNotFoundError,
    InvalidTokenError,
    InvestigationInProgressError,
    InvestigationNotFoundError,
    MetricNotAvailableError,
    RateLimitedError,
    RemediationStateError,
    SignInRequiredError,
    UnauthorizedError,
)
from app.repository import InMemoryIncidentRepository
from app.schemas import ErrorDetail, ErrorResponse
from app.security import ACCESS_HEADER, AccessGate, RateLimiter, load_access_code
from app.simulator.captured import CapturedSource
from app.simulator.service import Simulator
from app.simulator.source import FixtureSource, TelemetrySource

logger = logging.getLogger("agentops")
logger.setLevel(logging.INFO)  # Lambda forwards this to CloudWatch; the default would drop the per-request line

MAX_BODY_BYTES = 16 * 1024  # the largest real body is a 500-character feedback note

_STATUS_BY_CODE = {
    IncidentNotFoundError.code: 404,
    MetricNotAvailableError.code: 404,
    InvestigationNotFoundError.code: 404,
    InvestigationInProgressError.code: 409,
    RemediationStateError.code: 409,
    FeedbackNotAllowedError.code: 409,
    UnauthorizedError.code: 401,
    SignInRequiredError.code: 401,
    InvalidTokenError.code: 401,
    AuthUnavailableError.code: 503,
    RateLimitedError.code: 429,
}


def _envelope(status: int, detail: ErrorDetail) -> JSONResponse:
    return JSONResponse(status_code=status, content=ErrorResponse(error=detail).model_dump(exclude_none=True))


def _build_store(settings: Settings) -> InvestigationStore:
    if settings.storage_mode == "dynamodb":
        from app.agent.dynamodb_store import DynamoDbInvestigationStore  # keeps boto3 optional locally

        return DynamoDbInvestigationStore.for_table(settings.dynamodb_table)
    if settings.storage_mode == "local":
        return SqliteInvestigationStore(Path(settings.local_data_dir) / "agentops.sqlite3")
    return InMemoryInvestigationStore()


def _build_simulator(settings: Settings) -> Simulator:
    source: TelemetrySource = (
        CapturedSource.load(Path(settings.telemetry_dataset)) if settings.telemetry_dataset else FixtureSource()
    )
    return Simulator(InMemoryIncidentRepository(source.incidents()), source)


def create_app(
    simulator: Simulator | None = None,
    settings: Settings | None = None,
    provider: LLMProvider | None = None,
    store: InvestigationStore | None = None,
    access: AccessGate | None = None,
    verifier: TokenVerifier | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="AgentOps AI API",
        version=APP_VERSION,
        description=(
            "Incident investigation backend. Data is either simulated demo telemetry (default) or telemetry "
            "captured from a local lab system (`TELEMETRY_DATASET`). Neither is production or CloudWatch data."
        ),
    )
    app.state.simulator = simulator or _build_simulator(settings)
    app.state.investigations = InvestigationService(
        app.state.simulator, store or _build_store(settings), provider or build_provider(settings), settings
    )

    app.state.access = access or AccessGate(load_access_code())
    app.state.verifier = verifier or build_verifier(settings)  # None unless a Cognito user pool is configured
    app.state.limiter = RateLimiter(settings.rate_per_minute, settings.rate_per_hour)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.middleware("http")
    async def _limits_and_trace(request: Request, call_next: Any) -> Any:
        """Refuse oversized bodies, then tag the request with an id and log one line about it.

        The log line holds the method, the path (ids only; no query string, no body) and the outcome, so a
        support question ("what happened at 14:02?") can be answered from CloudWatch without guessing.
        """
        request_id = uuid.uuid4().hex[:16]
        started = time.perf_counter()
        declared = request.headers.get("content-length", "0")
        if not declared.isdigit():
            response: Any = _envelope(400, ErrorDetail(code="invalid_request", message="Invalid Content-Length."))
        elif int(declared) > MAX_BODY_BYTES:
            response = _envelope(413, ErrorDetail(code="payload_too_large", message="Request body is too large."))
        else:
            try:
                response = await call_next(request)
            except Exception:  # noqa: BLE001 - the unhandled-error handler normally answers; this is the last resort
                logger.error("request %s crashed before a response", request_id)
                response = _envelope(500, ErrorDetail(code="internal_error", message="Internal server error."))
        response.headers["X-Request-Id"] = request_id
        logger.info(
            "request %s %s %s -> %s in %d ms",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allow_origins),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Authorization", ACCESS_HEADER],
    )

    @app.exception_handler(AgentOpsError)
    async def _domain_error(_: Request, exc: AgentOpsError) -> JSONResponse:
        status = _STATUS_BY_CODE.get(exc.code, 400)
        response = _envelope(status, ErrorDetail(code=exc.code, message=exc.message))
        if isinstance(exc, RateLimitedError):
            response.headers["Retry-After"] = str(exc.retry_after)
        if isinstance(exc, SignInRequiredError | InvalidTokenError):
            response.headers["WWW-Authenticate"] = "Bearer"
        return response

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


if not os.environ.get("AGENTOPS_NO_DOTENV"):  # tests set this so a real key is never picked up
    load_env_file(Path(__file__).resolve().parent.parent / ".env")
app = create_app()
