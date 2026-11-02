"""Investigation records. In memory for Phase 3A (lost on restart); Phase 4 replaces it with SQLite
behind this same interface.

Every method takes the owner first and only ever sees that owner's records: asking for another owner's
investigation id is the same as asking for one that does not exist."""

import threading
from datetime import datetime
from typing import Protocol

from app.agent.models import Investigation, ProviderInfo
from app.errors import InvestigationInProgressError, InvestigationNotFoundError

MAX_RECORDS = 200  # bound memory: evict the oldest finished investigation beyond this


class InvestigationStore(Protocol):
    def create(self, owner: str, incident_id: str, provider: ProviderInfo, started_at: datetime) -> Investigation:
        """Raise InvestigationInProgressError if this owner already has a running investigation of the incident."""
        ...

    def save(self, owner: str, investigation: Investigation) -> None:
        """Replace the owner's record. An unknown id, or one that belongs to someone else, is ignored."""
        ...

    def get(self, owner: str, investigation_id: str) -> Investigation:
        """Raise InvestigationNotFoundError if unknown or owned by someone else."""
        ...

    def latest_for(self, owner: str, incident_id: str) -> Investigation | None: ...

    def list_for_incident(self, owner: str, incident_id: str) -> list[Investigation]:
        """Oldest first. Used to build the audit timeline."""
        ...

    def delete_for_owner(self, owner: str, incident_ids: list[str]) -> int:
        """Remove the owner's records and return how many investigations went. Never touches anyone else's.

        `incident_ids` lets a store without a per-owner index (DynamoDB) find the records through their pointers.
        """
        ...


class InMemoryInvestigationStore:
    def __init__(self) -> None:
        self._rows: dict[str, tuple[str, Investigation]] = {}  # id -> (owner, record)
        self._counter = 0
        self._lock = threading.Lock()

    def create(self, owner: str, incident_id: str, provider: ProviderInfo, started_at: datetime) -> Investigation:
        with self._lock:
            if any(
                o == owner and r.incident_id == incident_id and r.status == "running" for o, r in self._rows.values()
            ):
                raise InvestigationInProgressError("An investigation is already running for this incident.")
            self._counter += 1
            record = Investigation(
                id=f"inv-{self._counter:04d}",
                incident_id=incident_id,
                status="running",
                provider=provider,
                started_at=started_at,
            )
            self._rows[record.id] = (owner, record)
            while len(self._rows) > MAX_RECORDS:
                oldest_finished = next((k for k, (_, r) in self._rows.items() if r.status != "running"), None)
                if oldest_finished is None:
                    break
                del self._rows[oldest_finished]
            return record

    def save(self, owner: str, investigation: Investigation) -> None:
        with self._lock:
            existing = self._rows.get(investigation.id)
            if existing is not None and existing[0] == owner:
                self._rows[investigation.id] = (owner, investigation)

    def get(self, owner: str, investigation_id: str) -> Investigation:
        with self._lock:
            found = self._rows.get(investigation_id)
        if found is None or found[0] != owner:
            raise InvestigationNotFoundError("Investigation not found.")
        return found[1]

    def latest_for(self, owner: str, incident_id: str) -> Investigation | None:
        matching = self.list_for_incident(owner, incident_id)
        return matching[-1] if matching else None

    def list_for_incident(self, owner: str, incident_id: str) -> list[Investigation]:
        with self._lock:
            return [r for o, r in self._rows.values() if o == owner and r.incident_id == incident_id]

    def delete_for_owner(self, owner: str, incident_ids: list[str]) -> int:
        with self._lock:
            mine = [k for k, (o, _) in self._rows.items() if o == owner]
            for k in mine:
                del self._rows[k]
            return len(mine)
