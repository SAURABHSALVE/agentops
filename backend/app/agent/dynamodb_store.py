"""Investigation records in DynamoDB, so state survives across Lambda containers.

Single table, partition key `pk` only. Item kinds:
  COUNTER              atomic id source (`n`)
  INV#<id>             the investigation as JSON in `body` (+ `incident_id`, `status`)
  LATEST#<incident>    pointer to the newest investigation of an incident
  LOCK#<incident>      one-running-per-incident guard, taken with a conditional put

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

RECORD_TTL_SECONDS = 7 * 24 * 3600
LOCK_SECONDS = 60  # comfortably above the 29s Lambda timeout


def _is_conditional_failure(exc: ClientError) -> bool:
    return bool(exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException")


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

    def create(self, incident_id: str, provider: ProviderInfo, started_at: datetime) -> Investigation:
        now = int(self._now())
        token = uuid.uuid4().hex
        lock_key = {"pk": f"LOCK#{incident_id}"}
        try:
            self._table.put_item(
                Item={**lock_key, "owner": token, "expires_at": now + self._lock_seconds},
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
            self._put(record, condition="attribute_not_exists(pk)")
            self._table.put_item(
                Item={"pk": f"LATEST#{incident_id}", "investigation_id": record.id, "expires_at": self._expiry()}
            )
            self._table.update_item(
                Key=lock_key,
                UpdateExpression="SET investigation_id = :id",
                ConditionExpression="#o = :token",
                ExpressionAttributeNames={"#o": "owner"},
                ExpressionAttributeValues={":id": record.id, ":token": token},
            )
        except Exception:
            self._release(incident_id, owner=token)
            raise
        return record

    def save(self, investigation: Investigation) -> None:
        try:
            self._put(investigation, condition="attribute_exists(pk)")
        except ClientError as exc:
            if _is_conditional_failure(exc):
                return  # unknown id: ignored, like the in-memory store
            raise
        if investigation.status != "running":
            self._release(investigation.incident_id, investigation_id=investigation.id)

    def get(self, investigation_id: str) -> Investigation:
        item = self._table.get_item(Key={"pk": f"INV#{investigation_id}"}, ConsistentRead=True).get("Item")
        if item is None:
            raise InvestigationNotFoundError("Investigation not found.")
        return self._expire_if_orphaned(Investigation.model_validate_json(item["body"]))

    def latest_for(self, incident_id: str) -> Investigation | None:
        pointer = self._table.get_item(Key={"pk": f"LATEST#{incident_id}"}, ConsistentRead=True).get("Item")
        if pointer is None:
            return None
        try:
            return self.get(str(pointer["investigation_id"]))
        except InvestigationNotFoundError:
            return None  # the record aged out via TTL before its pointer did

    def list_for_incident(self, incident_id: str) -> list[Investigation]:
        # The single-table design has no per-incident index, so the timeline shows the newest run only.
        latest = self.latest_for(incident_id)
        return [latest] if latest else []

    # ---- internals -------------------------------------------------------------------------

    def _expiry(self) -> int:
        return int(self._now()) + self._record_ttl

    def _put(self, investigation: Investigation, *, condition: str, **condition_args: Any) -> None:
        self._table.put_item(
            Item={
                "pk": f"INV#{investigation.id}",
                "incident_id": investigation.incident_id,
                "status": investigation.status,
                "body": investigation.model_dump_json(),
                "expires_at": self._expiry(),
            },
            ConditionExpression=condition,
            **condition_args,
        )

    def _release(self, incident_id: str, *, owner: str | None = None, investigation_id: str | None = None) -> None:
        """Release the lock only if it is still ours. Best effort: a lock that is not released simply expires."""
        if owner is not None:  # still holding the pre-id token (create() failed part-way)
            condition, names, values = "#o = :v", {"#o": "owner"}, {":v": owner}
        else:
            condition, names, values = "investigation_id = :v", None, {":v": str(investigation_id)}
        args: dict[str, Any] = {"ConditionExpression": condition, "ExpressionAttributeValues": values}
        if names:
            args["ExpressionAttributeNames"] = names
        try:
            self._table.delete_item(Key={"pk": f"LOCK#{incident_id}"}, **args)
        except ClientError:
            pass

    def _expire_if_orphaned(self, record: Investigation) -> Investigation:
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
            self._put(
                failed,
                condition="#s = :running",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":running": "running"},
            )
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise
            return self.get(failed.id)
        return failed
