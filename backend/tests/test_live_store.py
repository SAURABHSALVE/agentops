"""Every LiveSnapshotStore behaves the same: one list per owner, kept apart, merged without losing what an
investigation may still refer to, and removable. The DynamoDB case needs moto (CI runs it)."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.live.alarms import alarm_id
from app.live.models import LiveAlarm
from app.live.sqlite_store import SqliteLiveSnapshotStore
from app.live.store import MAX_KEPT, InMemoryLiveSnapshotStore, LiveSnapshotStore, decode, merge
from tests.auth_helpers import USER_A, USER_B

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def alarm(name: str, seen: datetime = NOW, reason: str = "") -> LiveAlarm:
    return LiveAlarm(
        id=alarm_id("eu-west-1", name),
        name=name,
        region="eu-west-1",
        state_since=seen - timedelta(minutes=30),
        seen_at=seen,
        reason=reason,
    )


@pytest.fixture(params=["memory", "sqlite", "dynamodb"])
def store(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[LiveSnapshotStore]:
    if request.param == "memory":
        yield InMemoryLiveSnapshotStore()
    elif request.param == "sqlite":
        yield SqliteLiveSnapshotStore(tmp_path / "agentops.sqlite3")
    else:
        boto3 = pytest.importorskip("boto3")
        mock_aws = pytest.importorskip("moto").mock_aws
        from app.live.dynamodb_store import DynamoDbLiveSnapshotStore

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
            yield DynamoDbLiveSnapshotStore(ddb.Table("inv"), now=lambda: NOW.timestamp())


def test_nothing_saved_reads_as_empty(store: LiveSnapshotStore) -> None:
    assert store.load(USER_A) == []


def test_what_is_saved_comes_back_exactly(store: LiveSnapshotStore) -> None:
    mine = [alarm("a"), alarm("b", reason="Threshold crossed")]
    store.save(USER_A, mine)
    assert store.load(USER_A) == mine


def test_owners_are_kept_apart_and_delete_touches_only_the_owner(store: LiveSnapshotStore) -> None:
    store.save(USER_A, [alarm("a")])
    store.save(USER_B, [alarm("b")])
    assert [x.name for x in store.load(USER_A)] == ["a"] and [x.name for x in store.load(USER_B)] == ["b"]
    store.delete(USER_A)
    store.delete(USER_A)  # removing what is not there is not an error
    assert store.load(USER_A) == [] and [x.name for x in store.load(USER_B)] == ["b"]


def test_a_record_that_no_longer_validates_reads_as_empty() -> None:
    assert decode("not json") == [] and decode('[{"id": "nope"}]') == [] and decode("{}") == []


def test_a_sqlite_file_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "agentops.sqlite3"
    SqliteLiveSnapshotStore(path).save(USER_A, [alarm("a")])
    assert SqliteLiveSnapshotStore(path).load(USER_A) == [alarm("a")]


def test_merging_keeps_alarms_that_stopped_firing_and_takes_the_newest_reading() -> None:
    old = [alarm("gone", NOW - timedelta(hours=2)), alarm("same", NOW - timedelta(hours=2))]
    fresh = [alarm("same", NOW, reason="again"), alarm("new", NOW)]
    merged = merge(old, fresh)
    assert {a.name for a in merged} == {"gone", "same", "new"}  # an investigation of "gone" still finds its incident
    assert next(a for a in merged if a.name == "same").reason == "again"
    assert [a.name for a in merged][-1] == "gone"  # newest first


def test_the_list_is_bounded() -> None:
    many = [alarm(f"alarm-{i}", NOW - timedelta(minutes=i)) for i in range(MAX_KEPT + 20)]
    assert len(merge([], many)) == MAX_KEPT
    store = InMemoryLiveSnapshotStore()
    store.save(USER_A, many)
    assert len(store.load(USER_A)) == MAX_KEPT


def test_a_dynamodb_snapshot_expires_with_the_investigations_it_belongs_to() -> None:
    boto3 = pytest.importorskip("boto3")
    mock_aws = pytest.importorskip("moto").mock_aws
    from app.live.dynamodb_store import TTL_SECONDS, DynamoDbLiveSnapshotStore

    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="inv",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        table = ddb.Table("inv")
        DynamoDbLiveSnapshotStore(table, now=lambda: NOW.timestamp()).save(USER_A, [alarm("a")])
        item = table.get_item(Key={"pk": f"AWSLIVE#{USER_A}"})["Item"]
        assert set(item) == {"pk", "owner", "body", "expires_at"}
        assert int(item["expires_at"]) == int(NOW.timestamp()) + TTL_SECONDS == int(NOW.timestamp()) + 7 * 24 * 3600
