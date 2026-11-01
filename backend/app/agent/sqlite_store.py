"""Durable local investigation store (Phase 4): one SQLite file, standard library only.

A connection is opened per operation (cheap locally, and safe across FastAPI's worker threads).
`create` runs in an IMMEDIATE transaction, so "one running investigation per incident" and id
allocation are atomic even across processes. Records are stored as validated JSON; anything that
no longer validates is treated as absent rather than served.

Every row has an owner (a Cognito `sub`, or "anonymous" when sign-in is off); every query filters on it, so one
user can neither read nor change another's records. A database made before owners existed gains the column on
start-up and its rows belong to "anonymous".

On start-up, investigations left `running` by a previous process are marked failed, so a crash or
restart never leaves an incident blocked behind a run that will never finish.
"""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from app.agent.models import Failure, Investigation, ProviderInfo
from app.errors import InvestigationInProgressError, InvestigationNotFoundError
from app.identity import ANONYMOUS_OWNER

logger = logging.getLogger("agentops.agent")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS investigations (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    id          TEXT UNIQUE,
    incident_id TEXT NOT NULL,
    status      TEXT NOT NULL,
    body        TEXT NOT NULL,
    owner       TEXT NOT NULL DEFAULT 'anonymous'
);
CREATE INDEX IF NOT EXISTS idx_investigations_incident ON investigations (incident_id, seq);
"""
_OWNER_INDEX = "CREATE INDEX IF NOT EXISTS idx_investigations_owner ON investigations (owner, incident_id, seq)"


class SqliteInvestigationStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(_SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(investigations)")}
            if "owner" not in columns:  # a database from before sign-in existed
                db.execute(f"ALTER TABLE investigations ADD COLUMN owner TEXT NOT NULL DEFAULT '{ANONYMOUS_OWNER}'")
            db.execute(_OWNER_INDEX)
        self._recover_interrupted()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._path, timeout=10, isolation_level=None)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            yield db

    @staticmethod
    def _load(body: str) -> Investigation | None:
        try:
            return Investigation.model_validate_json(body)
        except ValidationError:
            return None

    def _recover_interrupted(self) -> None:
        with self._connect() as db:
            rows = db.execute("SELECT id, body FROM investigations WHERE status = 'running'").fetchall()
            for inv_id, body in rows:
                inv = self._load(body)
                if inv is None:
                    continue
                failed = inv.model_copy(
                    update={
                        "status": "failed",
                        "failure": Failure(
                            code="internal_error",
                            message="The server restarted before this investigation finished.",
                        ),
                        "finished_at": datetime.now(UTC),
                    }
                )
                db.execute(
                    "UPDATE investigations SET status = ?, body = ? WHERE id = ?",
                    (failed.status, failed.model_dump_json(), inv_id),
                )
                logger.info("investigation %s marked failed after restart", inv_id)

    # ---- InvestigationStore ----------------------------------------------------------------

    def create(self, owner: str, incident_id: str, provider: ProviderInfo, started_at: datetime) -> Investigation:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                running = db.execute(
                    "SELECT 1 FROM investigations WHERE owner = ? AND incident_id = ? AND status = 'running' LIMIT 1",
                    (owner, incident_id),
                ).fetchone()
                if running:
                    raise InvestigationInProgressError("An investigation is already running for this incident.")
                cur = db.execute(
                    "INSERT INTO investigations (owner, incident_id, status, body) VALUES (?, ?, 'running', '')",
                    (owner, incident_id),
                )
                record = Investigation(
                    id=f"inv-{cur.lastrowid:04d}",
                    incident_id=incident_id,
                    status="running",
                    provider=provider,
                    started_at=started_at,
                )
                db.execute(
                    "UPDATE investigations SET id = ?, body = ? WHERE seq = ?",
                    (record.id, record.model_dump_json(), cur.lastrowid),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return record

    def save(self, owner: str, investigation: Investigation) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE investigations SET status = ?, body = ? WHERE id = ? AND owner = ?",
                (investigation.status, investigation.model_dump_json(), investigation.id, owner),
            )

    def get(self, owner: str, investigation_id: str) -> Investigation:
        with self._connect() as db:
            row = db.execute(
                "SELECT body FROM investigations WHERE id = ? AND owner = ?", (investigation_id, owner)
            ).fetchone()
        found = self._load(row[0]) if row else None
        if found is None:
            raise InvestigationNotFoundError("Investigation not found.")
        return found

    def latest_for(self, owner: str, incident_id: str) -> Investigation | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT body FROM investigations WHERE owner = ? AND incident_id = ? ORDER BY seq DESC LIMIT 1",
                (owner, incident_id),
            ).fetchone()
        return self._load(row[0]) if row else None

    def list_for_incident(self, owner: str, incident_id: str) -> list[Investigation]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT body FROM investigations WHERE owner = ? AND incident_id = ? ORDER BY seq",
                (owner, incident_id),
            ).fetchall()
        return [inv for r in rows if (inv := self._load(r[0])) is not None]
