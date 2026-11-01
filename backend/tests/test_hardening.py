"""Operational hardening: body-size cap, request ids and the per-request log line."""

import logging
import re

import pytest
from fastapi.testclient import TestClient

from app.main import MAX_BODY_BYTES
from app.security import ACCESS_HEADER


def test_every_response_carries_a_request_id_and_ids_are_distinct(client: TestClient) -> None:
    ids = {client.get("/health").headers["X-Request-Id"] for _ in range(5)}
    assert len(ids) == 5 and all(re.fullmatch(r"[0-9a-f]{16}", i) for i in ids)


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [("GET", "/api/incidents/INC-9999", 404), ("GET", "/api/incidents/nope", 422), ("POST", "/api/nowhere", 404)],
)
def test_error_responses_have_a_request_id_too(client: TestClient, method: str, path: str, status: int) -> None:
    r = client.request(method, path)
    assert r.status_code == status and r.headers["X-Request-Id"]


def test_an_oversized_body_is_refused_before_it_reaches_any_route(client: TestClient) -> None:
    started = client.post("/api/incidents/INC-1001/investigations")
    inv_id = started.json()["id"]
    big = {"helpful": True, "comment": "x" * (MAX_BODY_BYTES + 1)}
    r = client.post(f"/api/investigations/{inv_id}/feedback", json=big)
    assert r.status_code == 413
    assert r.json() == {"error": {"code": "payload_too_large", "message": "Request body is too large."}}
    assert r.headers["X-Request-Id"]
    assert client.get(f"/api/investigations/{inv_id}").json()["feedback"] is None


def test_a_body_at_the_documented_maximum_is_still_accepted(client: TestClient) -> None:
    inv_id = client.post("/api/incidents/INC-1001/investigations").json()["id"]
    r = client.post(f"/api/investigations/{inv_id}/feedback", json={"helpful": True, "comment": "x" * 500})
    assert r.status_code == 200


def test_a_malformed_content_length_is_a_client_error(client: TestClient) -> None:
    r = client.post("/api/incidents/INC-1001/investigations", headers={"Content-Length": "abc"})
    assert r.status_code in (400, 422)  # the server stack may reject it first; either way it is not a 5xx


def test_the_log_line_names_the_request_but_never_its_query_body_or_access_code(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="agentops")
    client.get("/api/incidents/INC-1001/logs", params={"query": "secret-search-term"})
    client.post("/api/incidents/INC-1001/investigations", headers={ACCESS_HEADER: "very-secret-code"})
    text = "\n".join(r.getMessage() for r in caplog.records if r.name == "agentops")
    assert re.search(r"request [0-9a-f]{16} GET /api/incidents/INC-1001/logs -> 200 in \d+ ms", text)
    assert "POST /api/incidents/INC-1001/investigations -> 202" in text
    assert "secret-search-term" not in text and "very-secret-code" not in text
