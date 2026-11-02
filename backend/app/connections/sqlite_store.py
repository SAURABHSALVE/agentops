"""A user's saved connection in the same local SQLite file as the investigations, in its own table."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from app.connections.models import AwsConnection
from app.connections.store import load

_SCHEMA = "CREATE TABLE IF NOT EXISTS aws_connections (owner TEXT PRIMARY KEY, body TEXT NOT NULL)"


class SqliteConnectionStore:
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

    def get(self, owner: str) -> AwsConnection | None:
        with self._connect() as db:
            row = db.execute("SELECT body FROM aws_connections WHERE owner = ?", (owner,)).fetchone()
        return None if row is None else load(row[0])

    def put(self, owner: str, connection: AwsConnection) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO aws_connections (owner, body) VALUES (?, ?) "
                "ON CONFLICT(owner) DO UPDATE SET body = excluded.body",
                (owner, connection.model_dump_json()),
            )

    def delete(self, owner: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM aws_connections WHERE owner = ?", (owner,))
