"""Storage boundary. Business logic depends only on IncidentRepository, so the in-memory
implementation can be replaced (e.g. by DynamoDB in Phase 4) without touching the simulator."""

import threading
from collections.abc import Iterable
from typing import Protocol

from app.errors import IncidentNotFoundError
from app.identity import ANONYMOUS_OWNER
from app.schemas import IncidentDetail, IncidentSummary
from app.simulator.fixtures import INCIDENTS


class IncidentRepository(Protocol):
    def list_incidents(self) -> list[IncidentSummary]: ...

    def get_incident(self, incident_id: str) -> IncidentDetail:
        """Raise IncidentNotFoundError if the id is unknown."""
        ...

    def get_applied_remediation(self, incident_id: str, owner: str = ANONYMOUS_OWNER) -> str | None: ...

    def record_remediation(self, incident_id: str, action_id: str, owner: str = ANONYMOUS_OWNER) -> bool:
        """Atomically record a remediation for this owner. Return True if newly recorded, False if this
        action was already recorded (so callers can make execution idempotent). One user applying a fix
        never changes what another user sees."""
        ...


class InMemoryIncidentRepository:
    """Process-local store seeded from the deterministic fixtures. Lost on restart."""

    def __init__(self, incidents: Iterable[IncidentDetail] | None = None) -> None:
        self._incidents = {i.id: i for i in (INCIDENTS if incidents is None else incidents)}
        self._applied: dict[tuple[str, str], str] = {}  # (owner, incident id) -> action id
        self._lock = threading.Lock()

    def list_incidents(self) -> list[IncidentSummary]:
        fields = set(IncidentSummary.model_fields)
        return [IncidentSummary.model_validate(i.model_dump(include=fields)) for i in self._incidents.values()]

    def get_incident(self, incident_id: str) -> IncidentDetail:
        try:
            return self._incidents[incident_id]
        except KeyError:
            raise IncidentNotFoundError("Incident not found.") from None

    def get_applied_remediation(self, incident_id: str, owner: str = ANONYMOUS_OWNER) -> str | None:
        self.get_incident(incident_id)
        return self._applied.get((owner, incident_id))

    def record_remediation(self, incident_id: str, action_id: str, owner: str = ANONYMOUS_OWNER) -> bool:
        self.get_incident(incident_id)
        with self._lock:
            if self._applied.get((owner, incident_id)) == action_id:
                return False
            self._applied[(owner, incident_id)] = action_id
            return True
