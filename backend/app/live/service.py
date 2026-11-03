"""Per-user access to the alarms and diagnostics of a connected AWS account.

Every method takes the owner and only ever touches that owner's connection and snapshots. Whether the feature is on,
whether the user agreed to the current wording, and whether a role is registered are all decided by ConnectionService;
this class adds the one rule specific to reading real data: investigating needs the AI service, because the offline
rule-based assistant only knows the demo incidents.
"""

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from app.connections.service import ConnectionService
from app.errors import IncidentNotFoundError, LiveNeedsAiError
from app.live.alarms import alarm_from_raw, to_detail
from app.live.diagnostics import LiveDiagnostics
from app.live.models import LiveAlarm, LiveIncidentsView
from app.live.reader import ReaderFactory
from app.live.store import LiveSnapshotStore, merge
from app.schemas import IncidentDetail, IncidentSummary

MIN_REFRESH_SECONDS = 10  # asking again sooner answers from the last reading instead of calling the account again


def _utc_now() -> datetime:
    return datetime.now(UTC)


def is_live_id(incident_id: str) -> bool:
    return incident_id.startswith("LIVE-")


def _summary(alarm: LiveAlarm) -> IncidentSummary:
    return IncidentSummary.model_validate(to_detail(alarm).model_dump(exclude={"signals", "history"}))


class LiveService:
    def __init__(
        self,
        connections: ConnectionService,
        snapshots: LiveSnapshotStore,
        readers: ReaderFactory,
        *,
        provider_kind: str,
        clock: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connections = connections
        self._snapshots = snapshots
        self._readers = readers
        self._provider_kind = provider_kind
        self._clock = clock
        self._monotonic = monotonic
        self._last: dict[str, tuple[float, LiveIncidentsView]] = {}
        self._lock = threading.Lock()

    # ---- reading the account ---------------------------------------------------------------

    def refresh(self, owner: str) -> LiveIncidentsView:
        """The alarms firing in the owner's account right now. Calls the account at most once per few seconds."""
        connection = self._connections.readable(owner)
        with self._lock:
            cached = self._last.get(owner)
        if cached is not None and self._monotonic() - cached[0] < MIN_REFRESH_SECONDS:
            return cached[1]

        now = self._clock()
        raw = self._readers.reader_for(owner, connection).firing_alarms()
        fresh = [a for r in raw if (a := alarm_from_raw(r, connection.region, now)) is not None]
        self._snapshots.save(owner, merge(self._snapshots.load(owner), fresh))
        self._connections.mark_verified(owner, connection)
        view = LiveIncidentsView(
            region=connection.region,
            checked_at=now,
            incidents=[_summary(a) for a in sorted(fresh, key=lambda a: a.state_since, reverse=True)],
        )
        with self._lock:
            self._last[owner] = (self._monotonic(), view)
        return view

    def diagnostics(self, owner: str) -> LiveDiagnostics:
        """The four tools for this owner's investigations. Needs a registered role and the AI service."""
        connection = self._connections.readable(owner)
        if self._provider_kind == "mock":
            raise LiveNeedsAiError(
                "Investigating your own AWS account needs the AI service, which is switched off on this site. "
                "You can still see which alarms are firing."
            )
        return LiveDiagnostics(
            {a.id: a for a in self._snapshots.load(owner)},
            connection.log_groups,
            lambda: self._readers.reader_for(owner, connection),
        )

    # ---- what was last seen (no call to the account) ----------------------------------------

    def _known(self, owner: str) -> list[LiveAlarm]:
        return self._snapshots.load(owner) if self._connections.feature_available(owner) else []

    def detail(self, owner: str, incident_id: str) -> IncidentDetail:
        for alarm in self._known(owner):
            if alarm.id == incident_id:
                return to_detail(alarm)
        raise IncidentNotFoundError("Incident not found.")

    def known_summaries(self, owner: str) -> list[IncidentSummary]:
        """Every live incident this owner has seen, for "my investigations" and for deleting them."""
        return [_summary(a) for a in self._known(owner)]

    def all_ids(self, owner: str) -> list[str]:
        """Every live incident id remembered for the owner, whether or not the feature is on now (for deleting)."""
        return [a.id for a in self._snapshots.load(owner)]

    def forget(self, owner: str) -> None:
        self._snapshots.delete(owner)
        with self._lock:
            self._last.pop(owner, None)
