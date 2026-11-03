"""Wires the simulator, a provider and the store together for the API.

The service holds the shared parts (simulator, provider, limits). `scoped(owner)` returns a view of it for one
owner (a signed-in user, or the shared anonymous owner when sign-in is off): every record it reads or writes,
and every remediation it applies, belongs to that owner and to nobody else.
"""

import copy
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from app.agent.coordinator import FAILURE_MESSAGES, Coordinator, Limits
from app.agent.models import (
    Failure,
    Feedback,
    FeedbackRequest,
    Investigation,
    InvestigationSummary,
    ProviderInfo,
    RemediationRecord,
)
from app.agent.provider import LLMProvider
from app.agent.store import InvestigationStore
from app.agent.timeline import effective_status, events_for
from app.agent.tools import ToolRunner
from app.config import Settings
from app.diagnostics import Diagnostics
from app.errors import (
    AgentOpsError,
    FeedbackNotAllowedError,
    IncidentNotFoundError,
    InvestigationInProgressError,
    RemediationStateError,
)
from app.identity import ANONYMOUS_OWNER
from app.live.service import LiveService, is_live_id
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
        live: LiveService | None = None,
    ) -> None:
        self._sim = simulator
        self._live = live
        self._store = store
        self._provider = provider
        self._clock = clock
        self._owner = ANONYMOUS_OWNER
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

    def scoped(self, owner: str) -> "InvestigationService":
        """The same service, acting for `owner`. Cheap: it shares the simulator, provider, store and lock."""
        view = copy.copy(self)
        view._owner = owner  # noqa: SLF001 - a copy of this very class
        return view

    def _save(self, investigation: Investigation) -> None:
        self._store.save(self._owner, investigation)

    # ---- where an incident's data comes from ---------------------------------------------------

    def _incident(self, incident_id: str) -> IncidentDetail:
        """The incident, from the shared demo data or (LIVE-...) from this owner's own AWS account."""
        if is_live_id(incident_id):
            if self._live is None:
                raise IncidentNotFoundError("Incident not found.")
            return self._live.detail(self._owner, incident_id)
        return self._sim.get_incident(GetIncidentArgs(incident_id=incident_id))

    def _diagnostics(self, incident_id: str) -> Diagnostics:
        if is_live_id(incident_id):
            if self._live is None:
                raise IncidentNotFoundError("Incident not found.")
            return self._live.diagnostics(self._owner)
        return self._sim

    def start(self, incident_id: str) -> Investigation:
        """Validate the incident and record a running investigation. Cheap; the work is in run()."""
        if is_live_id(incident_id):
            # Refused here (409), before any record exists, if the connection, the role or the AI service is not usable.
            diagnostics = self._diagnostics(incident_id)
            diagnostics.get_incident(GetIncidentArgs(incident_id=incident_id))
            diagnostics.prepare()
        else:
            self._sim.get_incident(GetIncidentArgs(incident_id=incident_id))
        return self._store.create(self._owner, incident_id, self._provider.info, self._clock())

    def run(self, investigation_id: str) -> None:
        """Execute a started investigation to a terminal state. Never raises, never leaves it 'running'."""
        initial = self._store.get(self._owner, investigation_id)
        try:
            diagnostics = self._diagnostics(initial.incident_id)
            try:
                diagnostics.prepare()
            except AgentOpsError as exc:
                logger.info("investigation %s could not reach its data source: %s", investigation_id, exc.code)
                self._save(
                    initial.model_copy(
                        update={
                            "status": "failed",
                            "failure": Failure(code="source_unavailable", message=exc.message),
                            "finished_at": self._clock(),
                        }
                    )
                )
                return
            service = diagnostics.get_incident(GetIncidentArgs(incident_id=initial.incident_id)).service
            self._save(self._coordinator.run(initial, service, self._save, diagnostics))
        except Exception as exc:
            logger.error("investigation %s crashed: %s", investigation_id, type(exc).__name__)
            latest = self._store.get(self._owner, investigation_id)
            self._save(
                latest.model_copy(
                    update={
                        "status": "failed",
                        "failure": Failure(code="internal_error", message=FAILURE_MESSAGES["internal_error"]),
                        "finished_at": self._clock(),
                    }
                )
            )

    def incident(self, incident_id: str) -> IncidentDetail:
        """The incident as this owner may see it (demo data, or their own live alarm)."""
        return self._incident(incident_id)

    def get(self, investigation_id: str) -> Investigation:
        return self._store.get(self._owner, investigation_id)

    def latest(self, incident_id: str) -> Investigation | None:
        """This owner's newest investigation of the incident, or None if they have not run one (not an error)."""
        self._incident(incident_id)
        return self._store.latest_for(self._owner, incident_id)

    # ---- "my investigations" -----------------------------------------------------------------

    def mine(self) -> list[InvestigationSummary]:
        """This owner's newest investigation of each incident they have investigated, newest first."""
        rows: list[InvestigationSummary] = []
        live = self._live.known_summaries(self._owner) if self._live is not None else []
        for incident in [*self._sim.list_incidents(), *live]:
            inv = self._store.latest_for(self._owner, incident.id)
            if inv is None:
                continue
            state = inv.remediation_state
            rows.append(
                InvestigationSummary(
                    id=inv.id,
                    incident_id=incident.id,
                    incident_title=incident.title,
                    status=inv.status,
                    started_at=inv.started_at,
                    confidence=inv.findings.confidence if inv.findings else None,
                    remediation_status=state.status if state else None,
                    helpful=inv.feedback.helpful if inv.feedback else None,
                )
            )
        return sorted(rows, key=lambda r: r.started_at, reverse=True)

    def delete_mine(self) -> int:
        """Delete everything this owner has recorded. Refused while one of their investigations is running."""
        with self._approval_lock:  # nothing is being approved or rated while records disappear
            incident_ids = [i.id for i in self._sim.list_incidents()]
            if self._live is not None:
                incident_ids += self._live.all_ids(self._owner)
            for incident_id in incident_ids:
                latest = self._store.latest_for(self._owner, incident_id)
                if latest is not None and latest.status == "running":
                    raise InvestigationInProgressError("Wait for the running investigation to finish, then delete.")
            removed = self._store.delete_for_owner(self._owner, incident_ids)
            self._sim.forget_owner(self._owner)
            if self._live is not None:
                self._live.forget(self._owner)
            return removed

    # ---- incident views --------------------------------------------------------------------

    def with_status(self, incident: IncidentSummary) -> IncidentSummary:
        """The incident with its status reflecting the newest investigation (investigating / resolved)."""
        status = effective_status(incident.status, self._store.latest_for(self._owner, incident.id))
        return incident.model_copy(update={"status": status}) if status != incident.status else incident

    def with_status_detail(self, incident: IncidentDetail) -> IncidentDetail:
        status = effective_status(incident.status, self._store.latest_for(self._owner, incident.id))
        return incident.model_copy(update={"status": status}) if status != incident.status else incident

    def timeline(self, incident_id: str) -> list[TimelineEvent]:
        incident = self._incident(incident_id)
        derived = [e for inv in self._store.list_for_incident(self._owner, incident_id) for e in events_for(inv)]
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
            latest = self._store.latest_for(self._owner, inv.incident_id)
            if latest is not None and latest.id != inv.id:
                raise RemediationStateError("A newer investigation exists; approve from the latest one.")

            inv = self._save_state(inv, record.model_copy(update={"status": "executing", "decided_at": self._clock()}))
            running = inv.remediation_state
            assert running is not None  # noqa: S101  (just set)
            record = running
            try:
                action = self._sim.execute_remediation(inv.incident_id, record.action_id, self._owner)
                executed_at = self._clock()
                record = record.model_copy(update={"action": action, "executed_at": executed_at})
                verification = self._sim.verify_resolution(inv.incident_id, self._owner)
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
            inv = self._store.get(self._owner, investigation_id)
            if inv.status == "running":
                raise FeedbackNotAllowedError("This investigation is still running; rate it when it finishes.")
            updated = inv.model_copy(
                update={
                    "feedback": Feedback(helpful=request.helpful, comment=request.comment or None, at=self._clock())
                }
            )
            self._save(updated)
            return updated

    def _decidable(self, investigation_id: str) -> tuple[Investigation, RemediationRecord]:
        inv = self._store.get(self._owner, investigation_id)
        if inv.status != "completed" or inv.remediation_state is None or inv.findings is None:
            raise RemediationStateError("This investigation has no remediation awaiting a decision.")
        return inv, inv.remediation_state

    def _save_state(self, inv: Investigation, record: RemediationRecord) -> Investigation:
        updated = inv.model_copy(update={"remediation_state": record})
        self._save(updated)
        return updated
