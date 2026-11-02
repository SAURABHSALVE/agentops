"""Every ConnectionStore behaves the same: one record per owner, kept apart, and removable.

The DynamoDB case needs moto (skipped where it cannot be installed; CI runs it on the Lambda runtime's Python).
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.connections.models import AwsConnection
from app.connections.sqlite_store import SqliteConnectionStore
from app.connections.store import ConnectionStore, InMemoryConnectionStore
from tests.auth_helpers import USER_A, USER_B

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def record(external_id: str = "agentops-abcdefghijklmnopqrstuvwx", **changes: object) -> AwsConnection:
    base = AwsConnection(
        status="draft",
        external_id=external_id,
        region="eu-west-1",
        log_groups=("/aws/lambda/checkout",),
        consent_version="v1",
        consent_provider="mock",
        consented_at=NOW,
        updated_at=NOW,
    )
    return base.model_copy(update=changes)


@pytest.fixture(params=["memory", "sqlite", "dynamodb"])
def store(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ConnectionStore]:
    if request.param == "memory":
        yield InMemoryConnectionStore()
    elif request.param == "sqlite":
        yield SqliteConnectionStore(tmp_path / "agentops.sqlite3")
    else:
        boto3 = pytest.importorskip("boto3")
        mock_aws = pytest.importorskip("moto").mock_aws
        from app.connections.dynamodb_store import DynamoDbConnectionStore

        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            monkeypatch.setenv(name, "testing")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        with mock_aws():
            ddb = boto3.resource("dynamodb")
            ddb.create_table(
                TableName="inv",
                KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )
            yield DynamoDbConnectionStore(ddb.Table("inv"))


def test_nothing_saved_reads_as_none(store: ConnectionStore) -> None:
    assert store.get(USER_A) is None


def test_what_is_saved_comes_back_exactly(store: ConnectionStore) -> None:
    mine = record(role_arn="arn:aws:iam::111122223333:role/AgentOpsReadOnly", status="registered")
    store.put(USER_A, mine)
    assert store.get(USER_A) == mine


def test_saving_again_replaces_it(store: ConnectionStore) -> None:
    store.put(USER_A, record())
    store.put(USER_A, record(region="us-east-1"))
    assert store.get(USER_A) == record(region="us-east-1")


def test_owners_are_kept_apart(store: ConnectionStore) -> None:
    store.put(USER_A, record("agentops-aaaaaaaaaaaaaaaaaaaaaaaa"))
    store.put(USER_B, record("agentops-bbbbbbbbbbbbbbbbbbbbbbbb"))
    assert store.get(USER_A) is not None and store.get(USER_A).external_id.endswith("aaaaaaaaaaaaaaaaaaaaaaaa")  # type: ignore[union-attr]
    assert store.get(USER_B) is not None and store.get(USER_B).external_id.endswith("bbbbbbbbbbbbbbbbbbbbbbbb")  # type: ignore[union-attr]
    store.delete(USER_A)
    assert store.get(USER_A) is None and store.get(USER_B) is not None  # deleting A's left B's alone


def test_deleting_what_is_not_there_is_not_an_error(store: ConnectionStore) -> None:
    store.delete(USER_A)
    store.put(USER_A, record())
    store.delete(USER_A)
    store.delete(USER_A)
    assert store.get(USER_A) is None


def test_a_sqlite_file_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "agentops.sqlite3"
    SqliteConnectionStore(path).put(USER_A, record())
    assert SqliteConnectionStore(path).get(USER_A) == record()


def test_a_dynamodb_connection_has_no_expiry_so_the_seven_day_clean_up_never_removes_it() -> None:
    boto3 = pytest.importorskip("boto3")
    mock_aws = pytest.importorskip("moto").mock_aws
    from app.connections.dynamodb_store import DynamoDbConnectionStore

    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="inv",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        table = ddb.Table("inv")
        DynamoDbConnectionStore(table).put(USER_A, record())
        item = table.get_item(Key={"pk": f"AWSCONN#{USER_A}"})["Item"]
        assert set(item) == {"pk", "owner", "body"}  # in particular, no `expires_at`
