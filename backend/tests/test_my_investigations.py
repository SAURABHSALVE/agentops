"""'My investigations': a signed-in user lists and deletes their own work, and only their own."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security import ACCESS_HEADER, AccessGate
from app.simulator.service import Simulator
from tests.auth_helpers import USER_A, USER_B, FakePool, bearer

CODE = "demo-code-1234"


@pytest.fixture(scope="module")
def pool() -> FakePool:
    return FakePool()


@pytest.fixture
def app_client(sim: Simulator, pool: FakePool) -> TestClient:
    return TestClient(create_app(simulator=sim, settings=Settings(storage_mode="memory"), verifier=pool.verifier()))


def _h(pool: FakePool, sub: str) -> dict[str, str]:
    return bearer(pool.token(sub))


def _investigate(c: TestClient, pool: FakePool, sub: str, incident: str) -> str:
    r = c.post(f"/api/incidents/{incident}/investigations", headers=_h(pool, sub))
    assert r.status_code == 202, r.text
    inv_id: str = r.json()["id"]
    return inv_id


def test_a_user_lists_their_own_investigations_newest_first_with_what_to_show(
    app_client: TestClient, pool: FakePool
) -> None:
    first = _investigate(app_client, pool, USER_A, "INC-1001")
    second = _investigate(app_client, pool, USER_A, "INC-1003")
    app_client.post(f"/api/investigations/{first}/remediation/approve", headers=_h(pool, USER_A))
    app_client.post(f"/api/investigations/{first}/feedback", headers=_h(pool, USER_A), json={"helpful": True})

    rows = app_client.get("/api/me/investigations", headers=_h(pool, USER_A)).json()
    assert [r["id"] for r in rows] == [second, first]  # newest first
    newest, oldest = rows
    assert newest["incident_id"] == "INC-1003" and newest["incident_title"] and newest["status"] == "completed"
    assert newest["confidence"] in ("low", "medium", "high") and newest["remediation_status"] == "proposed"
    assert newest["helpful"] is None
    assert oldest["remediation_status"] == "resolved" and oldest["helpful"] is True
    assert set(oldest) == {
        "id",
        "incident_id",
        "incident_title",
        "status",
        "started_at",
        "confidence",
        "remediation_status",
        "helpful",
    }  # a summary: no findings, no tool calls, no comment text


def test_only_the_newest_run_of_each_incident_is_listed(app_client: TestClient, pool: FakePool) -> None:
    _investigate(app_client, pool, USER_A, "INC-1001")
    again = _investigate(app_client, pool, USER_A, "INC-1001")
    rows = app_client.get("/api/me/investigations", headers=_h(pool, USER_A)).json()
    assert [r["id"] for r in rows] == [again]


def test_nobody_sees_anyone_elses_list(app_client: TestClient, pool: FakePool) -> None:
    _investigate(app_client, pool, USER_A, "INC-1001")
    assert app_client.get("/api/me/investigations", headers=_h(pool, USER_B)).json() == []


def test_a_visitor_must_sign_in_to_list_or_delete(app_client: TestClient) -> None:
    for call in (app_client.get, app_client.delete):
        r = call("/api/me/investigations")
        assert r.status_code == 401 and r.json()["error"]["code"] == "sign_in_required"


def test_deleting_removes_only_the_callers_work(app_client: TestClient, pool: FakePool) -> None:
    mine = _investigate(app_client, pool, USER_A, "INC-1001")
    _investigate(app_client, pool, USER_A, "INC-1002")
    theirs = _investigate(app_client, pool, USER_B, "INC-1001")

    r = app_client.delete("/api/me/investigations", headers=_h(pool, USER_A))
    assert r.status_code == 200 and r.json() == {"deleted": 2}
    assert app_client.get("/api/me/investigations", headers=_h(pool, USER_A)).json() == []
    assert app_client.get(f"/api/investigations/{mine}", headers=_h(pool, USER_A)).status_code == 404
    assert app_client.get("/api/incidents/INC-1001/investigations/latest", headers=_h(pool, USER_A)).json() is None
    # B is untouched.
    assert app_client.get(f"/api/investigations/{theirs}", headers=_h(pool, USER_B)).status_code == 200
    assert len(app_client.get("/api/me/investigations", headers=_h(pool, USER_B)).json()) == 1
    # Deleting again is harmless.
    assert app_client.delete("/api/me/investigations", headers=_h(pool, USER_A)).json() == {"deleted": 0}


def test_deleting_also_forgets_the_simulated_fixes_that_were_applied(app_client: TestClient, pool: FakePool) -> None:
    first = _investigate(app_client, pool, USER_A, "INC-1001")
    done = app_client.post(f"/api/investigations/{first}/remediation/approve", headers=_h(pool, USER_A)).json()
    assert done["remediation_state"]["action"]["status"] == "executed"
    app_client.delete("/api/me/investigations", headers=_h(pool, USER_A))

    fresh = _investigate(app_client, pool, USER_A, "INC-1001")
    again = app_client.post(f"/api/investigations/{fresh}/remediation/approve", headers=_h(pool, USER_A)).json()
    assert again["remediation_state"]["action"]["status"] == "executed"  # not "already_applied": a clean slate


def test_a_running_investigation_blocks_deletion(sim: Simulator, pool: FakePool) -> None:
    app = create_app(simulator=sim, settings=Settings(storage_mode="memory"), verifier=pool.verifier())
    app.state.investigations.scoped(USER_A).start("INC-1001")  # recorded as running, never finished
    c = TestClient(app)
    r = c.delete("/api/me/investigations", headers=_h(pool, USER_A))
    assert r.status_code == 409 and r.json()["error"]["code"] == "investigation_in_progress"
    assert len(app.state.investigations.scoped(USER_A).mine()) == 1  # nothing was removed


def test_deleting_needs_the_access_code_when_one_is_configured(sim: Simulator, pool: FakePool) -> None:
    app = create_app(
        simulator=sim, access=AccessGate(CODE), settings=Settings(storage_mode="memory"), verifier=pool.verifier()
    )
    c = TestClient(app)
    assert c.delete("/api/me/investigations", headers=_h(pool, USER_A)).status_code == 401
    ok = c.delete("/api/me/investigations", headers={**_h(pool, USER_A), ACCESS_HEADER: CODE})
    assert ok.status_code == 200


def test_with_sign_in_off_everyone_shares_one_list(client: TestClient) -> None:
    client.post("/api/incidents/INC-1001/investigations")
    assert len(client.get("/api/me/investigations").json()) == 1
    assert client.delete("/api/me/investigations").json() == {"deleted": 1}
    assert client.get("/api/me/investigations").json() == []


def test_cors_allows_delete_only_from_the_configured_origin(app_client: TestClient) -> None:
    ok = app_client.options(
        "/api/me/investigations",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "DELETE"},
    )
    assert ok.status_code == 200 and "DELETE" in ok.headers["access-control-allow-methods"]
    bad = app_client.options(
        "/api/me/investigations",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "DELETE"},
    )
    assert "access-control-allow-origin" not in bad.headers
