"""Where the diagnostic tools get their telemetry from.

The Simulator's four read-only tools only ever ask a TelemetrySource. Two sources exist: the built-in
deterministic fixtures (`FixtureSource`, the default) and a dataset captured from a real running system
(`app.simulator.captured`). Everything the agent is allowed to do is enforced above this boundary, so a
different source can supply different data but cannot widen what the agent may call.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Literal, Protocol

from app.errors import MetricNotAvailableError
from app.schemas import (
    DeploymentRecord,
    IncidentDetail,
    LogEntry,
    MetricName,
    MetricsResult,
    VerificationCheck,
)
from app.simulator import fixtures
from app.simulator.metrics import build_metrics_result, raw_value

DataMode = Literal["simulated", "captured"]


class TelemetrySource(Protocol):
    data_mode: DataMode

    def incidents(self) -> Sequence[IncidentDetail]: ...

    def logs(self, incident_id: str) -> Sequence[LogEntry]:
        """All log entries known for the incident (the caller applies the time window and the query)."""
        ...

    def metrics(self, incident: IncidentDetail, metric: MetricName) -> MetricsResult:
        """Raise MetricNotAvailableError if this incident has no such metric."""
        ...

    def deployments(self, service: str, incident_started_at: datetime) -> Sequence[DeploymentRecord]: ...

    def verification(self, incident: IncidentDetail, applied: bool) -> list[VerificationCheck]:
        """Metric checks for the incident, as observed before (applied=False) or after the remediation."""
        ...


class FixtureSource:
    """The built-in, fully fictional and deterministic telemetry."""

    data_mode: DataMode = "simulated"

    def incidents(self) -> Sequence[IncidentDetail]:
        return fixtures.INCIDENTS

    def logs(self, incident_id: str) -> Sequence[LogEntry]:
        return fixtures.LOGS.get(incident_id, ())

    def metrics(self, incident: IncidentDetail, metric: MetricName) -> MetricsResult:
        spec = fixtures.METRICS.get((incident.id, metric))
        if spec is None:
            raise MetricNotAvailableError(f"Metric '{metric}' is not available for service '{incident.service}'.")
        return build_metrics_result(incident, metric, spec)

    def deployments(self, service: str, incident_started_at: datetime) -> Sequence[DeploymentRecord]:
        return fixtures.deployments_for(service, incident_started_at)

    def verification(self, incident: IncidentDetail, applied: bool) -> list[VerificationCheck]:
        checks: list[VerificationCheck] = []
        for v in fixtures.VERIFICATION.get(incident.id, ()):
            observed = (
                v.post_remediation_value
                if applied
                else raw_value(fixtures.METRICS[(incident.id, v.metric)], incident.window_end)
            )
            checks.append(
                VerificationCheck(
                    metric=v.metric,
                    observed=round(observed, 2),
                    threshold=v.threshold,
                    comparison="lt",
                    passed=observed < v.threshold,
                )
            )
        return checks
