"""The audit timeline, derived from investigation records.

Every event is computed from data the server recorded (tool calls, timestamps, approval state), so
the timeline cannot disagree with the records and survives whatever store holds them.
"""

from app.agent.models import Investigation
from app.schemas import IncidentStatus, TimelineEvent

_MAX_DETAIL = 300


def _args(arguments: dict[str, str | int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in arguments.items())


def events_for(inv: Investigation) -> list[TimelineEvent]:
    out: list[TimelineEvent] = [
        TimelineEvent(
            id=f"{inv.id}-start",
            at=inv.started_at,
            kind="agent",
            title=f"Investigation {inv.id} started",
            detail=inv.provider.label,
        )
    ]
    for c in inv.tool_calls:
        failed = c.status == "error"
        out.append(
            TimelineEvent(
                id=f"{inv.id}-{c.id}",
                at=c.at,
                kind="error" if failed else "tool",
                title=f"{c.name}({_args(c.arguments)})",
                detail=f"{'Failed: ' if failed else ''}{c.result_summary} · {c.duration_ms} ms"[:_MAX_DETAIL],
            )
        )
    if inv.finished_at is not None:
        if inv.status == "completed" and inv.findings is not None:
            out.append(
                TimelineEvent(
                    id=f"{inv.id}-done",
                    at=inv.finished_at,
                    kind="agent",
                    title="Findings ready",
                    detail=f"Confidence: {inv.findings.confidence}",
                )
            )
        elif inv.failure is not None:
            out.append(
                TimelineEvent(
                    id=f"{inv.id}-failed",
                    at=inv.finished_at,
                    kind="error",
                    title="Investigation failed",
                    detail=inv.failure.message,
                )
            )
    r = inv.remediation_state
    if r is not None and r.decided_at is not None:
        approved = r.status != "rejected"
        out.append(
            TimelineEvent(
                id=f"{inv.id}-decision",
                at=r.decided_at,
                kind="approval",
                title="Remediation approved" if approved else "Remediation rejected",
                detail=r.action_id if approved else f"{r.action_id} was not executed.",
            )
        )
    if r is not None and r.executed_at is not None:
        ok = r.action is not None
        out.append(
            TimelineEvent(
                id=f"{inv.id}-action",
                at=r.executed_at,
                kind="action" if ok else "error",
                title="Simulated action succeeded" if ok else "Simulated action failed",
                detail=(r.action.summary if r.action else r.error),
            )
        )
    if r is not None and r.verified_at is not None and r.verification is not None:
        v = r.verification
        passed = v.status == "resolved"
        checks = "; ".join(f"{c.metric} {c.observed:g} {'<' if c.passed else '>='} {c.threshold:g}" for c in v.checks)
        out.append(
            TimelineEvent(
                id=f"{inv.id}-verification",
                at=r.verified_at,
                kind="verification",
                title=(
                    "Verification passed: simulated incident resolved"
                    if passed
                    else "Verification FAILED: the incident is not resolved"
                    if v.status == "not_resolved"
                    else "Verification not available for this incident"
                ),
                detail=checks or None,
            )
        )
    return out


def effective_status(base: IncidentStatus, latest: Investigation | None) -> IncidentStatus:
    """What the dashboard should show, given the newest investigation. The fixtures never change."""
    if latest is None:
        return base
    if latest.status == "running":
        return "investigating"
    r = latest.remediation_state
    if r is not None:
        if r.status == "executing":
            return "investigating"
        if r.status == "resolved":
            return "resolved"
    return base
