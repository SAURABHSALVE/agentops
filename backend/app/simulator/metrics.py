"""Pure, deterministic metric generation from a MetricSpec. No randomness, no wall clock."""

from datetime import datetime, timedelta

from app.schemas import (
    IncidentDetail,
    MetricName,
    MetricPoint,
    MetricsResult,
    MetricSummary,
)
from app.simulator.fixtures import MetricSpec

LEAD = timedelta(minutes=15)  # pre-incident period used to compute the baseline
ONE_MINUTE = timedelta(minutes=1)
BUCKET_MINUTES = 5
ELEVATED_FACTOR = 2.0


def raw_value(spec: MetricSpec, ts: datetime) -> float:
    """Value at a minute-aligned timestamp, with small fixed jitter (+/-2.5%)."""
    value = spec.baseline
    if spec.shape == "step" and spec.onset is not None and ts >= spec.onset:
        if spec.ramp_minutes <= 0 or ts >= spec.onset + timedelta(minutes=spec.ramp_minutes):
            value = spec.fault
        else:
            frac = (ts - spec.onset) / timedelta(minutes=spec.ramp_minutes)
            value = spec.baseline + (spec.fault - spec.baseline) * frac
    elif spec.shape == "spikes" and any(s <= ts < s + timedelta(minutes=spec.spike_minutes) for s in spec.spikes):
        value = spec.fault
    minute_index = int(ts.timestamp() // 60)
    jitter = 1 + ((minute_index * 37) % 11 - 5) / 200
    return round(value * jitter, 2)


def series_span(incident: IncidentDetail) -> tuple[datetime, datetime]:
    return incident.started_at - LEAD, incident.window_end


def _minutes(start: datetime, end: datetime) -> list[datetime]:
    out: list[datetime] = []
    ts = start
    while ts <= end:
        out.append(ts)
        ts += ONE_MINUTE
    return out


def build_metrics_result(incident: IncidentDetail, metric: MetricName, spec: MetricSpec) -> MetricsResult:
    start, end = series_span(incident)
    values = [(ts, raw_value(spec, ts)) for ts in _minutes(start, end)]
    return build_metrics_result_from_values(incident, metric, spec.unit, values)


def build_metrics_result_from_values(
    incident: IncidentDetail, metric: MetricName, unit: str, values: list[tuple[datetime, float]]
) -> MetricsResult:
    """Summarise one value per minute across the incident's lead-in and window (shared by every source)."""
    start, end = series_span(incident)

    lead = [v for ts, v in values if ts < incident.started_at]
    window = [(ts, v) for ts, v in values if ts >= incident.started_at]
    baseline = round(sum(lead) / len(lead), 2)
    elevated = [(ts, v) for ts, v in window if v >= ELEVATED_FACTOR * baseline]

    buckets: dict[int, float] = {}
    for ts, v in values:
        idx = int((ts - start) / timedelta(minutes=BUCKET_MINUTES))
        buckets[idx] = max(buckets.get(idx, v), v)
    points = [
        MetricPoint(timestamp=start + timedelta(minutes=BUCKET_MINUTES * idx), value=v)
        for idx, v in sorted(buckets.items())
    ]

    return MetricsResult(
        incident_id=incident.id,
        service=incident.service,
        metric=metric,
        unit=unit,
        window_start=start,
        window_end=end,
        resolution_minutes=BUCKET_MINUTES,
        aggregation="max",
        points=points,
        summary=MetricSummary(
            baseline=baseline,
            peak=max(v for _, v in window),
            mean_in_window=round(sum(v for _, v in window) / len(window), 2),
            elevated_fraction=round(len(elevated) / len(window), 3),
            first_elevated_at=elevated[0][0] if elevated else None,
        ),
        reference_ids=[f"metric:{metric}:{start:%H:%M}-{end:%H:%M}"],
    )
