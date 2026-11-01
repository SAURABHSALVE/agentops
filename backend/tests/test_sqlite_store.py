"""Phase 4: investigation history is durable across restarts, and a crash never blocks an incident."""

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.models import ProviderInfo
from app.agent.sqlite_store import SqliteInvestigationStore
from app.config import Settings
from app.errors import InvestigationInProgressError, InvestigationNotFoundError
from app.identity import ANONYMOUS_OWNER as OWNER
from app.main import create_app
from app.repository import InMemoryIncidentRepository
from app.simulator.service import Simulator

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
INFO = ProviderInfo(kind="mock", label="test")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "data" / "agentops.sqlite3"


def test_creates_the_directory_and_round_trips_a_record(db: Path) -> None:
    store = SqliteInvestigationStore(db)
    created = store.create(OWNER, "INC-1001", INFO, NOW)
    assert created.id == "inv-0001" and db.exists()
    assert store.get(OWNER, "inv-0001") == created
    assert store.latest_for(OWNER, "INC-1001") == created and store.latest_for(OWNER, "INC-1002") is None
    with pytest.raises(InvestigationNotFoundError):
        store.get(OWNER, "inv-0099")


def test_one_running_investigation_per_incident_and_ids_keep_counting(db: Path) -> None:
    store = SqliteInvestigationStore(db)
    first = store.create(OWNER, "INC-1001", INFO, NOW)
    with pytest.raises(InvestigationInProgressError):
        store.create(OWNER, "INC-1001", INFO, NOW)
    assert store.create(OWNER, "INC-1002", INFO, NOW).id == "inv-0002"
    store.save(OWNER, first.model_copy(update={"status": "completed"}))
    assert store.create(OWNER, "INC-1001", INFO, NOW).id == "inv-0003"
    assert [i.id for i in store.list_for_incident(OWNER, "INC-1001")] == ["inv-0001", "inv-0003"]


def test_concurrent_starts_for_one_incident_admit_exactly_one(db: Path) -> None:
    store = SqliteInvestigationStore(db)
    results: list[str] = []

    def start() -> None:
        try:
            results.append(store.create(OWNER, "INC-1001", INFO, NOW).id)
        except InvestigationInProgressError:
            results.append("busy")

    threads = [threading.Thread(target=start) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("busy") == 7 and len([r for r in results if r != "busy"]) == 1


def test_history_survives_a_restart_and_a_second_store_sees_it(db: Path) -> None:
    store = SqliteInvestigationStore(db)
    done = store.create(OWNER, "INC-1001", INFO, NOW).model_copy(update={"status": "completed"})
    store.save(OWNER, done)
    reopened = SqliteInvestigationStore(db)
    assert reopened.get(OWNER, done.id) == done
    assert reopened.create(OWNER, "INC-1001", INFO, NOW).id == "inv-0002"  # ids do not restart


def test_a_run_interrupted_by_a_restart_is_failed_not_stuck(db: Path) -> None:
    store = SqliteInvestigationStore(db)
    stuck = store.create(OWNER, "INC-1001", INFO, NOW)
    reopened = SqliteInvestigationStore(db)  # the "restart"
    recovered = reopened.get(OWNER, stuck.id)
    assert recovered.status == "failed" and recovered.failure is not None
    assert recovered.failure.code == "internal_error" and "restarted" in recovered.failure.message
    assert recovered.finished_at is not None
    assert reopened.create(OWNER, "INC-1001", INFO, NOW).id == "inv-0002"  # the incident is not blocked


def test_save_of_an_unknown_record_does_nothing(db: Path, tmp_path: Path) -> None:
    other = SqliteInvestigationStore(tmp_path / "other.sqlite3").create(OWNER, "INC-1001", INFO, NOW)
    store = SqliteInvestigationStore(db)
    store.save(OWNER, other)
    with pytest.raises(InvestigationNotFoundError):
        store.get(OWNER, other.id)


def test_corrupt_rows_are_treated_as_absent_not_served(db: Path) -> None:
    store = SqliteInvestigationStore(db)
    rec = store.create(OWNER, "INC-1001", INFO, NOW)
    store.save(OWNER, rec.model_copy(update={"status": "completed"}))
    with sqlite3.connect(db) as raw:
        raw.execute("UPDATE investigations SET body = '{not valid' WHERE id = ?", (rec.id,))
    with pytest.raises(InvestigationNotFoundError):
        store.get(OWNER, rec.id)
    assert store.latest_for(OWNER, "INC-1001") is None and store.list_for_incident(OWNER, "INC-1001") == []


def test_values_are_bound_not_interpolated(db: Path) -> None:
    store = SqliteInvestigationStore(db)
    with pytest.raises(InvestigationNotFoundError):
        store.get(OWNER, "inv-1' OR '1'='1")
    assert store.latest_for(OWNER, "INC-1001' OR '1'='1") is None


def test_the_full_workflow_survives_an_app_restart(tmp_path: Path) -> None:
    """Investigate, approve, 'restart' the whole app on the same file, and the history is still there."""

    def new_app() -> TestClient:
        settings = Settings(storage_mode="local", local_data_dir=str(tmp_path))
        return TestClient(create_app(simulator=Simulator(InMemoryIncidentRepository()), settings=settings))

    first = new_app()
    inv_id = first.post("/api/incidents/INC-1006/investigations").json()["id"]
    first.post(f"/api/investigations/{inv_id}/remediation/approve")

    second = new_app()
    body: dict[str, Any] = second.get("/api/incidents/INC-1006/investigations/latest").json()
    assert body["id"] == inv_id and body["status"] == "completed"
    assert body["remediation_state"]["status"] == "not_resolved"
    titles = [e["title"] for e in second.get("/api/incidents/INC-1006/timeline").json()]
    assert "Verification FAILED: the incident is not resolved" in titles
    assert second.post("/api/incidents/INC-1006/investigations").json()["id"] == "inv-0002"
