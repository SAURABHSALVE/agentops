"""Phase 5: remediation is unreachable without a recorded human approval, runs once, and
'action succeeded' is a separate state from 'verified resolved'."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.simulator.service import Simulator
from tests.scripted_provider import ScriptedProvider, final


def investigate(client: TestClient, iid: str) -> dict[str, Any]:
    started = client.post(f"/api/incidents/{iid}/investigations")
    assert started.status_code == 202
    body: dict[str, Any] = client.get(f"/api/investigations/{started.json()['id']}").json()
    return body


def approve(client: TestClient, inv_id: str) -> Any:
    return client.post(f"/api/investigations/{inv_id}/remediation/approve")


def reject(client: TestClient, inv_id: str) -> Any:
    return client.post(f"/api/investigations/{inv_id}/remediation/reject")


def test_a_completed_investigation_proposes_but_does_not_run_the_action(client: TestClient) -> None:
    inv = investigate(client, "INC-1001")
    rs = inv["remediation_state"]
    assert rs["status"] == "proposed" and rs["action_id"] == "simulate_clear_dependency_fault"
    assert rs["action"] is None and rs["verification"] is None and rs["decided_at"] is None
    assert client.get("/api/incidents/INC-1001").json()["status"] == "open"  # nothing changed


def test_approval_runs_the_action_then_verifies_and_the_incident_becomes_resolved(client: TestClient) -> None:
    inv = investigate(client, "INC-1001")
    done = approve(client, inv["id"])
    assert done.status_code == 200
    rs = done.json()["remediation_state"]
    assert rs["status"] == "resolved"
    assert rs["action"]["status"] == "executed" and rs["action"]["simulated"] is True
    assert rs["verification"]["status"] == "resolved"
    assert all(c["passed"] for c in rs["verification"]["checks"])
    assert rs["decided_at"] and rs["executed_at"] and rs["verified_at"]
    assert client.get("/api/incidents/INC-1001").json()["status"] == "resolved"
    listed = {i["id"]: i["status"] for i in client.get("/api/incidents").json()}
    assert listed["INC-1001"] == "resolved" and listed["INC-1003"] == "open"


def test_action_success_and_verified_resolution_are_separate_states(client: TestClient) -> None:
    """INC-1006: the restart works as an action, but the backlog stays high, so it is NOT resolved."""
    inv = investigate(client, "INC-1006")
    assert inv["remediation_state"]["action_id"] == "simulate_restart_worker"
    rs = approve(client, inv["id"]).json()["remediation_state"]
    assert rs["action"]["status"] == "executed"  # the action succeeded...
    assert rs["status"] == "not_resolved"  # ...the incident is not fixed
    assert rs["verification"]["status"] == "not_resolved"
    check = rs["verification"]["checks"][0]
    assert check["metric"] == "queue_depth" and check["passed"] is False and check["observed"] >= check["threshold"]
    assert client.get("/api/incidents/INC-1006").json()["status"] == "open"  # never shown as resolved


def test_duplicate_approvals_run_the_action_once(sim: Simulator) -> None:
    calls: list[str] = []
    real = sim.execute_remediation

    def counting(incident_id: str, action_id: str) -> Any:
        calls.append(action_id)
        return real(incident_id, action_id)

    sim.execute_remediation = counting  # type: ignore[method-assign]
    client = TestClient(create_app(simulator=sim))
    inv = investigate(client, "INC-1003")
    first, second, third = (approve(client, inv["id"]) for _ in range(3))
    assert first.status_code == second.status_code == third.status_code == 200
    assert first.json()["remediation_state"] == second.json()["remediation_state"]
    assert calls == ["simulate_restart_worker"]


def test_approval_needs_a_proposal_and_a_completed_investigation(client: TestClient, sim: Simulator) -> None:
    ambiguous = investigate(client, "INC-1004")
    assert ambiguous["remediation_state"] is None and ambiguous["findings"]["remediation"] is None
    r = approve(client, ambiguous["id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_remediation_state"
    assert reject(client, ambiguous["id"]).status_code == 409
    assert approve(client, "inv-9999").status_code == 404
    assert approve(client, "not-an-id").status_code == 422


def test_failed_investigations_cannot_be_approved(sim: Simulator) -> None:
    client = TestClient(create_app(simulator=sim, provider=ScriptedProvider(final("{bad"))))
    inv = investigate(client, "INC-1001")
    assert inv["status"] == "failed" and inv["remediation_state"] is None
    assert approve(client, inv["id"]).status_code == 409


def test_rejection_runs_nothing_and_blocks_a_later_approval(sim: Simulator) -> None:
    calls: list[str] = []
    sim.execute_remediation = lambda *a: calls.append("ran")  # type: ignore[method-assign,assignment]
    client = TestClient(create_app(simulator=sim))
    inv = investigate(client, "INC-1001")
    rejected = reject(client, inv["id"])
    assert rejected.status_code == 200 and rejected.json()["remediation_state"]["status"] == "rejected"
    assert reject(client, inv["id"]).json()["remediation_state"]["status"] == "rejected"  # idempotent
    after = approve(client, inv["id"])
    assert after.status_code == 409 and after.json()["error"]["code"] == "invalid_remediation_state"
    assert calls == []
    assert client.get("/api/incidents/INC-1001").json()["status"] == "open"


def test_an_approved_remediation_cannot_be_rejected(client: TestClient) -> None:
    inv = investigate(client, "INC-1001")
    approve(client, inv["id"])
    assert reject(client, inv["id"]).status_code == 409


def test_only_the_latest_investigation_can_be_approved(client: TestClient) -> None:
    old = investigate(client, "INC-1001")
    new = investigate(client, "INC-1001")
    stale = approve(client, old["id"])
    assert stale.status_code == 409 and "newer investigation" in stale.json()["error"]["message"]
    assert approve(client, new["id"]).status_code == 200


def test_the_client_cannot_choose_the_action(client: TestClient) -> None:
    inv = investigate(client, "INC-1001")
    r = client.post(
        f"/api/investigations/{inv['id']}/remediation/approve",
        json={"action_id": "simulate_restart_worker", "remediation": "rm -rf /"},
    )
    assert r.status_code == 200
    assert r.json()["remediation_state"]["action_id"] == "simulate_clear_dependency_fault"
    assert r.json()["remediation_state"]["action"]["action_id"] == "simulate_clear_dependency_fault"


def test_a_crash_while_executing_is_recorded_not_left_executing(sim: Simulator) -> None:
    def boom(*_: object) -> Any:
        raise RuntimeError("secret-token-abc123")

    sim.execute_remediation = boom  # type: ignore[method-assign,assignment]
    client = TestClient(create_app(simulator=sim))
    inv = investigate(client, "INC-1001")
    rs = approve(client, inv["id"]).json()["remediation_state"]
    assert rs["status"] == "action_failed" and rs["verification"] is None
    assert "secret-token" not in str(rs)
    assert client.get("/api/incidents/INC-1001").json()["status"] == "open"


@pytest.mark.parametrize("iid", ["INC-1001", "INC-1002", "INC-1003", "INC-1005"])
def test_every_clear_incident_resolves_after_approval(client: TestClient, iid: str) -> None:
    inv = investigate(client, iid)
    assert approve(client, inv["id"]).json()["remediation_state"]["status"] == "resolved"


# ---- timeline --------------------------------------------------------------------------------


def test_timeline_is_ordered_and_records_every_step_of_the_workflow(client: TestClient) -> None:
    inv = investigate(client, "INC-1006")
    approve(client, inv["id"])
    events = client.get("/api/incidents/INC-1006/timeline").json()
    times = [e["at"] for e in events]
    assert times == sorted(times)
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "incident"  # the alert from the fixture history comes first
    for expected in ("agent", "tool", "approval", "action", "verification"):
        assert expected in kinds, expected
    titles = " | ".join(e["title"] for e in events)
    assert "Findings ready" in titles and "Remediation approved" in titles
    assert "Verification FAILED" in titles and "resolved" not in events[-1]["title"].lower().replace("not resolved", "")
    assert len({e["id"] for e in events}) == len(events)


def test_timeline_includes_rejections_failures_and_reruns(client: TestClient, sim: Simulator) -> None:
    first = investigate(client, "INC-1001")
    reject(client, first["id"])
    investigate(client, "INC-1001")
    titles = [e["title"] for e in client.get("/api/incidents/INC-1001/timeline").json()]
    assert "Remediation rejected" in titles
    assert sum(t.startswith("Investigation inv-") and t.endswith("started") for t in titles) == 2


def test_timeline_for_unknown_incident_is_404(client: TestClient) -> None:
    assert client.get("/api/incidents/INC-9999/timeline").status_code == 404


def test_timeline_shows_failures_honestly(sim: Simulator) -> None:
    client = TestClient(create_app(simulator=sim, provider=ScriptedProvider(final("{bad"))))
    investigate(client, "INC-1001")
    events = client.get("/api/incidents/INC-1001/timeline").json()
    assert events[-1]["kind"] == "error" and events[-1]["title"] == "Investigation failed"
