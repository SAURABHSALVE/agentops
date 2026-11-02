"""A user's saved connection in the investigations table: item `AWSCONN#<owner>`, the record as JSON in `body`.

It uses the same four calls the investigation store already has permission for (GetItem, PutItem, DeleteItem), so no
new access is needed. The item has no `expires_at`, so the table's 7-day expiry never removes a connection: it lives
until the user disconnects.
"""

from typing import Any

from app.connections.models import AwsConnection
from app.connections.store import load


class DynamoDbConnectionStore:
    def __init__(self, table: Any) -> None:
        self._table = table

    @classmethod
    def for_table(cls, table_name: str) -> "DynamoDbConnectionStore":
        import boto3  # imported lazily: in-memory runs and the unit tests never need AWS

        return cls(boto3.resource("dynamodb").Table(table_name))

    @staticmethod
    def _key(owner: str) -> dict[str, str]:
        return {"pk": f"AWSCONN#{owner}"}

    def get(self, owner: str) -> AwsConnection | None:
        item = self._table.get_item(Key=self._key(owner), ConsistentRead=True).get("Item")
        return None if item is None else load(str(item.get("body", "")))

    def put(self, owner: str, connection: AwsConnection) -> None:
        self._table.put_item(Item={**self._key(owner), "owner": owner, "body": connection.model_dump_json()})

    def delete(self, owner: str) -> None:
        self._table.delete_item(Key=self._key(owner))
