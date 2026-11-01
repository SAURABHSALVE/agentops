"""Wires the simulator, a provider and the store together for the API."""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from app.agent.coordinator import FAILURE_MESSAGES, Coordinator, Limits
from app.agent.models import Failure, Feedback, FeedbackRequest, Investigation, ProviderInfo, RemediationRecord
from app.agent.provider import LLMProvider
from app.agent.store import InvestigationStore
from app.agent.timeline import effective_status, events_for
from app.agent.tools import ToolRunner
from app.config import Settings
from app.errors import AgentOpsError, FeedbackNotAllowedError, InvestigationNotFoundError, RemediationStateError
from app.schemas import GetIncidentArgs, IncidentDetail, IncidentSummary, TimelineEvent
from app.simulator.service import Simulator

logger = logging.getLogger("agentops.agent")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class InvestigationService:
    def __init__(
        self,
        simulator: Simulator,
        store: InvestigationStore,
        provider: LLMProvider,
        settings: Settings,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._sim = simulator
        self._store = store
        self._provider = provider
        self._clock = clock
        self._approval_lock = threading.Lock()  # approvals are rare; serialising them makes "runs once" simple
        runner = ToolRunner(
            simulator,
            timeout_seconds=settings.tool_timeout_seconds,
            max_result_bytes=settings.tool_result_max_bytes,
        )
        limits = Limits(
            max_rounds=settings.agent_max_rounds,
            max_total_tokens=settings.agent_max_total_tokens,
            max_output_tokens=settings.max_output_tokens,
        )
        self._coordinator = Coordinator(provider, runner, limits, clock)

    @property
    def provider_info(self) -> ProviderInfo:
        return self._provider.info

    def start(self, incident_id: str) -> Investigation:
        """Validate the incident and record a running investigation. Cheap; the work is in run()."""
        self._sim.get_incident(GetIncidentArgs(incident_id=incident_id))
        return self._store.create(incident_id, self._provider.info, self._clock())

    def run(self, investigation_id: str) -> None:
        """Execute a started investigation to a terminal state. Never raises, never leaves it 'running'."""
        initial = self._store.get(investigation_id)
        try:
            service = self._sim.get_incident(GetIncidentArgs(incident_id=initial.incident_id)).service
            self._store.save(self._coordinator.run(initial, service, self._store.save))
        except Exception as exc:
            logger.error("investigation %s crashed: %s", investigation_id, type(exc).__name__)
            latest = self._store.get(investigation_id)
            self._store.save(
                latest.model_copy(
                    update={
                        "status": "failed",
                        "failure": Failure(code="internal_error", message=FAILURE_MESSAGES["internal_error"]),
                        "finished_at": self._clock(),
                    }
                )
            )

    def get(self, investigation_id: str) -> Investigation:
        return self._store.get(investigation_id)

    def latest(self, incident_id: str) -> Investigation:
        self._sim.get_incident(GetIncidentArgs(incident_id=incident_id))
        found = self._store.latest_for(incident_id)
        if found is None:
            raise InvestigationNotFoundError("No investigation has been run for this incident.")
        return found

    # ---- incident views --------------------------------------------------------------------

    def with_status(self, incident: IncidentSummary) -> IncidentSummary:
        """The incident with its status reflecting the newest investigation (investigating / resolved)."""
        status = effective_status(incident.status, self._store.latest_for(incident.id))
        return incident.model_copy(update={"status": status}) if status != incident.status else incident

    def with_status_detail(self, incident: IncidentDetail) -> IncidentDetail:
        status = effective_status(incident.status, self._store.latest_for(incident.id))
        return incident.model_copy(update={"status": status}) if status != incident.status else incident

    def timeline(self, incident_id: str) -> list[TimelineEvent]:
        incident = self._sim.get_incident(GetIncidentArgs(incident_id=incident_id))
        derived = [e for inv in self._store.list_for_incident(incident_id) for e in events_for(inv)]
        # Stable sort: the fixture history first at equal timestamps, then investigation order.
        return sorted([*incident.history, *derived], key=lambda e: e.at)

    # ---- approval gate (Phase 5) -----------------------------------------------------------

    def approve(self, investigation_id: str) -> Investigation:
        """Run the proposed simulated action and verify it, exactly once.

        The action id is read from this investigation's validated findings; the client supplies
        nothing. Repeating an approval returns the recorded outcome and runs nothing.
        """
        with self._approval_lock:
            inv, record = self._decidable(investigation_id)
            if record.status != "proposed":
                if record.status == "rejected":
                    raise RemediationStateError("This remediation was rejected and cannot be approved.")
                return inv  # duplicate approval: already executing or finished
            latest = self._store.latest_for(inv.incident_id)
            if latest is not None and latest.id != inv.id:
                raise RemediationStateError("A newer investigation exists; approve from the latest one.")

            inv = self._save_state(inv, record.model_copy(update={"status": "executing", "decided_at": self._clock()}))
            running = inv.remediation_state
            assert running is not None  # noqa: S101  (just set)
            record = running
            try:
                action = self._sim.execute_remediation(inv.incident_id, record.action_id)
                executed_at = self._clock()
                record = record.model_copy(update={"action": action, "executed_at": executed_at})
                verification = self._sim.verify_resolution(inv.incident_id)
                record = record.model_copy(
                    update={"verification": verification, "verified_at": self._clock(), "status": verification.status}
                )
            except AgentOpsError as exc:
                logger.error("remediation %s failed: %s", investigation_id, exc.code)
                record = record.model_copy(
                    update={"status": "action_failed", "executed_at": self._clock(), "error": exc.message}
                )
            except Exception as exc:
                logger.error("remediation %s crashed: %s", investigation_id, type(exc).__name__)
                record = record.model_copy(
                    update={
                        "status": "action_failed",
                        "executed_at": self._clock(),
                        "error": "The simulated action failed unexpectedly.",
                    }
                )
            return self._save_state(inv, record)

    def reject(self, investigation_id: str) -> Investigation:
        with self._approval_lock:
            inv, record = self._decidable(investigation_id)
            if record.status == "rejected":
                return inv
            if record.status != "proposed":
                raise RemediationStateError("This remediation was already approved and cannot be rejected.")
            return self._save_state(inv, record.model_copy(update={"status": "rejected", "decided_at": self._clock()}))

    def give_feedback(self, investigation_id: str, request: FeedbackRequest) -> Investigation:
        """Record whether a finished answer helped. Replaces an earlier verdict; never reaches a model."""
        with self._approval_lock:  # same lock as approvals: both rewrite the whole record
            inv = self._store.get(investigation_id)
            if inv.status == "running":
                raise FeedbackNotAllowedError("This investigation is still running; rate it when it finishes.")
            updated = inv.model_copy(
                update={
                    "feedback": Feedback(helpful=request.helpful, comment=request.comment or None, at=self._clock())
                }
            )
            self._store.save(updated)
            return updated

    def _decidable(self, investigation_id: str) -> tuple[Investigation, RemediationRecord]:
        inv = self._store.get(investigation_id)
        if inv.status != "completed" or inv.remediation_state is None or inv.findings is None:
            raise RemediationStateError("This investigation has no remediation awaiting a decision.")
        return inv, inv.remediation_state

    def _save_state(self, inv: Investigation, record: RemediationRecord) -> Investigation:
        updated = inv.model_copy(update={"remediation_state": record})
        self._store.save(updated)
        return updated
