"""How sure the evidence allows the agent to be: a ceiling the backend enforces on any provider's confidence.

The prompt tells a model to "never exceed what the evidence supports", and live models do not reliably obey it (on the
ambiguous incident a model reported medium confidence every time, blaming one unrelated warning). A rule a model can
ignore is not a guarantee, so the backend applies the objective half of the rubric itself, from the tool results this
run actually retrieved:

* Any confidence above low needs a metric, retrieved in this run, that was elevated for at least half of the incident
  window. Brief or intermittent spikes (or no metric looked at) cap the answer at low.
* High needs no competing change: if a code, config or dependency change was deployed shortly before the incident, or
  the deployment history was not checked, the answer caps at medium.

The ceiling only ever lowers confidence. It never raises it and it never invents evidence, and what it did is shown to
the reader. The offline mock follows the same two rules, so it is never lowered (a test checks every incident).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.agent.models import Confidence

SUSTAINED_FRACTION = 0.5  # share of incident-window minutes a metric must be elevated to count as sustained
CAUSE_WINDOW_MINUTES = 60  # a code/config/dependency change this close before onset is a candidate cause

_RANK: dict[Confidence, int] = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class Ceiling:
    level: Confidence
    reason: str | None  # why it is below "high"; None when nothing limits the answer

    def lowers(self, confidence: Confidence) -> bool:
        return _RANK[confidence] > _RANK[self.level]

    def capped(self, level: Confidence, reason: str) -> "Ceiling":
        """This ceiling, or one at `level` with `reason` if that is lower."""
        return Ceiling(level, reason) if _RANK[level] < _RANK[self.level] else self


def evidence_ceiling(observed: Sequence[tuple[str, dict[str, Any]]]) -> Ceiling:
    """The highest confidence the successful tool results in `observed` (name, payload) support."""
    metrics = [p for name, p in observed if name == "get_service_metrics" and "summary" in p]
    if not any(m["summary"]["elevated_fraction"] >= SUSTAINED_FRACTION for m in metrics):
        return Ceiling(
            "low",
            "no metric checked in this run was elevated for at least half of the incident window"
            if metrics
            else "no metric was checked in this run",
        )

    deployments = next((p for name, p in observed if name == "get_recent_deployments" and "deployments" in p), None)
    if deployments is None:
        return Ceiling("medium", "recent deployments were not checked, so a competing change cannot be ruled out")
    if any(
        d["change_type"] != "docs" and 0 <= d["minutes_before_incident_start"] <= CAUSE_WINDOW_MINUTES
        for d in deployments["deployments"]
    ):
        return Ceiling("medium", "a change was deployed shortly before the incident and could also explain it")
    return Ceiling("high", None)
