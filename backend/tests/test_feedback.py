"""Feedback on a finished investigation: stored with the record, validated, gated, and never sent to a model."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.agent.models import Investigation
from app.config import Settings
from app.main import create_app
from app.security import ACCESS_HEADER, AccessGate
from app.simulator.service import Simulator


def _finished(client: TestClient, incident_id: str = "INC-1001") -> str:
    started = client.post(f"/api/incidents/{incident_id}/investigations")
    assert started.status_code == 202
    inv_id: str = started.json()["id"]
    assert client.get(f"/api/investigations/{inv_id}").json()["status"] == "completed"
    return inv_id


def test_a_verdict_and_note_are_stored_and_served_with_the_investigation(client: TestClient) -> None:
    inv_id = _finished(client)
    r = client.post(f"/api/investigations/{inv_id}/feedback", json={"helpful": True, "comment": "  spot on  "})
    assert r.status_code == 200
    assert r.json()["feedback"]["helpful"] is True and r.json()["feedback"]["comment"] == "spot on"
    again = client.get(f"/api/investigations/{inv_id}").json()
    assert again["feedback"]["comment"] == "spot on"
    assert client.get("/api/incidents/INC-1001/investigations/latest").json()["feedback"]["helpful"] is True


def test_there_is_no_feedback_until_someone_gives_it(client: TestClient) -> None:
    assert client.get(f"/api/investigations/{_finished(client)}").json()["feedback"] is None


def test_a_later_verdict_replaces_the_earlier_one_and_an_empty_note_is_none(client: TestClient) -> None:
    inv_id = _finished(client)
    client.post(f"/api/investigations/{inv_id}/feedback", json={"helpful": True, "comment": "good"})
    r = client.post(f"/api/investigations/{inv_id}/feedback", json={"helpful": False, "comment": "   "})
    assert r.json()["feedback"]["helpful"] is False and r.json()["feedback"]["comment"] is None


def test_feedback_does_not_touch_findings_or_the_approval_state(client: TestClient) -> None:
    inv_id = _finished(client)
    before = Investigation.model_validate(client.get(f"/api/investigations/{inv_id}").json())
    client.post(f"/api/investigations/{inv_id}/feedback", json={"helpful": False})
    after = Investigation.model_validate(client.get(f"/api/investigations/{inv_id}").json())
    assert after.model_copy(update={"feedback": None}) == before
    assert after.remediation_state is not None and after.remediation_state.status == "proposed"


def test_feedback_is_allowed_on_a_failed_run_too(client: TestClient) -> None:
    # INC-1004 completes with no remediation; feedback must not depend on an action being proposed.
    inv_id = _finished(client, "INC-1004")
    assert client.post(f"/api/investigations/{inv_id}/feedback", json={"helpful": False}).status_code == 200


def test_bad_bodies_are_rejected_without_echoing_input(client: TestClient) -> None:
    inv_id = _finished(client)
    url = f"/api/investigations/{inv_id}/feedback"
    for body in (
        {},
        {"helpful": "maybe"},
        {"helpful": True, "comment": "x" * 501},
        {"helpful": True, "extra": "field"},
        {"helpful": True, "action_id": "simulate_restart_worker"},
    ):
        r = client.post(url, json=body)
        assert r.status_code == 422, body
        assert "maybe" not in r.text and "simulate_restart_worker" not in r.text
    assert client.get(f"/api/investigations/{inv_id}").json()["feedback"] is None


def test_unknown_and_malformed_ids_are_not_found_or_rejected(client: TestClient) -> None:
    assert client.post("/api/investigations/inv-9999/feedback", json={"helpful": True}).status_code == 404
    assert client.post("/api/investigations/not-an-id/feedback", json={"helpful": True}).status_code == 422


def test_a_running_investigation_cannot_be_rated(client: TestClient, sim: Simulator) -> None:
    app = create_app(simulator=sim)
    svc = app.state.investigations
    running = svc.start("INC-1001")  # recorded as running; the background task is not run here
    r = TestClient(app).post(f"/api/investigations/{running.id}/feedback", json={"helpful": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "feedback_not_allowed"


def test_feedback_needs_the_access_code_when_one_is_configured(sim: Simulator) -> None:
    code = "demo-code-123"
    app = create_app(simulator=sim, access=AccessGate(code), settings=Settings(storage_mode="memory"))
    c = TestClient(app)
    inv_id = c.post("/api/incidents/INC-1001/investigations", headers={ACCESS_HEADER: code}).json()["id"]
    url = f"/api/investigations/{inv_id}/feedback"
    assert c.post(url, json={"helpful": True}).status_code == 401
    assert c.post(url, json={"helpful": True}, headers={ACCESS_HEADER: "wrong-code-1"}).status_code == 401
    assert c.post(url, json={"helpful": True}, headers={ACCESS_HEADER: code}).status_code == 200


def test_feedback_survives_a_restart_on_the_sqlite_store(tmp_path: Path) -> None:
    import dataclasses

    settings = dataclasses.replace(Settings(), storage_mode="local", local_data_dir=str(tmp_path))
    first = TestClient(create_app(settings=settings))
    inv_id = first.post("/api/incidents/INC-1001/investigations").json()["id"]
    first.post(f"/api/investigations/{inv_id}/feedback", json={"helpful": False, "comment": "missed the deploy"})

    second = TestClient(create_app(settings=settings))  # a new process over the same file
    saved = second.get(f"/api/investigations/{inv_id}").json()["feedback"]
    assert saved["helpful"] is False and saved["comment"] == "missed the deploy"
