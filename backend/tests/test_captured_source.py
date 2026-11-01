"""The captured-telemetry source: the same four read-only tools over data measured from the lab system.

These tests assert the adapter's contract (validated load, windowing, no look-ahead, honest verification,
agent grounding), not that the mock provider is good at real logs: it is a rule table written against the
simulated fixtures and is not expected to be.
"""

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings
from app.errors import MetricNotAvailableError
from app.main import create_app
from app.repository import InMemoryIncidentRepository
from app.schemas import GetDeploymentsArgs, GetIncidentArgs, GetMetricsArgs, MetricName, SearchLogsArgs
from app.simulator.captured import CapturedSource
from app.simulator.remediation import REMEDIATIONS
from app.simulator.service import Simulator

DATASET = Path(__file__).resolve().parents[1] / "data" / "captured" / "lab-capture.json"
IDS = ["INC-2001", "INC-2002", "INC-2003"]
QUERIES = {"INC-2001": "timeout", "INC-2002": "query", "INC-2003": "503"}
ALERT_METRIC: dict[str, MetricName] = {
    "INC-2001": "error_rate",
    "INC-2002": "p95_latency_ms",
    "INC-2003": "queue_depth",
}
ACTION = {
    "INC-2001": "simulate_clear_dependency_fault",
    "INC-2002": "simulate_kill_slow_query",
    "INC-2003": "simulate_restart_worker",
}


@pytest.fixture
def source() -> CapturedSource:
    return CapturedSource.load(DATASET)


@pytest.fixture
def real_sim(source: CapturedSource) -> Simulator:
    return Simulator(InMemoryIncidentRepository(source.incidents()), source)


@pytest.fixture
def real_client(real_sim: Simulator) -> TestClient:
    return TestClient(create_app(simulator=real_sim))


def test_the_dataset_loads_and_is_labelled_as_a_compressed_lab_capture(source: CapturedSource) -> None:
    raw = json.loads(DATASET.read_text(encoding="utf-8"))
    assert [i["incident"]["id"] for i in raw["incidents"]] == IDS
    assert source.data_mode == "captured" and "Not production data" in source.description
    assert any("compressed" in note for note in raw["meta"]["notes"])


def test_the_api_reports_the_data_mode_and_serves_only_captured_incidents(real_client: TestClient) -> None:
    assert real_client.get("/api/config").json()["data_mode"] == "captured"
    assert real_client.get("/health").json()["data_mode"] == "captured"
    assert [i["id"] for i in real_client.get("/api/incidents").json()] == IDS
    assert real_client.get("/api/incidents/INC-1001").status_code == 404  # simulated ids are not served here


def test_the_default_still_serves_the_simulated_incidents(client: TestClient) -> None:
    assert client.get("/api/config").json()["data_mode"] == "simulated"
    assert len(client.get("/api/incidents").json()) == 6


@pytest.mark.parametrize("iid", IDS)
def test_logs_are_windowed_and_ids_are_unique_across_the_dataset(real_sim: Simulator, iid: str) -> None:
    incident = real_sim.get_incident(GetIncidentArgs(incident_id=iid))
    result = real_sim.search_logs(SearchLogsArgs(incident_id=iid, query=QUERIES[iid], limit=50))
    assert result.total_matches > 0 and result.returned == len(result.entries) <= 50
    assert all(result.window_start <= e.timestamp <= incident.window_end for e in result.entries)
    assert all(e.service == incident.service for e in result.entries)
    assert result.reference_ids == [e.id for e in result.entries]


def test_log_ids_are_globally_unique(source: CapturedSource) -> None:
    ids = [e.id for i in source.incidents() for e in source.logs(i.id)]
    assert len(ids) == len(set(ids)) and all(i.startswith("log-") for i in ids)


@pytest.mark.parametrize("iid", IDS)
def test_metrics_show_a_real_departure_from_baseline_and_never_look_ahead(real_sim: Simulator, iid: str) -> None:
    incident = real_sim.get_incident(GetIncidentArgs(incident_id=iid))
    result = real_sim.get_service_metrics(GetMetricsArgs(incident_id=iid, metric=ALERT_METRIC[iid]))
    assert result.summary.peak >= 3 * max(result.summary.baseline, 0.1)
    assert result.window_end == incident.window_end and max(p.timestamp for p in result.points) <= incident.window_end
    assert result.reference_ids and result.reference_ids[0].startswith(f"metric:{ALERT_METRIC[iid]}:")


def test_an_unmeasured_metric_is_reported_as_unavailable(real_sim: Simulator) -> None:
    with pytest.raises(MetricNotAvailableError):
        real_sim.get_service_metrics(GetMetricsArgs(incident_id="INC-2001", metric="queue_depth"))


def test_deployments_come_from_the_dataset_and_a_real_release_is_just_before_the_alert(real_sim: Simulator) -> None:
    recent = real_sim.get_recent_deployments(GetDeploymentsArgs(incident_id="INC-2002", hours=6))
    assert [d.id for d in recent.deployments] == ["deploy-2302"]
    assert 0 <= recent.deployments[0].minutes_before_incident_start <= 2
    decoy = real_sim.get_recent_deployments(GetDeploymentsArgs(incident_id="INC-2001", hours=6))
    assert [d.id for d in decoy.deployments] == ["deploy-2301"]  # an unrelated release hours earlier
    assert decoy.deployments[0].minutes_before_incident_start > 60
    none_recent = real_sim.get_recent_deployments(GetDeploymentsArgs(incident_id="INC-2003", hours=6))
    assert none_recent.deployments == [] and none_recent.reference_ids  # "no deployments" is citable evidence
    assert real_sim.get_recent_deployments(GetDeploymentsArgs(incident_id="INC-2003", hours=24)).deployments


def test_verification_reports_what_was_measured_after_the_lab_undid_the_fault(real_sim: Simulator) -> None:
    expected = {"INC-2001": "resolved", "INC-2002": "resolved", "INC-2003": "not_resolved"}
    for iid in IDS:
        assert real_sim.verify_resolution(iid).status == "not_resolved", "failing before any action"
        real_sim.execute_remediation(iid, ACTION[iid])
        after = real_sim.verify_resolution(iid)
        assert after.status == expected[iid], (iid, after.checks)


@pytest.mark.parametrize("iid", IDS)
def test_the_agent_stays_grounded_and_inside_the_allowlist_on_captured_data(real_client: TestClient, iid: str) -> None:
    started = real_client.post(f"/api/incidents/{iid}/investigations")
    inv = real_client.get(f"/api/investigations/{started.json()['id']}").json()
    assert inv["status"] == "completed"
    retrieved = {ref for c in inv["tool_calls"] if c["status"] == "ok" for ref in c["reference_ids"]}
    assert {e["reference_id"] for e in inv["findings"]["evidence"]} <= retrieved
    rem = inv["findings"]["remediation"]
    if rem:
        assert rem["action_id"] in REMEDIATIONS
        assert (
            real_client.get(f"/api/incidents/{iid}").json()["service"]
            in REMEDIATIONS[rem["action_id"]].applicable_services
        )


# ---- the file is validated on load ----------------------------------------------------------------


def _mutated(tmp_path: Path, change: Any) -> Path:
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    change(data)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["incidents"][0].update(unexpected=1),
        lambda d: d["incidents"].append(d["incidents"][0]),
        lambda d: d["incidents"][0]["logs"][0].update(level="FATAL"),
        lambda d: d["incidents"][0]["metrics"].update(not_a_metric={"unit": "x", "points": []}),
        lambda d: d["incidents"][0]["incident"].update(id="BAD-1"),
        lambda d: d.pop("meta"),
    ],
)
def test_a_malformed_dataset_stops_startup(tmp_path: Path, change: Any) -> None:
    with pytest.raises(ConfigError):
        CapturedSource.load(_mutated(tmp_path, change))


def test_missing_and_non_json_datasets_stop_startup(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        CapturedSource.load(tmp_path / "nope.json")
    junk = tmp_path / "junk.json"
    junk.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        CapturedSource.load(junk)


def test_settings_select_the_dataset_and_a_bad_path_fails_at_startup() -> None:
    base = dataclasses.replace(Settings(), storage_mode="memory")
    app = create_app(settings=dataclasses.replace(base, telemetry_dataset=str(DATASET)))
    assert TestClient(app).get("/api/config").json()["data_mode"] == "captured"
    with pytest.raises(ConfigError):
        create_app(settings=dataclasses.replace(base, telemetry_dataset="does-not-exist.json"))


# ---- search behaviour that real data exposed ------------------------------------------------------


def test_a_level_name_matches_a_search_even_when_the_message_never_says_it(real_sim: Simulator) -> None:
    """Real log lines say "upstream timeout...", not "error": the level field is where that word lives."""
    by_message = real_sim.search_logs(SearchLogsArgs(incident_id="INC-2001", query="timeout", limit=50))
    by_level = real_sim.search_logs(SearchLogsArgs(incident_id="INC-2001", query="error", limit=50))
    assert by_level.total_matches >= by_message.total_matches > 0
    assert all("error" not in e.message.lower() for e in by_level.entries)
    assert {e.level for e in by_level.entries} == {"ERROR"}


def test_an_empty_search_returns_a_citable_absence_id(real_sim: Simulator) -> None:
    empty = real_sim.search_logs(SearchLogsArgs(incident_id="INC-2002", query="Out of Memory!", limit=5))
    assert empty.total_matches == 0 and empty.reference_ids == ["logs:catalog-api:no-match:out-of-memory"]
