"""Every InvestigationStore keeps each owner's records apart: the same contract, run against all three stores.

The DynamoDB case needs moto (skipped where it cannot be installed; CI runs it on the Lambda runtime's Python).
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.agent.models import ProviderInfo
from app.agent.sqlite_store import SqliteInvestigationStore
from app.agent.store import InMemoryInvestigationStore, InvestigationStore
from app.errors import InvestigationInProgressError, InvestigationNotFoundError
from app.identity import ANONYMOUS_OWNER
from tests.auth_helpers import USER_A, USER_B

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
INFO = ProviderInfo(kind="mock", label="test")


@pytest.fixture(params=["memory", "sqlite", "dynamodb"])
def store(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[InvestigationStore]:
    if request.param == "memory":
        yield InMemoryInvestigationStore()
    elif request.param == "sqlite":
        yield SqliteInvestigationStore(tmp_path / "agentops.sqlite3")
    else:
        boto3 = pytest.importorskip("boto3")
        mock_aws = pytest.importorskip("moto").mock_aws
        from app.agent.dynamodb_store import DynamoDbInvestigationStore

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
            yield DynamoDbInvestigationStore(ddb.Table("inv"), now=lambda: NOW.timestamp())


def test_one_owner_cannot_read_another_owners_record(store: InvestigationStore) -> None:
    mine = store.create(USER_A, "INC-1001", INFO, NOW)
    assert store.get(USER_A, mine.id) == mine
    with pytest.raises(InvestigationNotFoundError):
        store.get(USER_B, mine.id)
    with pytest.raises(InvestigationNotFoundError):
        store.get(ANONYMOUS_OWNER, mine.id)
    assert store.latest_for(USER_B, "INC-1001") is None
    assert store.list_for_incident(USER_B, "INC-1001") == []
    assert store.latest_for(USER_A, "INC-1001") == mine


def test_each_owner_may_have_one_running_investigation_of_the_same_incident(store: InvestigationStore) -> None:
    a = store.create(USER_A, "INC-1001", INFO, NOW)
    b = store.create(USER_B, "INC-1001", INFO, NOW)  # not blocked by A's run
    assert a.id != b.id  # ids are unique across owners
    with pytest.raises(InvestigationInProgressError):
        store.create(USER_A, "INC-1001", INFO, NOW)
    with pytest.raises(InvestigationInProgressError):
        store.create(USER_B, "INC-1001", INFO, NOW)


def test_finishing_one_owners_run_frees_only_their_incident(store: InvestigationStore) -> None:
    a = store.create(USER_A, "INC-1001", INFO, NOW)
    store.create(USER_B, "INC-1001", INFO, NOW)
    store.save(USER_A, a.model_copy(update={"status": "completed", "finished_at": NOW}))
    assert store.create(USER_A, "INC-1001", INFO, NOW).id != a.id
    with pytest.raises(InvestigationInProgressError):
        store.create(USER_B, "INC-1001", INFO, NOW)  # B is still running


def test_saving_someone_elses_record_changes_nothing(store: InvestigationStore) -> None:
    mine = store.create(USER_A, "INC-1001", INFO, NOW)
    store.save(USER_B, mine.model_copy(update={"status": "failed", "finished_at": NOW}))
    assert store.get(USER_A, mine.id).status == "running"
    with pytest.raises(InvestigationNotFoundError):
        store.get(USER_B, mine.id)
    # ...and B's attempt did not release A's lock either.
    with pytest.raises(InvestigationInProgressError):
        store.create(USER_A, "INC-1001", INFO, NOW)


def test_the_owner_survives_every_save(store: InvestigationStore) -> None:
    mine = store.create(USER_A, "INC-1001", INFO, NOW)
    store.save(USER_A, mine.model_copy(update={"usage": mine.usage.model_copy(update={"rounds": 2})}))
    done = mine.model_copy(update={"status": "completed", "finished_at": NOW})
    store.save(USER_A, done)
    assert store.get(USER_A, mine.id).status == "completed"
    with pytest.raises(InvestigationNotFoundError):
        store.get(USER_B, mine.id)


def test_a_record_is_listed_only_for_its_owner_and_incident(store: InvestigationStore) -> None:
    a1 = store.create(USER_A, "INC-1001", INFO, NOW)
    store.create(USER_A, "INC-1002", INFO, NOW)
    store.create(USER_B, "INC-1001", INFO, NOW)
    assert [r.id for r in store.list_for_incident(USER_A, "INC-1001")] == [a1.id]


def test_the_anonymous_owner_is_just_another_owner(store: InvestigationStore) -> None:
    anon = store.create(ANONYMOUS_OWNER, "INC-1001", INFO, NOW)
    assert store.latest_for(ANONYMOUS_OWNER, "INC-1001") == anon
    assert store.latest_for(USER_A, "INC-1001") is None


def test_deleting_an_owners_records_leaves_everyone_elses_and_frees_their_locks(store: InvestigationStore) -> None:
    done = store.create(USER_A, "INC-1001", INFO, NOW)
    store.save(USER_A, done.model_copy(update={"status": "completed", "finished_at": NOW}))
    running = store.create(USER_A, "INC-1002", INFO, NOW)
    theirs = store.create(USER_B, "INC-1001", INFO, NOW)

    assert store.delete_for_owner(USER_A, ["INC-1001", "INC-1002"]) == 2
    for gone in (done, running):
        with pytest.raises(InvestigationNotFoundError):
            store.get(USER_A, gone.id)
    assert store.latest_for(USER_A, "INC-1001") is None and store.list_for_incident(USER_A, "INC-1002") == []
    assert store.get(USER_B, theirs.id) == theirs  # untouched
    assert store.create(USER_A, "INC-1002", INFO, NOW).id != running.id  # A's lock went with A's record
    with pytest.raises(InvestigationInProgressError):
        store.create(USER_B, "INC-1001", INFO, NOW)  # B's lock is still B's


# ---- records written before owners existed ---------------------------------------------------


def test_a_sqlite_database_from_before_sign_in_is_upgraded_and_belongs_to_anonymous(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    first = InMemoryInvestigationStore().create(ANONYMOUS_OWNER, "INC-1001", INFO, NOW)
    finished = first.model_copy(update={"status": "completed", "finished_at": NOW})
    with sqlite3.connect(path) as db:  # the schema as it was before the owner column
        db.executescript(
            "CREATE TABLE investigations (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE,"
            " incident_id TEXT NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL);"
        )
        db.execute(
            "INSERT INTO investigations (id, incident_id, status, body) VALUES (?, ?, ?, ?)",
            (finished.id, "INC-1001", "completed", finished.model_dump_json()),
        )
    store = SqliteInvestigationStore(path)
    assert store.get(ANONYMOUS_OWNER, finished.id) == finished
    with pytest.raises(InvestigationNotFoundError):
        store.get(USER_A, finished.id)
    assert store.create(USER_A, "INC-1001", INFO, NOW).id == "inv-0002"  # ids carry on
    SqliteInvestigationStore(path)  # opening an upgraded database again is harmless


def test_a_dynamodb_item_with_no_owner_reads_and_saves_as_anonymous() -> None:
    boto3 = pytest.importorskip("boto3")
    mock_aws = pytest.importorskip("moto").mock_aws
    from app.agent.dynamodb_store import DynamoDbInvestigationStore

    legacy = InMemoryInvestigationStore().create(ANONYMOUS_OWNER, "INC-1001", INFO, NOW)
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="inv",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        table: Any = ddb.Table("inv")
        table.put_item(
            Item={
                "pk": f"INV#{legacy.id}",
                "incident_id": "INC-1001",
                "status": "running",
                "body": legacy.model_dump_json(),
                "expires_at": int(NOW.timestamp()) + 3600,
            }
        )
        store = DynamoDbInvestigationStore(table, now=lambda: NOW.timestamp())
        assert store.get(ANONYMOUS_OWNER, legacy.id) == legacy
        with pytest.raises(InvestigationNotFoundError):
            store.get(USER_A, legacy.id)
        store.save(ANONYMOUS_OWNER, legacy.model_copy(update={"status": "completed", "finished_at": NOW}))
        assert store.get(ANONYMOUS_OWNER, legacy.id).status == "completed"
