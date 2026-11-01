"""DynamoDB-backed investigation store, exercised against moto (the real conditional-write semantics).

Skipped where moto cannot be installed (it needs PyYAML, which has no wheel for Python 3.15 yet);
CI runs them on the Lambda runtime's Python.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.dynamodb_store import DynamoDbInvestigationStore
from app.agent.models import ProviderInfo
from app.config import ConfigError, Settings
from app.errors import InvestigationInProgressError, InvestigationNotFoundError
from app.identity import ANONYMOUS_OWNER as OWNER
from app.main import create_app
from app.simulator.service import Simulator

boto3 = pytest.importorskip("boto3")
mock_aws = pytest.importorskip("moto").mock_aws

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
INFO = ProviderInfo(kind="mock", label="test")


class Clock:
    def __init__(self) -> None:
        self.t = NOW.timestamp()

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def table(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(name, "testing")  # moto never reaches AWS; these only satisfy the SDK
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        ddb = boto3.resource("dynamodb")
        ddb.create_table(
            TableName="inv",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield ddb.Table("inv")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(table: Any, clock: Clock) -> DynamoDbInvestigationStore:
    return DynamoDbInvestigationStore(table, now=clock)


def test_create_returns_a_running_record_and_it_can_be_read_back(store: DynamoDbInvestigationStore) -> None:
    record = store.create(OWNER, "INC-1001", INFO, NOW)
    assert (record.id, record.status, record.incident_id) == ("inv-0001", "running", "INC-1001")
    assert store.get(OWNER, "inv-0001") == record
    assert store.latest_for(OWNER, "INC-1001") == record


def test_a_second_start_for_the_same_incident_is_rejected_but_other_incidents_are_not(
    store: DynamoDbInvestigationStore,
) -> None:
    store.create(OWNER, "INC-1001", INFO, NOW)
    with pytest.raises(InvestigationInProgressError):
        store.create(OWNER, "INC-1001", INFO, NOW)
    assert store.create(OWNER, "INC-1002", INFO, NOW).id == "inv-0002"


def test_finishing_releases_the_incident_and_ids_keep_counting(store: DynamoDbInvestigationStore) -> None:
    first = store.create(OWNER, "INC-1001", INFO, NOW)
    store.save(OWNER, first.model_copy(update={"status": "completed", "finished_at": NOW}))
    second = store.create(OWNER, "INC-1001", INFO, NOW)
    assert second.id == "inv-0002"
    assert store.get(OWNER, "inv-0001").status == "completed"
    assert store.latest_for(OWNER, "INC-1001") == second


def test_progress_saves_keep_the_run_locked(store: DynamoDbInvestigationStore) -> None:
    first = store.create(OWNER, "INC-1001", INFO, NOW)
    store.save(OWNER, first.model_copy(update={"usage": first.usage.model_copy(update={"rounds": 1})}))
    assert store.get(OWNER, first.id).usage.rounds == 1
    with pytest.raises(InvestigationInProgressError):
        store.create(OWNER, "INC-1001", INFO, NOW)


def test_unknown_ids_and_incidents(store: DynamoDbInvestigationStore) -> None:
    with pytest.raises(InvestigationNotFoundError):
        store.get(OWNER, "inv-0099")
    assert store.latest_for(OWNER, "INC-1001") is None


def test_saving_an_unknown_investigation_is_ignored(store: DynamoDbInvestigationStore) -> None:
    ghost = store.create(OWNER, "INC-1001", INFO, NOW).model_copy(update={"id": "inv-0077"})
    store.save(OWNER, ghost)
    with pytest.raises(InvestigationNotFoundError):
        store.get(OWNER, "inv-0077")


def test_a_run_that_died_frees_the_incident_and_reads_as_failed(
    store: DynamoDbInvestigationStore, clock: Clock
) -> None:
    orphan = store.create(OWNER, "INC-1001", INFO, NOW)
    clock.t += 59
    with pytest.raises(InvestigationInProgressError):
        store.create(OWNER, "INC-1001", INFO, NOW)
    assert store.get(OWNER, orphan.id).status == "running"

    clock.t += 2  # past the 60s lock
    gone = store.get(OWNER, orphan.id)
    assert gone.status == "failed" and gone.failure is not None and gone.failure.code == "internal_error"
    assert gone.finished_at is not None
    assert store.get(OWNER, orphan.id).status == "failed"  # persisted, not just computed
    assert store.create(OWNER, "INC-1001", INFO, NOW).id == "inv-0002"


def test_an_old_lock_does_not_release_a_newer_run(store: DynamoDbInvestigationStore, clock: Clock) -> None:
    old = store.create(OWNER, "INC-1001", INFO, NOW)
    clock.t += 61
    newer = store.create(OWNER, "INC-1001", INFO, NOW)
    store.save(OWNER, old.model_copy(update={"status": "failed"}))  # the old run finally ends
    with pytest.raises(InvestigationInProgressError):  # ...but the newer run still holds the incident
        store.create(OWNER, "INC-1001", INFO, NOW)
    store.save(OWNER, newer.model_copy(update={"status": "completed"}))
    store.create(OWNER, "INC-1001", INFO, NOW)


def test_records_expire_through_ttl(store: DynamoDbInvestigationStore, table: Any, clock: Clock) -> None:
    record = store.create(OWNER, "INC-1001", INFO, NOW)
    item = table.get_item(Key={"pk": f"INV#{record.id}"})["Item"]
    assert int(item["expires_at"]) == int(clock.t) + 7 * 24 * 3600


def test_the_api_runs_an_investigation_end_to_end_on_dynamodb(
    sim: Simulator, store: DynamoDbInvestigationStore
) -> None:
    client = TestClient(create_app(simulator=sim, store=store))
    started = client.post("/api/incidents/INC-1001/investigations")
    assert started.status_code == 202 and started.json()["id"] == "inv-0001"
    done = client.get("/api/investigations/inv-0001").json()
    assert done["status"] == "completed" and done["findings"]["confidence"] == "high"
    assert client.get("/api/incidents/INC-1001/investigations/latest").json() == done
    # the guard releases once finished, so a re-run is allowed
    assert client.post("/api/incidents/INC-1001/investigations").status_code == 202


def test_feedback_is_saved_on_dynamodb_and_does_not_reopen_the_incident_for_another_run(
    sim: Simulator, store: DynamoDbInvestigationStore
) -> None:
    client = TestClient(create_app(simulator=sim, store=store))
    inv_id = client.post("/api/incidents/INC-1001/investigations").json()["id"]
    saved = client.post(f"/api/investigations/{inv_id}/feedback", json={"helpful": True, "comment": "clear"})
    assert saved.status_code == 200
    stored = store.get(OWNER, inv_id).feedback
    assert stored is not None and stored.comment == "clear"
    assert client.get("/api/incidents/INC-1001/investigations/latest").json()["feedback"]["helpful"] is True
    assert client.post("/api/incidents/INC-1001/investigations").status_code == 202  # still free to re-run


def test_storage_mode_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STORAGE_MODE", raising=False)  # conftest pins "memory" for the rest of the suite
    assert Settings.from_env().storage_mode == "local"
    monkeypatch.setenv("STORAGE_MODE", "dynamodb")
    with pytest.raises(ConfigError):
        Settings.from_env()  # table name missing
    monkeypatch.setenv("DYNAMODB_TABLE", "agentops-dev")
    assert Settings.from_env().dynamodb_table == "agentops-dev"
    monkeypatch.setenv("STORAGE_MODE", "sqlite")
    with pytest.raises(ConfigError):
        Settings.from_env()
