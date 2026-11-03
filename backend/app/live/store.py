"""The alarms last read from each user's account, kept so an investigation can name the incident it is about.

One record per owner holding up to MAX_KEPT alarms. It is derived data: it is replaced whenever the account is read, and
removed (with the investigations) by "Delete my investigations". A record that no longer validates reads as empty.
"""

import json
from typing import Protocol

from pydantic import ValidationError

from app.live.models import LiveAlarm

MAX_KEPT = 50


class LiveSnapshotStore(Protocol):
    def load(self, owner: str) -> list[LiveAlarm]: ...

    def save(self, owner: str, alarms: list[LiveAlarm]) -> None: ...

    def delete(self, owner: str) -> None:
        """Remove the owner's record. Removing one that does not exist is not an error."""
        ...


def encode(alarms: list[LiveAlarm]) -> str:
    return json.dumps([a.model_dump(mode="json") for a in alarms[:MAX_KEPT]])


def decode(body: str) -> list[LiveAlarm]:
    try:
        return [LiveAlarm.model_validate(item) for item in json.loads(body)]
    except (ValueError, TypeError, ValidationError):
        return []


def merge(previous: list[LiveAlarm], fresh: list[LiveAlarm]) -> list[LiveAlarm]:
    """Fresh readings replace older ones for the same alarm; alarms no longer firing stay (an investigation of one
    must still find its incident), newest first, up to MAX_KEPT."""
    by_id = {a.id: a for a in previous}
    by_id.update({a.id: a for a in fresh})
    return sorted(by_id.values(), key=lambda a: a.seen_at, reverse=True)[:MAX_KEPT]


class InMemoryLiveSnapshotStore:
    def __init__(self) -> None:
        self._items: dict[str, str] = {}

    def load(self, owner: str) -> list[LiveAlarm]:
        body = self._items.get(owner)
        return [] if body is None else decode(body)

    def save(self, owner: str, alarms: list[LiveAlarm]) -> None:
        self._items[owner] = encode(alarms)

    def delete(self, owner: str) -> None:
        self._items.pop(owner, None)
