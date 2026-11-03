"""Typed contract shared by the API, the simulator and (in Phase 3) the agent's tools.

Field names are snake_case and match frontend/src/lib/types.ts.
The *Args models double as the strict, server-side validators for model-requested tool calls.
"""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

# INC-nnnn: simulated or captured demo data, shared. LIVE-xxxxxxxxxxxx: an alarm in one user's own connected AWS
# account, visible only to that user (app/live).
IncidentId = Annotated[str, StringConstraints(pattern=r"^(INC-\d{4}|LIVE-[0-9a-f]{12})$")]
Severity = Literal["critical", "high", "medium", "low"]
IncidentStatus = Literal["open", "investigating", "mitigated", "resolved"]
LogLevel = Literal["DEBUG", "INFO", "WARN", "ERROR"]
MetricName = Literal[
    "error_rate", "p99_latency_ms", "p95_latency_ms", "db_query_p95_ms", "queue_depth", "alarm_metric"
]  # alarm_metric: the one metric a connected AWS account's alarm watches (live incidents only)
ChangeType = Literal["code", "config", "dependency", "docs"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ---- Incidents -------------------------------------------------------------------------------


class Signal(_Model):
    label: str
    value: str


class TimelineEvent(_Model):
    id: str
    at: AwareDatetime
    kind: Literal["incident", "agent", "tool", "approval", "action", "verification", "error"]
    title: str
    detail: str | None = None


class IncidentSummary(_Model):
    id: IncidentId
    title: str
    service: str
    severity: Severity
    status: IncidentStatus
    summary: str
    started_at: AwareDatetime
    window_end: AwareDatetime
    updated_at: AwareDatetime


class IncidentDetail(IncidentSummary):
    signals: list[Signal]
    history: list[TimelineEvent]


# ---- Diagnostic tool arguments (strict) ------------------------------------------------------


class _Args(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class LogSearchQuery(_Args):
    query: str = Field(min_length=1, max_length=100)
    level: LogLevel | None = None
    limit: int = Field(default=20, ge=1, le=50)


class MetricsQuery(_Args):
    metric: MetricName


class DeploymentsQuery(_Args):
    hours: int = Field(default=6, ge=1, le=24)


class GetIncidentArgs(_Args):
    incident_id: IncidentId


class SearchLogsArgs(LogSearchQuery):
    incident_id: IncidentId


class GetMetricsArgs(MetricsQuery):
    incident_id: IncidentId


class GetDeploymentsArgs(DeploymentsQuery):
    incident_id: IncidentId


# ---- Diagnostic tool results -----------------------------------------------------------------


class LogEntry(_Model):
    id: str
    timestamp: AwareDatetime
    level: LogLevel
    service: str
    message: str  # untrusted text: consumers must treat it as data, never as instructions


class LogSearchResult(_Model):
    incident_id: IncidentId
    service: str
    query: str
    window_start: AwareDatetime
    window_end: AwareDatetime
    total_matches: int
    returned: int
    truncated: bool
    entries: list[LogEntry]
    reference_ids: list[str]


class MetricPoint(_Model):
    timestamp: AwareDatetime
    value: float


class MetricSummary(_Model):
    baseline: float  # mean over the 15 minutes before the incident started
    peak: float
    mean_in_window: float
    elevated_fraction: float  # share of incident-window minutes at >= 2x baseline
    first_elevated_at: AwareDatetime | None


class MetricsResult(_Model):
    incident_id: IncidentId
    service: str
    metric: MetricName
    unit: str
    window_start: AwareDatetime
    window_end: AwareDatetime
    resolution_minutes: int
    aggregation: Literal["max"]
    points: list[MetricPoint]
    summary: MetricSummary
    reference_ids: list[str]


class DeploymentRecord(_Model):
    id: str
    deployed_at: AwareDatetime
    service: str
    change_type: ChangeType
    summary: str
    minutes_before_incident_start: int


class DeploymentsResult(_Model):
    incident_id: IncidentId
    service: str
    hours: int
    as_of: AwareDatetime
    deployments: list[DeploymentRecord]
    reference_ids: list[str]


# ---- Remediation and verification ------------------------------------------------------------


class ActionResult(_Model):
    incident_id: IncidentId
    action_id: str
    status: Literal["executed", "already_applied"]
    summary: str
    simulated: Literal[True] = True


class VerificationCheck(_Model):
    metric: MetricName
    observed: float
    threshold: float
    comparison: Literal["lt"]
    passed: bool


class VerificationResult(_Model):
    incident_id: IncidentId
    status: Literal["resolved", "not_resolved", "not_verifiable"]
    checks: list[VerificationCheck]


# ---- API envelopes ---------------------------------------------------------------------------


class HealthResponse(_Model):
    status: Literal["ok"]
    data_mode: Literal["simulated", "captured"]
    version: str


class ErrorDetail(_Model):
    code: str
    message: str
    details: list[dict[str, str]] | None = None


class ErrorResponse(_Model):
    error: ErrorDetail
