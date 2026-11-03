"""The alarm snapshots in the same local SQLite file as everything else, in their own table."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from app.live.models import LiveAlarm
from app.live.store import decode, encode

_SCHEMA = "CREATE TABLE IF NOT EXISTS live_alarms (owner TEXT PRIMARY KEY, body TEXT NOT NULL)"


class SqliteLiveSnapshotStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._path, timeout=10, isolation_level=None)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            yield db

    def load(self, owner: str) -> list[LiveAlarm]:
        with self._connect() as db:
            row = db.execute("SELECT body FROM live_alarms WHERE owner = ?", (owner,)).fetchone()
        return [] if row is None else decode(row[0])

    def save(self, owner: str, alarms: list[LiveAlarm]) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO live_alarms (owner, body) VALUES (?, ?) "
                "ON CONFLICT(owner) DO UPDATE SET body = excluded.body",
                (owner, encode(alarms)),
            )

    def delete(self, owner: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM live_alarms WHERE owner = ?", (owner,))
