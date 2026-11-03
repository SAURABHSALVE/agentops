"""The alarm snapshots in the investigations table: item `AWSLIVE#<owner>`, a JSON list in `body`.

Same three calls the table already allows (GetItem, PutItem, DeleteItem), so no new access is needed. The item carries
the table's `expires_at`, so a snapshot ages out after 7 days, exactly as the investigations that refer to it do.
"""

import time
from collections.abc import Callable
from typing import Any

from app.live.models import LiveAlarm
from app.live.store import decode, encode

TTL_SECONDS = 7 * 24 * 3600


class DynamoDbLiveSnapshotStore:
    def __init__(self, table: Any, *, now: Callable[[], float] = time.time) -> None:
        self._table = table
        self._now = now

    @classmethod
    def for_table(cls, table_name: str) -> "DynamoDbLiveSnapshotStore":
        import boto3  # imported lazily: in-memory runs and the unit tests never need AWS

        return cls(boto3.resource("dynamodb").Table(table_name))

    @staticmethod
    def _key(owner: str) -> dict[str, str]:
        return {"pk": f"AWSLIVE#{owner}"}

    def load(self, owner: str) -> list[LiveAlarm]:
        item = self._table.get_item(Key=self._key(owner), ConsistentRead=True).get("Item")
        return [] if item is None else decode(str(item.get("body", "")))

    def save(self, owner: str, alarms: list[LiveAlarm]) -> None:
        self._table.put_item(
            Item={
                **self._key(owner),
                "owner": owner,
                "body": encode(alarms),
                "expires_at": int(self._now()) + TTL_SECONDS,
            }
        )

    def delete(self, owner: str) -> None:
        self._table.delete_item(Key=self._key(owner))
