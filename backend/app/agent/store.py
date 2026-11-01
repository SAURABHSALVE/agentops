"""Investigation records. In memory for Phase 3A (lost on restart); Phase 4 replaces it with SQLite
behind this same interface."""

import threading
from datetime import datetime
from typing import Protocol

from app.agent.models import Investigation, ProviderInfo
from app.errors import InvestigationInProgressError, InvestigationNotFoundError

MAX_RECORDS = 200  # bound memory: evict the oldest finished investigation beyond this


class InvestigationStore(Protocol):
    def create(self, incident_id: str, provider: ProviderInfo, started_at: datetime) -> Investigation:
        """Raise InvestigationInProgressError if this incident already has a running investigation."""
        ...

    def save(self, investigation: Investigation) -> None: ...

    def get(self, investigation_id: str) -> Investigation:
        """Raise InvestigationNotFoundError if unknown."""
        ...

    def latest_for(self, incident_id: str) -> Investigation | None: ...

    def list_for_incident(self, incident_id: str) -> list[Investigation]:
        """Oldest first. Used to build the audit timeline."""
        ...


class InMemoryInvestigationStore:
    def __init__(self) -> None:
        self._rows: dict[str, Investigation] = {}
        self._counter = 0
        self._lock = threading.Lock()

    def create(self, incident_id: str, provider: ProviderInfo, started_at: datetime) -> Investigation:
        with self._lock:
            if any(r.incident_id == incident_id and r.status == "running" for r in self._rows.values()):
                raise InvestigationInProgressError("An investigation is already running for this incident.")
            self._counter += 1
            record = Investigation(
                id=f"inv-{self._counter:04d}",
                incident_id=incident_id,
                status="running",
                provider=provider,
                started_at=started_at,
            )
            self._rows[record.id] = record
            while len(self._rows) > MAX_RECORDS:
                oldest_finished = next((k for k, r in self._rows.items() if r.status != "running"), None)
                if oldest_finished is None:
                    break
                del self._rows[oldest_finished]
            return record

    def save(self, investigation: Investigation) -> None:
        with self._lock:
            if investigation.id in self._rows:
                self._rows[investigation.id] = investigation

    def get(self, investigation_id: str) -> Investigation:
        with self._lock:
            try:
                return self._rows[investigation_id]
            except KeyError:
                raise InvestigationNotFoundError("Investigation not found.") from None

    def latest_for(self, incident_id: str) -> Investigation | None:
        with self._lock:
            matching = [r for r in self._rows.values() if r.incident_id == incident_id]
            return matching[-1] if matching else None

    def list_for_incident(self, incident_id: str) -> list[Investigation]:
        with self._lock:
            return [r for r in self._rows.values() if r.incident_id == incident_id]
