"""Who may use the site's paid features, and how much: one-time invitations and daily investigation caps.

* Invitation. When an access code is configured, a signed-in user presents it once. If it is right, their account is
  remembered as invited and they need not send it again (the code is shared, so it can be changed to stop new invites).
* Daily caps. A real AI model costs money per investigation, so each user gets a number per day and the whole site gets
  a number per day. Unlike the per-process rate limiters, these counters live in the database, so they hold across
  every server and restart. A cap of 0 means no cap. The offline assistant is free and is never capped.

Three stores with the same behaviour: in memory (tests), a local SQLite file, and DynamoDB (same table, same four calls
the table already allows).
"""

import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from app.errors import RateLimitedError

COUNTER_TTL_SECONDS = 3 * 24 * 3600  # a day's counter is useless after that


class UsageStore(Protocol):
    def consume(self, key: str, limit: int, expires_at: int) -> bool:
        """Add one to counter `key` if it is below `limit`, atomically. False (and nothing added) when it is full."""
        ...

    def release(self, key: str) -> None:
        """Give one back (never below zero), for a charge that must not stand."""
        ...

    def is_invited(self, owner: str) -> bool: ...

    def invite(self, owner: str) -> None: ...


class InMemoryUsageStore:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._invited: set[str] = set()
        self._lock = threading.Lock()

    def consume(self, key: str, limit: int, expires_at: int) -> bool:
        with self._lock:
            if self._counts.get(key, 0) >= limit:
                return False
            self._counts[key] = self._counts.get(key, 0) + 1
            return True

    def release(self, key: str) -> None:
        with self._lock:
            self._counts[key] = max(0, self._counts.get(key, 0) - 1)

    def is_invited(self, owner: str) -> bool:
        return owner in self._invited

    def invite(self, owner: str) -> None:
        self._invited.add(owner)


class SqliteUsageStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS usage_counters (key TEXT PRIMARY KEY, n INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS invited (owner TEXT PRIMARY KEY)")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._path, timeout=10, isolation_level=None)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            yield db

    def consume(self, key: str, limit: int, expires_at: int) -> bool:
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO usage_counters (key, n) VALUES (?, 0)", (key,))
            cursor = db.execute("UPDATE usage_counters SET n = n + 1 WHERE key = ? AND n < ?", (key, limit))
            return cursor.rowcount == 1

    def release(self, key: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE usage_counters SET n = n - 1 WHERE key = ? AND n > 0", (key,))

    def is_invited(self, owner: str) -> bool:
        with self._connect() as db:
            return db.execute("SELECT 1 FROM invited WHERE owner = ?", (owner,)).fetchone() is not None

    def invite(self, owner: str) -> None:
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO invited (owner) VALUES (?)", (owner,))


class DynamoDbUsageStore:
    """Counters are `USAGE#<key>` items with `n` and the table's `expires_at`; invitations are `INVITED#<owner>`."""

    def __init__(self, table: Any) -> None:
        self._table = table

    @classmethod
    def for_table(cls, table_name: str) -> "DynamoDbUsageStore":
        import boto3  # imported lazily: in-memory runs and the unit tests never need AWS

        return cls(boto3.resource("dynamodb").Table(table_name))

    def consume(self, key: str, limit: int, expires_at: int) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._table.update_item(
                Key={"pk": f"USAGE#{key}"},
                UpdateExpression="ADD n :one SET expires_at = :exp",
                ConditionExpression="attribute_not_exists(n) OR n < :limit",
                ExpressionAttributeValues={":one": 1, ":limit": limit, ":exp": expires_at},
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def release(self, key: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self._table.update_item(
                Key={"pk": f"USAGE#{key}"},
                UpdateExpression="ADD n :neg",
                ConditionExpression="n > :zero",
                ExpressionAttributeValues={":neg": -1, ":zero": 0},
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise

    def is_invited(self, owner: str) -> bool:
        return self._table.get_item(Key={"pk": f"INVITED#{owner}"}, ConsistentRead=True).get("Item") is not None

    def invite(self, owner: str) -> None:
        # No `expires_at`: the 7-day clean-up must never un-invite someone.
        self._table.put_item(Item={"pk": f"INVITED#{owner}", "owner": owner})


class DailyCaps:
    """Charges one investigation to the user's day and the site's day, or refuses with a clear 429."""

    def __init__(
        self,
        store: UsageStore,
        *,
        per_user: int,
        total: int,
        costs_money: bool,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._per_user = per_user
        self._total = total
        self._active = costs_money and (per_user > 0 or total > 0)
        self._clock = clock

    @property
    def active(self) -> bool:
        return self._active

    def refund(self, owner: str) -> None:
        """Undo one charge (the investigation could not start)."""
        if not self._active:
            return
        day = f"{self._clock():%Y%m%d}"
        if self._per_user > 0:
            self._store.release(f"user#{owner}#{day}")
        if self._total > 0:
            self._store.release(f"all#{day}")

    def charge(self, owner: str) -> None:
        if not self._active:
            return
        now = self._clock()
        day = f"{now:%Y%m%d}"
        expires = int(time.time()) + COUNTER_TTL_SECONDS
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait = max(60, int((tomorrow - now).total_seconds()))
        user_key = f"user#{owner}#{day}"
        if self._per_user > 0 and not self._store.consume(user_key, self._per_user, expires):
            raise RateLimitedError(
                f"You have used your {self._per_user} investigations for today. They reset at midnight UTC.", wait
            )
        if self._total > 0 and not self._store.consume(f"all#{day}", self._total, expires):
            if self._per_user > 0:
                self._store.release(user_key)  # the refusal is the site's, so it must not count against this user
            raise RateLimitedError("The site has reached its daily investigation limit. Try again tomorrow.", wait)
