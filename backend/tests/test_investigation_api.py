"""Investigation endpoints, store behaviour, and configuration bounds."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.models import ProviderInfo
from app.agent.store import MAX_RECORDS, InMemoryInvestigationStore
from app.config import ConfigError, Settings
from app.errors import InvestigationInProgressError, InvestigationNotFoundError
from app.identity import ANONYMOUS_OWNER as OWNER
from app.main import create_app
from app.simulator.service import Simulator
from tests.scripted_provider import ScriptedProvider, final

APP_DIR = Path(__file__).resolve().parent.parent / "app"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
INFO = ProviderInfo(kind="mock", label="test")


def start(client: TestClient, iid: str = "INC-1001") -> dict[str, Any]:
    r = client.post(f"/api/incidents/{iid}/investigations")
    assert r.status_code == 202, r.text
    body: dict[str, Any] = r.json()
    return body


# ---- endpoints ---------------------------------------------------------------------------------


def test_start_returns_202_running_then_the_polled_record_is_complete(client: TestClient) -> None:
    started = start(client)
    assert started["status"] == "running" and started["id"] == "inv-0001" and started["tool_calls"] == []
    done = client.get(f"/api/investigations/{started['id']}").json()
    assert done["status"] == "completed" and done["failure"] is None
    assert done["findings"]["confidence"] == "high"
    assert done["findings"]["remediation"]["requires_approval"] is True
    assert {c["name"] for c in done["tool_calls"]} >= {"get_incident", "search_logs", "get_service_metrics"}
    assert all({"id", "arguments", "duration_ms", "result_summary", "at"} <= set(c) for c in done["tool_calls"])


def test_response_labels_the_provider_and_the_data_as_simulated(client: TestClient) -> None:
    start(client)
    body = client.get("/api/incidents/INC-1001/investigations/latest").json()
    assert body["simulated"] is True
    assert body["provider"] == {"kind": "mock", "label": "Mock provider: deterministic rule-based policy, not an LLM"}


def test_latest_returns_the_newest_investigation_and_reruns_are_allowed_after_completion(client: TestClient) -> None:
    first, second = start(client), start(client)
    assert (first["id"], second["id"]) == ("inv-0001", "inv-0002")
    assert client.get("/api/incidents/INC-1001/investigations/latest").json()["id"] == "inv-0002"
    assert start(client, "INC-1002")["id"] == "inv-0003"
    assert client.get("/api/incidents/INC-1001/investigations/latest").json()["id"] == "inv-0002"


def test_latest_is_404_before_any_investigation(client: TestClient) -> None:
    r = client.get("/api/incidents/INC-1001/investigations/latest")
    assert r.status_code == 404 and r.json()["error"]["code"] == "investigation_not_found"


def test_unknown_or_malformed_ids_are_rejected(client: TestClient) -> None:
    assert client.post("/api/incidents/INC-9999/investigations").status_code == 404
    assert client.get("/api/incidents/INC-9999/investigations/latest").status_code == 404
    assert client.post("/api/incidents/bogus/investigations").status_code == 422
    assert client.get("/api/investigations/inv-9999").status_code == 404
    assert client.get("/api/investigations/not-an-id").status_code == 422
    assert client.get("/api/investigations/inv-0001' OR 1=1").status_code == 422


def test_a_second_start_while_one_is_running_is_a_409(sim: Simulator) -> None:
    app = create_app(simulator=sim)
    client = TestClient(app)
    app.state.investigations.start("INC-1001")  # started but not run: stays 'running'
    r = client.post("/api/incidents/INC-1001/investigations")
    assert r.status_code == 409 and r.json()["error"]["code"] == "investigation_in_progress"
    assert client.post("/api/incidents/INC-1002/investigations").status_code == 202  # other incidents unaffected


def test_start_accepts_no_body_and_ignores_none_supplied(client: TestClient) -> None:
    r = client.post("/api/incidents/INC-1001/investigations", json={"remediation": "simulate_restart_worker"})
    assert r.status_code == 202  # the body is not read: a client cannot steer the investigation


def test_provider_failure_is_a_failed_record_with_a_generic_message(sim: Simulator) -> None:
    from app.errors import ProviderError

    client = TestClient(create_app(simulator=sim, provider=ScriptedProvider(ProviderError("sk-SECRET boom"))))
    started = start(client)
    body = client.get(f"/api/investigations/{started['id']}").json()
    assert body["status"] == "failed" and body["failure"]["code"] == "provider_error"
    assert body["findings"] is None and "sk-SECRET" not in str(body)


def test_malformed_model_output_never_reaches_the_api_as_findings(sim: Simulator) -> None:
    client = TestClient(create_app(simulator=sim, provider=ScriptedProvider(final("{not json"))))
    body = client.get(f"/api/investigations/{start(client)['id']}").json()
    assert body["status"] == "failed" and body["failure"]["code"] == "invalid_output" and body["findings"] is None


def test_a_crash_inside_the_run_ends_failed_not_stuck_running(sim: Simulator, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(simulator=sim)

    def boom(*_: object, **__: object) -> None:
        raise RuntimeError("secret-token-abc123")

    monkeypatch.setattr(app.state.investigations._coordinator, "run", boom)
    client = TestClient(app)
    body = client.get(f"/api/investigations/{start(client)['id']}").json()
    assert body["status"] == "failed" and body["failure"]["code"] == "internal_error"
    assert "secret-token" not in str(body)
    assert start(client)["id"] == "inv-0002"  # not blocked by a stuck 'running' record


def test_answer_key_never_appears_in_investigation_responses(client: TestClient) -> None:
    for iid in ("INC-1004", "INC-1005"):
        start(client, iid)
        text = client.get(f"/api/incidents/{iid}/investigations/latest").text
        leaked = ("root_cause_category", "expected_log_ids", "expected_metric_ref", "expected_deployment_ref")
        assert not any(key in text for key in leaked) and "expected_remediation" not in text
        assert "ground_truth" not in text


def test_openapi_documents_the_new_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "post" in paths["/api/incidents/{incident_id}/investigations"]
    assert "get" in paths["/api/investigations/{investigation_id}"]


def test_scripted_provider_is_test_only() -> None:
    offenders = [p.name for p in APP_DIR.rglob("*.py") if "scripted" in p.read_text(encoding="utf-8").lower()]
    assert offenders == []


# ---- store ------------------------------------------------------------------------------------


def test_store_allows_one_running_investigation_per_incident() -> None:
    store = InMemoryInvestigationStore()
    first = store.create(OWNER, "INC-1001", INFO, NOW)
    with pytest.raises(InvestigationInProgressError):
        store.create(OWNER, "INC-1001", INFO, NOW)
    store.create(OWNER, "INC-1002", INFO, NOW)
    store.save(OWNER, first.model_copy(update={"status": "completed"}))
    assert store.create(OWNER, "INC-1001", INFO, NOW).id == "inv-0003"


def test_store_unknown_id_and_save_of_unknown_record() -> None:
    store = InMemoryInvestigationStore()
    with pytest.raises(InvestigationNotFoundError):
        store.get(OWNER, "inv-0001")
    ghost = InMemoryInvestigationStore().create(OWNER, "INC-1001", INFO, NOW)
    store.save(OWNER, ghost)  # saving a record the store never created must not resurrect it
    with pytest.raises(InvestigationNotFoundError):
        store.get(OWNER, ghost.id)


def test_store_memory_is_bounded_and_never_evicts_a_running_record() -> None:
    store = InMemoryInvestigationStore()
    running = store.create(OWNER, "INC-0001", INFO, NOW)
    for n in range(MAX_RECORDS + 20):
        rec = store.create(OWNER, f"INC-{n + 2:04d}", INFO, NOW)
        store.save(OWNER, rec.model_copy(update={"status": "completed"}))
    assert len(store._rows) <= MAX_RECORDS  # noqa: SLF001
    assert store.get(OWNER, running.id).status == "running"


# ---- configuration ----------------------------------------------------------------------------


def test_defaults_match_the_documented_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AGENT_PROVIDER", "AGENT_MAX_ROUNDS", "AGENT_MAX_TOTAL_TOKENS", "TOOL_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    s = Settings.from_env()
    assert (s.agent_provider, s.agent_max_rounds, s.agent_max_total_tokens) == ("mock", 6, 20_000)
    assert (s.max_output_tokens, s.tool_timeout_seconds, s.tool_result_max_bytes) == (1_500, 5, 16_384)


def test_bounds_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MAX_ROUNDS", "3")
    monkeypatch.setenv("AGENT_MAX_TOTAL_TOKENS", "999")
    s = Settings.from_env()
    assert (s.agent_max_rounds, s.agent_max_total_tokens) == (3, 999)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AGENT_MAX_ROUNDS", "0"),
        ("AGENT_MAX_ROUNDS", "9999"),
        ("AGENT_MAX_ROUNDS", "six"),
        ("TOOL_TIMEOUT_SECONDS", "-1"),
    ],
)
def test_invalid_bounds_fail_at_startup(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        Settings.from_env()


@pytest.mark.parametrize("provider", ["openai", "OpenAI", "bedrock", "gpt"])
def test_only_the_mock_provider_exists_until_phase_3b(monkeypatch: pytest.MonkeyPatch, provider: str) -> None:
    monkeypatch.setenv("AGENT_PROVIDER", provider)
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_the_api_key_is_read_in_exactly_one_place() -> None:
    offenders = [p.name for p in APP_DIR.rglob("*.py") if "OPENAI_API_KEY" in p.read_text(encoding="utf-8")]
    assert offenders == ["factory.py"]
