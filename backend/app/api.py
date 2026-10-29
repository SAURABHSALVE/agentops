"""HTTP routes: read-only incidents and simulated diagnostics, plus investigations (Phase 3A).

Starting an investigation is the only write. Remediation execution is intentionally NOT exposed
yet: it needs the Phase 5 approval gate.
"""

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Path, Query, Request

from app.agent.models import Investigation
from app.agent.service import InvestigationService
from app.config import APP_VERSION
from app.schemas import (
    DeploymentsQuery,
    DeploymentsResult,
    ErrorResponse,
    GetDeploymentsArgs,
    GetIncidentArgs,
    GetMetricsArgs,
    HealthResponse,
    IncidentDetail,
    IncidentSummary,
    LogSearchQuery,
    LogSearchResult,
    MetricsQuery,
    MetricsResult,
    SearchLogsArgs,
)
from app.simulator.service import Simulator

IncidentIdPath = Annotated[str, Path(pattern=r"^INC-\d{4}$", description="Incident id, e.g. INC-1001")]

InvestigationIdPath = Annotated[str, Path(pattern=r"^inv-\d{4,}$", description="Investigation id, e.g. inv-0001")]

_ERRORS: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
_START_ERRORS: dict[int | str, dict[str, Any]] = {**_ERRORS, 409: {"model": ErrorResponse}}

router = APIRouter()


def _sim(request: Request) -> Simulator:
    sim: Simulator = request.app.state.simulator
    return sim


def _investigations(request: Request) -> InvestigationService:
    svc: InvestigationService = request.app.state.investigations
    return svc


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", data_mode="simulated", version=APP_VERSION)


@router.get("/api/incidents", response_model=list[IncidentSummary], tags=["incidents"])
def list_incidents(request: Request) -> list[IncidentSummary]:
    return _sim(request).list_incidents()


@router.get(
    "/api/incidents/{incident_id}",
    response_model=IncidentDetail,
    responses=_ERRORS,
    tags=["incidents"],
)
def get_incident(incident_id: IncidentIdPath, request: Request) -> IncidentDetail:
    return _sim(request).get_incident(GetIncidentArgs(incident_id=incident_id))


@router.get(
    "/api/incidents/{incident_id}/logs",
    response_model=LogSearchResult,
    responses=_ERRORS,
    tags=["diagnostics (simulated)"],
)
def search_logs(
    incident_id: IncidentIdPath, q: Annotated[LogSearchQuery, Query()], request: Request
) -> LogSearchResult:
    args = SearchLogsArgs(incident_id=incident_id, **q.model_dump())
    return _sim(request).search_logs(args)


@router.get(
    "/api/incidents/{incident_id}/metrics",
    response_model=MetricsResult,
    responses=_ERRORS,
    tags=["diagnostics (simulated)"],
)
def get_metrics(incident_id: IncidentIdPath, q: Annotated[MetricsQuery, Query()], request: Request) -> MetricsResult:
    return _sim(request).get_service_metrics(GetMetricsArgs(incident_id=incident_id, **q.model_dump()))


@router.get(
    "/api/incidents/{incident_id}/deployments",
    response_model=DeploymentsResult,
    responses=_ERRORS,
    tags=["diagnostics (simulated)"],
)
def get_deployments(
    incident_id: IncidentIdPath, q: Annotated[DeploymentsQuery, Query()], request: Request
) -> DeploymentsResult:
    return _sim(request).get_recent_deployments(GetDeploymentsArgs(incident_id=incident_id, **q.model_dump()))


@router.post(
    "/api/incidents/{incident_id}/investigations",
    response_model=Investigation,
    status_code=202,
    responses=_START_ERRORS,
    tags=["investigations"],
)
def start_investigation(incident_id: IncidentIdPath, request: Request, background: BackgroundTasks) -> Investigation:
    """Start an investigation and return immediately; poll the returned id until it is not 'running'."""
    svc = _investigations(request)
    started = svc.start(incident_id)
    background.add_task(svc.run, started.id)
    return started


@router.get(
    "/api/incidents/{incident_id}/investigations/latest",
    response_model=Investigation,
    responses=_ERRORS,
    tags=["investigations"],
)
def latest_investigation(incident_id: IncidentIdPath, request: Request) -> Investigation:
    return _investigations(request).latest(incident_id)


@router.get(
    "/api/investigations/{investigation_id}",
    response_model=Investigation,
    responses=_ERRORS,
    tags=["investigations"],
)
def get_investigation(investigation_id: InvestigationIdPath, request: Request) -> Investigation:
    return _investigations(request).get(investigation_id)
