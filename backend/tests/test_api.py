import json
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from app.repository import InMemoryIncidentRepository
from app.schemas import SearchLogsArgs
from app.simulator.service import Simulator


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "data_mode": "simulated", "version": "0.1.0"}


def test_openapi_and_docs_are_served(client: TestClient) -> None:
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    paths = spec.json()["paths"]
    assert "/api/incidents" in paths
    assert "/api/incidents/{incident_id}/logs" in paths
    assert "simulated" in spec.json()["info"]["description"].lower()
    assert client.get("/docs").status_code == 200


def test_list_incidents_returns_summaries(client: TestClient) -> None:
    r = client.get("/api/incidents")
    assert r.status_code == 200
    items: list[dict[str, Any]] = r.json()
    assert [i["id"] for i in items] == ["INC-1001", "INC-1002", "INC-1003", "INC-1004"]
    assert all("history" not in i and "signals" not in i for i in items)
    assert items[0]["started_at"] == "2026-10-28T09:12:00Z"


def test_incident_detail(client: TestClient) -> None:
    r = client.get("/api/incidents/INC-1001")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "checkout-api"
    assert body["signals"] and body["history"]


def test_unknown_incident_is_404_with_error_envelope(client: TestClient) -> None:
    r = client.get("/api/incidents/INC-9999")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "incident_not_found", "message": "Incident not found."}}


def test_malformed_id_is_422_and_input_is_not_echoed(client: TestClient) -> None:
    r = client.get("/api/incidents/bad-id-%3Cscript%3Ealert(1)")
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "invalid_request"
    assert "script" not in json.dumps(body)


def test_logs_endpoint(client: TestClient) -> None:
    r = client.get("/api/incidents/INC-1001/logs", params={"query": "timeout", "limit": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["returned"] == 3 and body["truncated"] is True
    assert body["reference_ids"][0] == "log-104"


def test_logs_endpoint_rejects_bad_queries(client: TestClient) -> None:
    assert client.get("/api/incidents/INC-1001/logs").status_code == 422  # query required
    assert client.get("/api/incidents/INC-1001/logs", params={"query": "x", "limit": 999}).status_code == 422
    assert client.get("/api/incidents/INC-1001/logs", params={"query": "x", "level": "TRACE"}).status_code == 422
    assert client.get("/api/incidents/INC-1001/logs", params={"query": "x", "foo": "1"}).status_code == 422


def test_metrics_endpoint(client: TestClient) -> None:
    r = client.get("/api/incidents/INC-1001/metrics", params={"metric": "error_rate"})
    assert r.status_code == 200
    body = r.json()
    assert body["reference_ids"] == ["metric:error_rate:08:57-09:42"]
    assert body["summary"]["elevated_fraction"] > 0.6


def test_metrics_endpoint_errors(client: TestClient) -> None:
    assert client.get("/api/incidents/INC-1001/metrics", params={"metric": "cpu"}).status_code == 422
    missing = client.get("/api/incidents/INC-1001/metrics", params={"metric": "queue_depth"})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "metric_not_available"


def test_deployments_endpoint(client: TestClient) -> None:
    r = client.get("/api/incidents/INC-1002/deployments", params={"hours": 6})
    assert r.status_code == 200
    assert "deploy-419" in r.json()["reference_ids"]
    assert client.get("/api/incidents/INC-1002/deployments", params={"hours": 25}).status_code == 422


def test_no_remediation_or_write_routes_are_exposed(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path, ops in paths.items():
        assert set(ops) <= {"get"}, f"unexpected write method on {path}"
        assert "remediat" not in path and "approve" not in path
    assert client.post("/api/incidents/INC-1001/remediation", json={}).status_code in (404, 405)


def test_answer_key_never_appears_in_responses(client: TestClient) -> None:
    urls = [
        "/api/incidents",
        "/api/incidents/INC-1004",
        "/api/incidents/INC-1004/logs?query=a&level=WARN",
        "/api/incidents/INC-1004/metrics?metric=p99_latency_ms",
        "/api/incidents/INC-1004/deployments",
    ]
    for u in urls:
        text = client.get(u).text
        assert "root_cause_category" not in text and "expected_" not in text and "ground_truth" not in text


def test_cors_allows_only_configured_origin(client: TestClient) -> None:
    ok = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    bad = client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in bad.headers


def test_unhandled_errors_return_generic_500_without_leaking_details() -> None:
    class Boom(Simulator):
        def search_logs(self, args: SearchLogsArgs) -> Any:
            raise RuntimeError("secret-token-abc123")

    app = create_app(simulator=Boom(InMemoryIncidentRepository()))
    r = TestClient(app, raise_server_exceptions=False).get("/api/incidents/INC-1001/logs", params={"query": "x"})
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "internal_error", "message": "Internal server error."}}
    assert "secret-token" not in r.text
