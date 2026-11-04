"""Turn a CloudWatch alarm that is in ALARM state into the incident the rest of AgentOps already understands.

An alarm is a good incident: it has a name, a metric, a threshold and a time it went wrong. What it does not have is a
severity, a service name or a history, so those are made from what the alarm does say and labelled as such.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from app.live.models import MAX_DIMENSIONS, Dimension, LiveAlarm
from app.redaction import redact
from app.schemas import IncidentDetail, Signal, TimelineEvent

# An alarm can sit in ALARM for days. The investigation looks at its most recent hours, up to when it was read.
MAX_WINDOW = timedelta(hours=3)

_COMPARISONS = {
    "GreaterThanThreshold": ">",
    "GreaterThanOrEqualToThreshold": ">=",
    "LessThanThreshold": "<",
    "LessThanOrEqualToThreshold": "<=",
    "LessThanLowerOrGreaterThanUpperThreshold": "outside the band of",
    "LessThanLowerThreshold": "<",
    "GreaterThanUpperThreshold": ">",
}


def alarm_id(region: str, name: str) -> str:
    """A stable id for one alarm, so the same alarm keeps its investigation history when it fires again."""
    digest = hashlib.sha256(f"{region}\n{name}".encode()).hexdigest()
    return f"LIVE-{digest[:12]}"


def _text(value: object, limit: int) -> str:
    """Free text written by whoever set the alarm up: masked, then bounded."""
    return redact(value)[:limit] if isinstance(value, str) else ""


def _aware(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def alarm_from_raw(raw: dict[str, Any], region: str, now: datetime) -> LiveAlarm | None:
    """One DescribeAlarms entry as a LiveAlarm, or None when it is missing what an incident needs."""
    name = raw.get("AlarmName")
    since = _aware(raw.get("StateTransitionedTimestamp")) or _aware(raw.get("StateUpdatedTimestamp"))
    if not isinstance(name, str) or not name or since is None:
        return None
    dimensions = tuple(
        Dimension(name=d["Name"], value=d["Value"])
        for d in raw.get("Dimensions", [])[:MAX_DIMENSIONS]
        if isinstance(d.get("Name"), str) and d["Name"] and isinstance(d.get("Value"), str) and d["Value"]
    )
    threshold = raw.get("Threshold")
    period = raw.get("Period")
    unit = raw.get("Unit")
    return LiveAlarm(
        id=alarm_id(region, name),
        name=name[:255],
        region=region,
        namespace=raw.get("Namespace") if isinstance(raw.get("Namespace"), str) else None,
        metric_name=raw.get("MetricName") if isinstance(raw.get("MetricName"), str) else None,
        dimensions=dimensions,
        statistic=raw.get("ExtendedStatistic") or raw.get("Statistic") or None,
        period_seconds=period if isinstance(period, int) and 1 <= period <= 86_400 else 60,
        threshold=float(threshold) if isinstance(threshold, int | float) else None,
        comparison=_COMPARISONS.get(str(raw.get("ComparisonOperator")), None),
        unit=unit if isinstance(unit, str) and unit != "None" else None,
        reason=_text(raw.get("StateReason"), 300),
        description=_text(raw.get("AlarmDescription"), 300),
        state_since=min(since, now),  # a clock difference must not put the start in the future
        seen_at=now,
    )


# An alarm changes state after the datapoint that breached it, so the window starts a little earlier: otherwise a fresh
# alarm's own trigger falls outside it and the metric looks flat.
LEAD_IN = timedelta(minutes=5)


def window_of(alarm: LiveAlarm) -> tuple[datetime, datetime]:
    """(started_at, window_end): from just before the alarm went off (at most 3 hours back) to when it was read."""
    lead = max(LEAD_IN, timedelta(seconds=2 * alarm.period_seconds))
    return max(alarm.state_since - lead, alarm.seen_at - MAX_WINDOW), alarm.seen_at


def _service(alarm: LiveAlarm) -> str:
    kind = (alarm.namespace or "").split("/")[-1] or "aws"
    subject = alarm.dimensions[0].value if alarm.dimensions else alarm.name
    return _text(f"{kind}/{subject}", 100) or kind


def to_detail(alarm: LiveAlarm) -> IncidentDetail:
    started, ended = window_of(alarm)
    firing_for = alarm.seen_at - alarm.state_since
    since_note = (
        f" It has been firing since {alarm.state_since:%Y-%m-%d %H:%M} UTC; only the last 3 hours are examined."
        if firing_for > MAX_WINDOW
        else ""
    )
    metric = (
        f"{alarm.namespace or ''} {alarm.metric_name} ({alarm.statistic or 'statistic not stated'}, "
        f"{alarm.period_seconds} s)".strip()
        if alarm.metric_name
        else "not a single metric (a metric-math or anomaly alarm)"
    )
    comparison = f"{alarm.comparison} {alarm.threshold:g}" if alarm.comparison and alarm.threshold is not None else None
    signals = [
        Signal(label="Data source", value="Your AWS account (live)"),
        Signal(label="Region", value=alarm.region),
        Signal(label="Alarm metric", value=_text(metric, 200)),
        *([Signal(label="Alarm threshold", value=comparison)] if comparison else []),
    ]
    reason = alarm.reason or "CloudWatch gave no reason."
    return IncidentDetail(
        id=alarm.id,
        title=_text(alarm.name, 200),
        service=_service(alarm),
        # CloudWatch alarms carry no severity; "medium" is a placeholder, not a judgement.
        severity="medium",
        status="open",
        summary=f"CloudWatch alarm in ALARM state since {alarm.state_since:%H:%M} UTC. {reason}{since_note}"[:600],
        started_at=started,
        window_end=ended,
        updated_at=alarm.seen_at,
        signals=signals,
        history=[
            TimelineEvent(
                id=f"{alarm.id}-state",
                at=alarm.state_since,
                kind="incident",
                title="The alarm went into the ALARM state",
                detail=alarm.reason or None,
            )
        ],
    )
