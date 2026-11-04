"""HTTP routes: incidents, simulated diagnostics, investigations and the approval gate.

Writes: start an investigation, approve or reject the remediation it proposed, and leave feedback. The
first three take no request body, so a client cannot choose an action, a tool or an incident beyond the path
ids. Feedback takes a verdict and a short note, which are stored and never reach a model or a tool.
The AWS-connection routes (Phase 16) take small, closed bodies (a region, log group names, an agreement, a role ARN);
they only describe where to look in the caller's own account and run nothing.

Who is calling comes only from a verified bearer token (app/auth.py), never from a header or body the client
chooses. Investigations, approvals and feedback belong to that user. Incidents and diagnostics are shared and read-only.
"""

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Path, Query, Request
from fastapi.responses import JSONResponse

from app.agent.models import FeedbackRequest, Investigation, InvestigationSummary
from app.agent.service import InvestigationService
from app.config import APP_VERSION
from app.connections.models import ConnectionSettings, ConnectionView, RoleRegistration
from app.connections.service import ConnectionService
from app.connections.template import TEMPLATE_FILE
from app.errors import InvalidTokenError, SignInRequiredError
from app.identity import ANONYMOUS_OWNER
from app.live.models import LiveIncidentsView
from app.live.service import LiveService
from app.schemas import (
    DeploymentsQuery,
    DeploymentsResult,
    ErrorResponse,
    GetDeploymentsArgs,
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

# Any incident the caller may open: a shared demo one, or (LIVE-...) one from their own connected AWS account. The
# public diagnostic routes keep the stricter IncidentIdPath: they take no sign-in, so they never serve a real account.
AnyIncidentIdPath = Annotated[
    str, Path(pattern=r"^(INC-\d{4}|LIVE-[0-9a-f]{12})$", description="Incident id, e.g. INC-1001 or LIVE-0a1b2c3d4e5f")
]

InvestigationIdPath = Annotated[str, Path(pattern=r"^inv-\d{4,}$", description="Investigation id, e.g. inv-0001")]

_ERRORS: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
_START_ERRORS: dict[int | str, dict[str, Any]] = {**_ERRORS, 409: {"model": ErrorResponse}}

router = APIRouter()


def caller(request: Request, authorization: Annotated[str | None, Header()] = None) -> str:
    """The owner id of whoever is asking: a verified user, or the shared anonymous owner without a token.

    With sign-in off the header is ignored. With it on, a token that is present but bad is refused outright
    (rather than quietly treated as anonymous), so an expired session shows up as "sign in again".
    """
    verifier = request.app.state.verifier
    if verifier is None or authorization is None:
        return ANONYMOUS_OWNER
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise InvalidTokenError("This sign-in is not valid. Sign in again.")
    return str(verifier.verify(token.strip()).sub)


def signed_in(request: Request, owner: Annotated[str, Depends(caller)]) -> str:
    """Gate for every write once sign-in is configured: the caller must be a real user."""
    if request.app.state.verifier is not None and owner == ANONYMOUS_OWNER:
        raise SignInRequiredError("Sign in to do this.")
    return owner


Owner = Annotated[str, Depends(caller)]


def _is_user(request: Request, owner: str) -> bool:
    """A real signed-in user (not the shared anonymous owner, and sign-in is on)."""
    return request.app.state.verifier is not None and owner != ANONYMOUS_OWNER


def require_access(
    request: Request, owner: Owner, x_access_code: Annotated[str | None, Header(alias=ACCESS_HEADER)] = None
) -> None:
    """Gate for every write. A no-op unless an access code is configured.

    A signed-in user who presents the right code once is remembered as invited and need not send it again. The check
    for a wrong or missing code is unchanged, so an uninvited caller learns nothing.
    """
    gate = request.app.state.access
    if not gate.required:
        return
    usage = request.app.state.usage
    if _is_user(request, owner) and usage.is_invited(owner):
        return
    gate.check(x_access_code)
    if _is_user(request, owner):
        usage.invite(owner)


SignedInOwner = Annotated[str, Depends(signed_in)]


def _sim(request: Request) -> Simulator:
    sim: Simulator = request.app.state.simulator
    return sim


def _investigations(request: Request, owner: str = ANONYMOUS_OWNER) -> InvestigationService:
    """The investigation service acting for `owner`. Every route that touches investigations passes one."""
    svc: InvestigationService = request.app.state.investigations
    return svc.scoped(owner)


def _live(request: Request) -> LiveService:
    svc: LiveService = request.app.state.live
    return svc


def _connections(request: Request) -> ConnectionService:
    svc: ConnectionService = request.app.state.connections
    return svc


@router.get("/api/config", tags=["system"])
def public_config(request: Request, owner: Owner) -> dict[str, Any]:
    """What the UI needs to know up front. Contains nothing secret. `write_access` is for the caller: a user who has
    already been invited no longer needs the code."""
    info = _investigations(request).provider_info
    needs_code = request.app.state.access.required and not (
        _is_user(request, owner) and request.app.state.usage.is_invited(owner)
    )
    return {
        "data_mode": _sim(request).data_mode,
        "write_access": "code_required" if needs_code else "open",
        "auth": {"mode": "none" if request.app.state.verifier is None else "cognito"},
        "provider": {"kind": info.kind, "label": info.label},
        "aws_connect": {"available": _connections(request).available_for_anyone()},
    }


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health(request: Request) -> HealthResponse:
    return HealthResponse(status="ok", data_mode=_sim(request).data_mode, version=APP_VERSION)


@router.get("/api/incidents", response_model=list[IncidentSummary], tags=["incidents"])
def list_incidents(request: Request, owner: Owner) -> list[IncidentSummary]:
    svc = _investigations(request, owner)
    return [svc.with_status(i) for i in _sim(request).list_incidents()]


@router.get(
    "/api/incidents/{incident_id}",
    response_model=IncidentDetail,
    responses=_ERRORS,
    tags=["incidents"],
)
def get_incident(incident_id: AnyIncidentIdPath, request: Request, owner: Owner) -> IncidentDetail:
    svc = _investigations(request, owner)
    return svc.with_status_detail(svc.incident(incident_id))


@router.get(
    "/api/incidents/{incident_id}/timeline",
    response_model=list[TimelineEvent],
    responses=_ERRORS,
    tags=["incidents"],
)
def get_timeline(incident_id: AnyIncidentIdPath, request: Request, owner: Owner) -> list[TimelineEvent]:
    """Fixture history plus the caller's own investigations, tool calls, approvals, actions and verifications."""
    return _investigations(request, owner).timeline(incident_id)


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
def start_investigation(
    incident_id: AnyIncidentIdPath, request: Request, background: BackgroundTasks, owner: SignedInOwner
) -> Investigation:
    """Start an investigation and return immediately; poll the returned id until it is not 'running'."""
    svc = _investigations(request, owner)
    # Before any record exists, so a refusal never leaves one 'running'. The person's own cap first: someone over
    # it is turned away without using up the shared allowance.
    request.app.state.user_limiter.check(owner)
    request.app.state.limiter.check()
    caps = request.app.state.caps
    caps.charge(owner)  # before any record exists, so a refusal never leaves one 'running'
    try:
        started = svc.start(incident_id)
    except Exception:
        caps.refund(owner)  # an investigation that could not start must not use up the day's allowance
        raise
    background.add_task(svc.run, started.id)
    return started


@router.get("/api/me/investigations", response_model=list[InvestigationSummary], tags=["account"])
def my_investigations(request: Request, owner: SignedInOwner) -> list[InvestigationSummary]:
    """The caller's newest investigation of each incident they have investigated, newest first."""
    return _investigations(request, owner).mine()


@router.delete(
    "/api/me/investigations",
    responses=_START_ERRORS,
    tags=["account"],
    dependencies=[Depends(require_access)],
)
def delete_my_investigations(request: Request, owner: SignedInOwner) -> dict[str, int]:
    """Delete everything the caller has recorded: investigations, approvals, feedback and applied simulated fixes.

    Their sign-in account is untouched. Takes no body. Refused (409) while one of their investigations is running.
    """
    return {"deleted": _investigations(request, owner).delete_mine()}


@router.get("/api/me/aws-connection", response_model=ConnectionView, tags=["aws-connection"])
def aws_connection(request: Request, owner: Owner) -> ConnectionView:
    """The caller's AWS connection, plus what they need to set one up. `available: false` says why it cannot be used."""
    return _connections(request).view(owner)


@router.put(
    "/api/me/aws-connection",
    response_model=ConnectionView,
    responses=_START_ERRORS,
    tags=["aws-connection"],
    dependencies=[Depends(require_access)],
)
def save_aws_connection(body: ConnectionSettings, request: Request, owner: SignedInOwner) -> ConnectionView:
    """Step 1: the region and log groups to look at, and the agreement to how the data is used."""
    return _connections(request).save_settings(owner, body)


@router.get("/api/me/aws/incidents", response_model=LiveIncidentsView, responses=_START_ERRORS, tags=["aws-connection"])
def live_incidents(request: Request, owner: SignedInOwner) -> LiveIncidentsView:
    """The alarms firing in the caller's own connected AWS account right now, read through their read-only role."""
    return _live(request).refresh(owner)


@router.get("/api/me/aws-connection/template", responses=_START_ERRORS, tags=["aws-connection"])
def aws_connection_template(request: Request, owner: SignedInOwner) -> JSONResponse:
    """Step 2: the CloudFormation template that creates the read-only role in the caller's own account."""
    return JSONResponse(
        content=_connections(request).template(owner),
        headers={"Content-Disposition": f'attachment; filename="{TEMPLATE_FILE}"'},
    )


@router.put(
    "/api/me/aws-connection/role",
    response_model=ConnectionView,
    responses=_START_ERRORS,
    tags=["aws-connection"],
    dependencies=[Depends(require_access)],
)
def register_aws_role(body: RoleRegistration, request: Request, owner: SignedInOwner) -> ConnectionView:
    """Step 3: the ARN of the role the template created. Stored as given; it is proven to work by using it."""
    return _connections(request).register_role(owner, body)


@router.delete(
    "/api/me/aws-connection",
    response_model=ConnectionView,
    responses=_START_ERRORS,
    tags=["aws-connection"],
    dependencies=[Depends(require_access)],
)
def disconnect_aws(request: Request, owner: SignedInOwner) -> ConnectionView:
    """Forget the connection. The role in the user's account stays until they delete its stack."""
    return _connections(request).disconnect(owner)


@router.get(
    "/api/incidents/{incident_id}/investigations/latest",
    response_model=Investigation | None,
    responses=_ERRORS,
    tags=["investigations"],
)
def latest_investigation(incident_id: AnyIncidentIdPath, request: Request, owner: Owner) -> Investigation | None:
    """The caller's newest investigation of this incident, or `null` if they have not run one yet.

    "Nothing yet" is a normal state, not an error, so it is a 200: a 404 here would show up as a failed request in
    every first-time visitor's browser, and CloudFront would replace its body with the site's HTML 404 page.
    """
    return _investigations(request, owner).latest(incident_id)


@router.get(
    "/api/investigations/{investigation_id}",
    response_model=Investigation,
    responses=_ERRORS,
    tags=["investigations"],
)
def get_investigation(investigation_id: InvestigationIdPath, request: Request, owner: Owner) -> Investigation:
    return _investigations(request, owner).get(investigation_id)


@router.post(
    "/api/investigations/{investigation_id}/remediation/approve",
    response_model=Investigation,
    responses=_START_ERRORS,
    tags=["approval"],
    dependencies=[Depends(require_access)],
)
def approve_remediation(investigation_id: InvestigationIdPath, request: Request, owner: SignedInOwner) -> Investigation:
    """Approve the remediation proposed by this investigation, run it (simulated) and verify it.

    Takes no body: the action is the one the server validated and stored. Repeating this call
    returns the recorded outcome and does not run the action again.
    """
    return _investigations(request, owner).approve(investigation_id)


@router.post(
    "/api/investigations/{investigation_id}/remediation/reject",
    response_model=Investigation,
    responses=_START_ERRORS,
    tags=["approval"],
    dependencies=[Depends(require_access)],
)
def reject_remediation(investigation_id: InvestigationIdPath, request: Request, owner: SignedInOwner) -> Investigation:
    return _investigations(request, owner).reject(investigation_id)


@router.post(
    "/api/investigations/{investigation_id}/feedback",
    response_model=Investigation,
    responses=_START_ERRORS,
    tags=["feedback"],
    dependencies=[Depends(require_access)],
)
def give_feedback(
    investigation_id: InvestigationIdPath, body: FeedbackRequest, request: Request, owner: SignedInOwner
) -> Investigation:
    """Say whether a finished investigation helped. The note is stored for the team and never sent to a model."""
    return _investigations(request, owner).give_feedback(investigation_id, body)
