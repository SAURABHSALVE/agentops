"""Where a user's saved connection lives. One record per owner; every call names the owner.

Three implementations with the same behaviour: in memory (tests), a SQLite file (local) and DynamoDB (AWS). A record
that no longer validates is treated as absent rather than served.
"""

from typing import Protocol

from pydantic import ValidationError

from app.connections.models import AwsConnection


class ConnectionStore(Protocol):
    def get(self, owner: str) -> AwsConnection | None: ...

    def put(self, owner: str, connection: AwsConnection) -> None: ...

    def delete(self, owner: str) -> None:
        """Remove the owner's connection. Removing one that does not exist is not an error."""
        ...


def load(body: str) -> AwsConnection | None:
    try:
        return AwsConnection.model_validate_json(body)
    except ValidationError:
        return None


class InMemoryConnectionStore:
    def __init__(self) -> None:
        self._items: dict[str, str] = {}

    def get(self, owner: str) -> AwsConnection | None:
        body = self._items.get(owner)
        return None if body is None else load(body)

    def put(self, owner: str, connection: AwsConnection) -> None:
        self._items[owner] = connection.model_dump_json()

    def delete(self, owner: str) -> None:
        self._items.pop(owner, None)
