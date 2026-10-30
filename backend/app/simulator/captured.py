"""Telemetry captured from a real running system, served through the same four read-only tools.

The dataset file is produced by `python -m lab.capture` (see backend/lab): real log lines and real
measured metrics from a small system with faults injected. It is validated on load, so a malformed or
hand-edited file stops the server at startup instead of producing odd answers later. It is still not
production data, and the file says so in `meta`.
"""

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.config import ConfigError
from app.errors import MetricNotAvailableError
from app.schemas import (
    ChangeType,
    DeploymentRecord,
    IncidentDetail,
    LogEntry,
    MetricName,
    MetricsResult,
    VerificationCheck,
)
from app.simulator.metrics import ONE_MINUTE, build_metrics_result_from_values, series_span
from app.simulator.source import DataMode


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Meta(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    kind: str
    description: str


class _Series(_Strict):
    unit: str
    points: list[tuple[AwareDatetime, float]] = Field(min_length=2)


class _Deploy(_Strict):
    id: str
    service: str
    deployed_at: AwareDatetime
    change_type: ChangeType
    summary: str


class _Check(_Strict):
    metric: MetricName
    threshold: float
    post_remediation_value: float


class _Record(_Strict):
    incident: IncidentDetail
    logs: list[LogEntry]
    metrics: dict[MetricName, _Series]
    deployments: list[_Deploy]
    verification: list[_Check]


class _Dataset(_Strict):
    meta: _Meta
    incidents: list[_Record] = Field(min_length=1)


class CapturedSource:
    data_mode: DataMode = "captured"

    def __init__(self, dataset: _Dataset) -> None:
        self.description = dataset.meta.description
        records = {r.incident.id: r for r in dataset.incidents}
        if len(records) != len(dataset.incidents):
            raise ConfigError("Telemetry dataset has duplicate incident ids.")
        self._records = records
        self._deploys = sorted((d for r in dataset.incidents for d in r.deployments), key=lambda d: d.deployed_at)

    @classmethod
    def load(cls, path: Path) -> "CapturedSource":
        try:
            return cls(_Dataset.model_validate(json.loads(path.read_text(encoding="utf-8"))))
        except OSError:
            raise ConfigError(f"Telemetry dataset not found: {path}") from None
        except ValueError as exc:  # JSON errors and pydantic validation errors
            raise ConfigError(f"Telemetry dataset {path} is not valid: {exc}") from None

    def incidents(self) -> Sequence[IncidentDetail]:
        return [r.incident for r in self._records.values()]

    def logs(self, incident_id: str) -> Sequence[LogEntry]:
        record = self._records.get(incident_id)
        return record.logs if record else ()

    def metrics(self, incident: IncidentDetail, metric: MetricName) -> MetricsResult:
        series = self._records[incident.id].metrics.get(metric)
        if series is None:
            raise MetricNotAvailableError(f"Metric '{metric}' is not available for service '{incident.service}'.")
        by_minute = dict(series.points)
        start, end = series_span(incident)
        values: list[tuple[datetime, float]] = []
        ts = start
        while ts <= end:
            if ts not in by_minute:
                raise MetricNotAvailableError(f"Metric '{metric}' has no data for the incident window.")
            values.append((ts, by_minute[ts]))
            ts += ONE_MINUTE
        return build_metrics_result_from_values(incident, metric, series.unit, values)

    def deployments(self, service: str, incident_started_at: datetime) -> Sequence[DeploymentRecord]:
        return [
            DeploymentRecord(
                id=d.id,
                deployed_at=d.deployed_at,
                service=d.service,
                change_type=d.change_type,
                summary=d.summary,
                minutes_before_incident_start=int((incident_started_at - d.deployed_at).total_seconds() // 60),
            )
            for d in self._deploys
            if d.service == service
        ]

    def verification(self, incident: IncidentDetail, applied: bool) -> list[VerificationCheck]:
        record = self._records[incident.id]
        checks: list[VerificationCheck] = []
        for c in record.verification:
            if applied:
                observed = c.post_remediation_value
            else:
                points = dict(record.metrics[c.metric].points)
                observed = points[incident.window_end]
            checks.append(
                VerificationCheck(
                    metric=c.metric,
                    observed=round(observed, 2),
                    threshold=c.threshold,
                    comparison="lt",
                    passed=observed < c.threshold,
                )
            )
        return checks
