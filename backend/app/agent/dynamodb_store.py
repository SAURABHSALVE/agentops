"""Investigation records in DynamoDB, so state survives across Lambda containers.

Single table, partition key `pk` only. Item kinds:
  COUNTER              atomic id source (`n`)
  INV#<id>             the investigation as JSON in `body` (+ `owner`, `incident_id`, `status`)
  LATEST#<owner>#<incident>   pointer to that owner's newest investigation of an incident
  LOCK#<owner>#<incident>     that owner's one-running-per-incident guard, taken with a conditional put

`<owner>` is a Cognito `sub` (a UUID, so it contains no `#`) or "anonymous" when sign-in is off. A record that
belongs to someone else is reported as not found. Items written before owners existed have no `owner` attribute
and read as "anonymous"; their pointers and locks used the old key shape and simply age out with the TTL.

`expires_at` is the table's TTL attribute. The lock also carries a short expiry that the conditional
put honours itself, because TTL deletion can lag by many hours. A container that dies mid-run
therefore frees its incident after `lock_seconds`, and the orphaned 'running' record is reported as
failed on the next read instead of polling forever.
"""

import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import ClientError

from app.agent.coordinator import FAILURE_MESSAGES
from app.agent.models import Failure, Investigation, ProviderInfo
from app.errors import InvestigationInProgressError, InvestigationNotFoundError
from app.identity import ANONYMOUS_OWNER

RECORD_TTL_SECONDS = 7 * 24 * 3600
LOCK_SECONDS = 60  # comfortably above the 29s Lambda timeout


def _is_conditional_failure(exc: ClientError) -> bool:
    return bool(exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException")


def _owned_by(owner: str) -> str:
    """Condition: the item belongs to `owner`. An item with no owner attribute predates sign-in and is anonymous's."""
    return "(#w = :owner OR attribute_not_exists(#w))" if owner == ANONYMOUS_OWNER else "#w = :owner"


class DynamoDbInvestigationStore:
    def __init__(
        self,
        table: Any,
        *,
        now: Callable[[], float] = time.time,
        record_ttl_seconds: int = RECORD_TTL_SECONDS,
        lock_seconds: int = LOCK_SECONDS,
    ) -> None:
        self._table = table
        self._now = now
        self._record_ttl = record_ttl_seconds
        self._lock_seconds = lock_seconds

    @classmethod
    def for_table(cls, table_name: str) -> "DynamoDbInvestigationStore":
        import boto3  # imported lazily: in-memory runs and the unit tests never need AWS

        return cls(boto3.resource("dynamodb").Table(table_name))

    # ---- InvestigationStore ----------------------------------------------------------------

    def create(self, owner: str, incident_id: str, provider: ProviderInfo, started_at: datetime) -> Investigation:
        now = int(self._now())
        token = uuid.uuid4().hex
        lock_key = {"pk": f"LOCK#{owner}#{incident_id}"}
        try:
            self._table.put_item(
                Item={**lock_key, "holder": token, "expires_at": now + self._lock_seconds},
                ConditionExpression="attribute_not_exists(pk) OR expires_at < :now",
                ExpressionAttributeValues={":now": now},
            )
        except ClientError as exc:
            if _is_conditional_failure(exc):
                raise InvestigationInProgressError("An investigation is already running for this incident.") from None
            raise

        try:
            counter = self._table.update_item(
                Key={"pk": "COUNTER"},
                UpdateExpression="ADD n :one",
                ExpressionAttributeValues={":one": 1},
                ReturnValues="UPDATED_NEW",
            )
            record = Investigation(
                id=f"inv-{int(counter['Attributes']['n']):04d}",
                incident_id=incident_id,
                status="running",
                provider=provider,
                started_at=started_at,
            )
            self._table.put_item(
                Item={
                    "pk": f"INV#{record.id}",
                    "owner": owner,
                    "incident_id": incident_id,
                    "status": record.status,
                    "body": record.model_dump_json(),
                    "expires_at": self._expiry(),
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
            self._table.put_item(
                Item={
                    "pk": f"LATEST#{owner}#{incident_id}",
                    "investigation_id": record.id,
                    "expires_at": self._expiry(),
                }
            )
            self._table.update_item(
                Key=lock_key,
                UpdateExpression="SET investigation_id = :id",
                ConditionExpression="holder = :token",
                ExpressionAttributeValues={":id": record.id, ":token": token},
            )
        except Exception:
            self._release(owner, incident_id, holder=token)
            raise
        return record

    def save(self, owner: str, investigation: Investigation) -> None:
        try:
            # An update, not a put: the stored item keeps its owner, and only the owner's own record matches.
            self._update(investigation, condition=f"attribute_exists(pk) AND {_owned_by(owner)}", owner=owner)
        except ClientError as exc:
            if _is_conditional_failure(exc):
                return  # unknown id, or someone else's: ignored, like the in-memory store
            raise
        if investigation.status != "running":
            self._release(owner, investigation.incident_id, investigation_id=investigation.id)

    def get(self, owner: str, investigation_id: str) -> Investigation:
        item = self._table.get_item(Key={"pk": f"INV#{investigation_id}"}, ConsistentRead=True).get("Item")
        if item is None or item.get("owner", ANONYMOUS_OWNER) != owner:
            raise InvestigationNotFoundError("Investigation not found.")
        return self._expire_if_orphaned(owner, Investigation.model_validate_json(item["body"]))

    def latest_for(self, owner: str, incident_id: str) -> Investigation | None:
        pointer = self._table.get_item(Key={"pk": f"LATEST#{owner}#{incident_id}"}, ConsistentRead=True).get("Item")
        if pointer is None:
            return None
        try:
            return self.get(owner, str(pointer["investigation_id"]))
        except InvestigationNotFoundError:
            return None  # the record aged out via TTL before its pointer did

    def delete_for_owner(self, owner: str, incident_ids: list[str]) -> int:
        """Delete the newest record of each incident, its pointer and its lock (item calls only: no Scan, no Query).

        Older runs of the same incident have no pointer to find them by; they are unreachable through the newest
        one and expire with the 7-day TTL.
        """
        removed = 0
        for incident_id in incident_ids:
            pointer_key = {"pk": f"LATEST#{owner}#{incident_id}"}
            pointer = self._table.get_item(Key=pointer_key, ConsistentRead=True).get("Item")
            if pointer is None:
                continue
            record_key = {"pk": f"INV#{pointer['investigation_id']}"}
            item = self._table.get_item(Key=record_key, ConsistentRead=True).get("Item")
            if item is not None and item.get("owner", ANONYMOUS_OWNER) == owner:
                self._table.delete_item(Key=record_key)
                removed += 1
            self._table.delete_item(Key=pointer_key)
            self._table.delete_item(Key={"pk": f"LOCK#{owner}#{incident_id}"})
        return removed

    def list_for_incident(self, owner: str, incident_id: str) -> list[Investigation]:
        # The single-table design has no per-incident index, so the timeline shows the newest run only.
        latest = self.latest_for(owner, incident_id)
        return [latest] if latest else []

    # ---- internals -------------------------------------------------------------------------

    def _expiry(self) -> int:
        return int(self._now()) + self._record_ttl

    def _update(
        self,
        investigation: Investigation,
        *,
        condition: str,
        owner: str,
        values: dict[str, Any] | None = None,
    ) -> None:
        """Rewrite the record's mutable fields in place when `condition` holds (`#w` names the owner attribute)."""
        self._table.update_item(
            Key={"pk": f"INV#{investigation.id}"},
            UpdateExpression="SET #s = :status, body = :body, expires_at = :exp",
            ConditionExpression=condition,
            ExpressionAttributeNames={"#s": "status", "#w": "owner"},
            ExpressionAttributeValues={
                ":status": investigation.status,
                ":body": investigation.model_dump_json(),
                ":exp": self._expiry(),
                ":owner": owner,
                **(values or {}),
            },
        )

    def _release(
        self, owner: str, incident_id: str, *, holder: str | None = None, investigation_id: str | None = None
    ) -> None:
        """Release the lock only if it is still ours. Best effort: a lock that is not released simply expires."""
        if holder is not None:  # still holding the pre-id token (create() failed part-way)
            condition, value = "holder = :v", holder
        else:
            condition, value = "investigation_id = :v", str(investigation_id)
        try:
            self._table.delete_item(
                Key={"pk": f"LOCK#{owner}#{incident_id}"},
                ConditionExpression=condition,
                ExpressionAttributeValues={":v": value},
            )
        except ClientError:
            pass

    def _expire_if_orphaned(self, owner: str, record: Investigation) -> Investigation:
        if record.status != "running" or self._now() - record.started_at.timestamp() <= self._lock_seconds:
            return record
        failed = record.model_copy(
            update={
                "status": "failed",
                "failure": Failure(code="internal_error", message=FAILURE_MESSAGES["internal_error"]),
                "finished_at": datetime.fromtimestamp(self._now(), UTC),
            }
        )
        try:  # only if nobody finished it in the meantime
            self._update(
                failed, condition=f"#s = :running AND {_owned_by(owner)}", owner=owner, values={":running": "running"}
            )
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise
            return self.get(owner, failed.id)
        return failed
