"""HTTP routes: incidents, simulated diagnostics, investigations and the approval gate.

Writes: start an investigation, approve or reject the remediation it proposed, and leave feedback. The
first three take no request body, so a client cannot choose an action, a tool or an incident beyond the path
ids. Feedback takes a verdict and a short note, which are stored and never reach a model or a tool.
"""

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Path, Query, Request

from app.agent.models import FeedbackRequest, Investigation
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
    TimelineEvent,
)
from app.security import ACCESS_HEADER
from app.simulator.service import Simulator

IncidentIdPath = Annotated[str, Path(pattern=r"^INC-\d{4}$", description="Incident id, e.g. INC-1001")]

InvestigationIdPath = Annotated[str, Path(pattern=r"^inv-\d{4,}$", description="Investigation id, e.g. inv-0001")]

_ERRORS: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
_START_ERRORS: dict[int | str, dict[str, Any]] = {**_ERRORS, 409: {"model": ErrorResponse}}

router = APIRouter()


def require_access(request: Request, x_access_code: Annotated[str | None, Header(alias=ACCESS_HEADER)] = None) -> None:
    """Gate for every write. A no-op unless an access code is configured."""
    request.app.state.access.check(x_access_code)


def _sim(request: Request) -> Simulator:
    sim: Simulator = request.app.state.simulator
    return sim


def _investigations(request: Request) -> InvestigationService:
    svc: InvestigationService = request.app.state.investigations
    return svc


@router.get("/api/config", tags=["system"])
def public_config(request: Request) -> dict[str, Any]:
    """What the UI needs to know up front. Contains nothing secret."""
    info = _investigations(request).provider_info
    return {
        "data_mode": _sim(request).data_mode,
        "write_access": "code_required" if request.app.state.access.required else "open",
        "provider": {"kind": info.kind, "label": info.label},
    }


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health(request: Request) -> HealthResponse:
    return HealthResponse(status="ok", data_mode=_sim(request).data_mode, version=APP_VERSION)


@router.get("/api/incidents", response_model=list[IncidentSummary], tags=["incidents"])
def list_incidents(request: Request) -> list[IncidentSummary]:
    svc = _investigations(request)
    return [svc.with_status(i) for i in _sim(request).list_incidents()]


@router.get(
    "/api/incidents/{incident_id}",
    response_model=IncidentDetail,
    responses=_ERRORS,
    tags=["incidents"],
)
def get_incident(incident_id: IncidentIdPath, request: Request) -> IncidentDetail:
    incident = _sim(request).get_incident(GetIncidentArgs(incident_id=incident_id))
    return _investigations(request).with_status_detail(incident)


@router.get(
    "/api/incidents/{incident_id}/timeline",
    response_model=list[TimelineEvent],
    responses=_ERRORS,
    tags=["incidents"],
)
def get_timeline(incident_id: IncidentIdPath, request: Request) -> list[TimelineEvent]:
    """Fixture history plus every recorded investigation, tool call, approval, action and verification."""
    return _investigations(request).timeline(incident_id)


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
    dependencies=[Depends(require_access)],
)
def start_investigation(incident_id: IncidentIdPath, request: Request, background: BackgroundTasks) -> Investigation:
    """Start an investigation and return immediately; poll the returned id until it is not 'running'."""
    svc = _investigations(request)
    request.app.state.limiter.check()  # before any record exists, so a refusal never leaves one 'running'
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


@router.post(
    "/api/investigations/{investigation_id}/remediation/approve",
    response_model=Investigation,
    responses=_START_ERRORS,
    tags=["approval"],
    dependencies=[Depends(require_access)],
)
def approve_remediation(investigation_id: InvestigationIdPath, request: Request) -> Investigation:
    """Approve the remediation proposed by this investigation, run it (simulated) and verify it.

    Takes no body: the action is the one the server validated and stored. Repeating this call
    returns the recorded outcome and does not run the action again.
    """
    return _investigations(request).approve(investigation_id)


@router.post(
    "/api/investigations/{investigation_id}/remediation/reject",
    response_model=Investigation,
    responses=_START_ERRORS,
    tags=["approval"],
    dependencies=[Depends(require_access)],
)
def reject_remediation(investigation_id: InvestigationIdPath, request: Request) -> Investigation:
    return _investigations(request).reject(investigation_id)


@router.post(
    "/api/investigations/{investigation_id}/feedback",
    response_model=Investigation,
    responses=_START_ERRORS,
    tags=["feedback"],
    dependencies=[Depends(require_access)],
)
def give_feedback(investigation_id: InvestigationIdPath, body: FeedbackRequest, request: Request) -> Investigation:
    """Say whether a finished investigation helped. The note is stored for the team and never sent to a model."""
    return _investigations(request).give_feedback(investigation_id, body)
