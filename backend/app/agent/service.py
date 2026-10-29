"""Wires the simulator, a provider and the store together for the API."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from app.agent.coordinator import FAILURE_MESSAGES, Coordinator, Limits
from app.agent.models import Failure, Investigation
from app.agent.provider import LLMProvider
from app.agent.store import InvestigationStore
from app.agent.tools import ToolRunner
from app.config import Settings
from app.errors import InvestigationNotFoundError
from app.schemas import GetIncidentArgs
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
