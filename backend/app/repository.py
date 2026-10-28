"""Storage boundary. Business logic depends only on IncidentRepository, so the in-memory
implementation can be replaced (e.g. by DynamoDB in Phase 4) without touching the simulator."""

import threading
from typing import Protocol

from app.errors import IncidentNotFoundError
from app.schemas import IncidentDetail, IncidentSummary
from app.simulator.fixtures import INCIDENTS


class IncidentRepository(Protocol):
    def list_incidents(self) -> list[IncidentSummary]: ...

    def get_incident(self, incident_id: str) -> IncidentDetail:
        """Raise IncidentNotFoundError if the id is unknown."""
        ...

    def get_applied_remediation(self, incident_id: str) -> str | None: ...

    def record_remediation(self, incident_id: str, action_id: str) -> bool:
        """Atomically record a remediation. Return True if newly recorded, False if this
        action was already recorded (so callers can make execution idempotent)."""
        ...


class InMemoryIncidentRepository:
    """Process-local store seeded from the deterministic fixtures. Lost on restart."""

    def __init__(self) -> None:
        self._incidents = {i.id: i for i in INCIDENTS}
        self._applied: dict[str, str] = {}
        self._lock = threading.Lock()

    def list_incidents(self) -> list[IncidentSummary]:
        fields = set(IncidentSummary.model_fields)
        return [IncidentSummary.model_validate(i.model_dump(include=fields)) for i in self._incidents.values()]

    def get_incident(self, incident_id: str) -> IncidentDetail:
        try:
            return self._incidents[incident_id]
        except KeyError:
            raise IncidentNotFoundError("Incident not found.") from None

    def get_applied_remediation(self, incident_id: str) -> str | None:
        self.get_incident(incident_id)
        return self._applied.get(incident_id)

    def record_remediation(self, incident_id: str, action_id: str) -> bool:
        self.get_incident(incident_id)
        with self._lock:
            if self._applied.get(incident_id) == action_id:
                return False
            self._applied[incident_id] = action_id
            return True
