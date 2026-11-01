"""Sign-in: who may write, and the guarantee that one user's investigations never touch another's."""

import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.simulator.service import Simulator
from tests.auth_helpers import USER_A, USER_B, FakePool, bearer


@pytest.fixture(scope="module")
def pool() -> FakePool:
    return FakePool()


@pytest.fixture
def app_client(sim: Simulator, pool: FakePool) -> TestClient:
    pool.fail = False
    return TestClient(create_app(simulator=sim, settings=Settings(storage_mode="memory"), verifier=pool.verifier()))


def _headers(pool: FakePool, sub: str) -> dict[str, str]:
    return bearer(pool.token(sub))


def _investigate(c: TestClient, pool: FakePool, sub: str, incident: str = "INC-1001") -> dict[str, object]:
    r = c.post(f"/api/incidents/{incident}/investigations", headers=_headers(pool, sub))
    assert r.status_code == 202, r.text
    body: dict[str, object] = c.get(f"/api/investigations/{r.json()['id']}", headers=_headers(pool, sub)).json()
    return body


# ---- who may write --------------------------------------------------------------------------


def test_the_config_says_sign_in_is_on(app_client: TestClient) -> None:
    assert app_client.get("/api/config").json()["auth"] == {"mode": "cognito"}


def test_every_write_needs_a_signed_in_user(app_client: TestClient, pool: FakePool) -> None:
    inv = _investigate(app_client, pool, USER_A)["id"]
    writes = [
        ("/api/incidents/INC-1001/investigations", None),
        (f"/api/investigations/{inv}/remediation/approve", None),
        (f"/api/investigations/{inv}/remediation/reject", None),
        (f"/api/investigations/{inv}/feedback", {"helpful": True}),
    ]
    for url, body in writes:
        r = app_client.post(url, json=body) if body else app_client.post(url)
        assert r.status_code == 401, url
        assert r.json()["error"]["code"] == "sign_in_required"
        assert r.headers["www-authenticate"] == "Bearer"


def test_a_visitor_who_is_not_signed_in_can_still_read_the_shared_incidents(app_client: TestClient) -> None:
    assert app_client.get("/api/incidents").status_code == 200
    assert app_client.get("/api/incidents/INC-1001").status_code == 200
    assert app_client.get("/api/incidents/INC-1001/logs", params={"query": "timeout"}).status_code == 200
    assert app_client.get("/api/incidents/INC-1001/investigations/latest").status_code == 404
    assert app_client.get("/api/incidents/INC-1001/timeline").status_code == 200


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_token_that_is_present_but_bad_is_refused_not_downgraded(
    app_client: TestClient, pool: FakePool, method: str
) -> None:
    expired = pool.token(USER_A, exp=int(time.time()) - 600)
    url = "/api/incidents" if method == "get" else "/api/incidents/INC-1001/investigations"
    for header in (bearer(expired), {"Authorization": "Bearer not-a-token"}, {"Authorization": "Basic abc"},
                   {"Authorization": "Bearer "}):  # fmt: skip
        r = getattr(app_client, method)(url, headers=header)
        assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_token", header
        assert expired not in r.text


def test_an_unreachable_key_service_is_a_503_not_a_login_failure(sim: Simulator, pool: FakePool) -> None:
    pool.fail = True
    try:
        c = TestClient(create_app(simulator=sim, settings=Settings(storage_mode="memory"), verifier=pool.verifier()))
        r = c.post("/api/incidents/INC-1001/investigations", headers=_headers(pool, USER_A))
        assert r.status_code == 503 and r.json()["error"]["code"] == "auth_unavailable"
    finally:
        pool.fail = False


def test_when_sign_in_is_off_the_header_is_ignored_and_everyone_shares_one_owner(client: TestClient) -> None:
    assert client.get("/api/config").json()["auth"] == {"mode": "none"}
    r = client.post("/api/incidents/INC-1001/investigations", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 202
    assert client.get("/api/incidents/INC-1001/investigations/latest").status_code == 200


# ---- isolation ------------------------------------------------------------------------------


def test_a_user_sees_only_their_own_investigation(app_client: TestClient, pool: FakePool) -> None:
    a = _investigate(app_client, pool, USER_A)
    mine = app_client.get("/api/incidents/INC-1001/investigations/latest", headers=_headers(pool, USER_A))
    assert mine.status_code == 200 and mine.json()["id"] == a["id"]

    theirs = app_client.get("/api/incidents/INC-1001/investigations/latest", headers=_headers(pool, USER_B))
    assert theirs.status_code == 404
    assert app_client.get(f"/api/investigations/{a['id']}", headers=_headers(pool, USER_B)).status_code == 404
    assert app_client.get(f"/api/investigations/{a['id']}").status_code == 404  # signed out


def test_a_missing_record_and_someone_elses_record_look_identical(app_client: TestClient, pool: FakePool) -> None:
    a = _investigate(app_client, pool, USER_A)
    other = app_client.get(f"/api/investigations/{a['id']}", headers=_headers(pool, USER_B))
    absent = app_client.get("/api/investigations/inv-9999", headers=_headers(pool, USER_B))
    assert other.status_code == absent.status_code == 404
    assert other.json() == absent.json()


def test_another_user_cannot_approve_reject_or_rate_a_record_that_is_not_theirs(
    app_client: TestClient, pool: FakePool
) -> None:
    a = _investigate(app_client, pool, USER_A)
    hb = _headers(pool, USER_B)
    for url, body in (
        (f"/api/investigations/{a['id']}/remediation/approve", None),
        (f"/api/investigations/{a['id']}/remediation/reject", None),
        (f"/api/investigations/{a['id']}/feedback", {"helpful": False, "comment": "vandalism"}),
    ):
        r = app_client.post(url, headers=hb, json=body) if body else app_client.post(url, headers=hb)
        assert r.status_code == 404, url
    untouched = app_client.get(f"/api/investigations/{a['id']}", headers=_headers(pool, USER_A)).json()
    assert untouched["feedback"] is None and untouched["remediation_state"]["status"] == "proposed"


def test_two_users_can_investigate_the_same_incident_at_once(sim: Simulator, pool: FakePool) -> None:
    app = create_app(simulator=sim, settings=Settings(storage_mode="memory"), verifier=pool.verifier())
    svc = app.state.investigations
    svc.scoped(USER_A).start("INC-1001")  # recorded as running, never finished
    c = TestClient(app)
    r = c.post("/api/incidents/INC-1001/investigations", headers=_headers(pool, USER_B))
    assert r.status_code == 202  # not a 409: A's run does not block B
    again = c.post("/api/incidents/INC-1001/investigations", headers=_headers(pool, USER_A))
    assert again.status_code == 409  # but A cannot start a second one of their own


def test_one_users_approval_does_not_resolve_the_incident_for_another(app_client: TestClient, pool: FakePool) -> None:
    a = _investigate(app_client, pool, USER_A)
    approved = app_client.post(f"/api/investigations/{a['id']}/remediation/approve", headers=_headers(pool, USER_A))
    assert approved.status_code == 200 and approved.json()["remediation_state"]["status"] == "resolved"
    assert app_client.get("/api/incidents/INC-1001", headers=_headers(pool, USER_A)).json()["status"] == "resolved"

    # B sees the incident exactly as it was before A did anything...
    assert app_client.get("/api/incidents/INC-1001", headers=_headers(pool, USER_B)).json()["status"] != "resolved"
    listed = {i["id"]: i["status"] for i in app_client.get("/api/incidents", headers=_headers(pool, USER_B)).json()}
    assert listed["INC-1001"] != "resolved"
    assert app_client.get("/api/incidents/INC-1001").json()["status"] != "resolved"  # and so does a visitor

    # ...and B's own approval really runs the action (it is not reported as already applied by A).
    b = _investigate(app_client, pool, USER_B)
    assert b["remediation_state"]["status"] == "proposed"  # type: ignore[index]
    done = app_client.post(f"/api/investigations/{b['id']}/remediation/approve", headers=_headers(pool, USER_B)).json()
    assert done["remediation_state"]["action"]["status"] == "executed"
    assert done["remediation_state"]["status"] == "resolved"


def test_the_timeline_holds_only_the_callers_own_events(app_client: TestClient, pool: FakePool) -> None:
    a = _investigate(app_client, pool, USER_A)
    app_client.post(f"/api/investigations/{a['id']}/remediation/approve", headers=_headers(pool, USER_A))

    def titles(headers: dict[str, str]) -> list[str]:
        return [e["title"] for e in app_client.get("/api/incidents/INC-1001/timeline", headers=headers).json()]

    assert any("Remediation approved" in t for t in titles(_headers(pool, USER_A)))
    for other in (_headers(pool, USER_B), {}):
        assert not any(str(a["id"]) in t or "approved" in t for t in titles(other))


def test_feedback_stays_with_the_user_who_gave_it(app_client: TestClient, pool: FakePool) -> None:
    a = _investigate(app_client, pool, USER_A)
    b = _investigate(app_client, pool, USER_B)
    app_client.post(f"/api/investigations/{a['id']}/feedback", headers=_headers(pool, USER_A), json={"helpful": True})
    assert app_client.get(f"/api/investigations/{b['id']}", headers=_headers(pool, USER_B)).json()["feedback"] is None


def test_the_owner_comes_only_from_the_verified_token(app_client: TestClient, pool: FakePool) -> None:
    """A client cannot name an owner: no header, query or body field is read for it."""
    a = _investigate(app_client, pool, USER_A)
    spoofed = {**_headers(pool, USER_B), "X-Owner": USER_A, "X-User-Id": USER_A, "X-Amzn-Oidc-Identity": USER_A}
    r = app_client.get(f"/api/investigations/{a['id']}", headers=spoofed, params={"owner": USER_A, "sub": USER_A})
    assert r.status_code == 404


# ---- structural guard -----------------------------------------------------------------------


def test_every_write_route_needs_a_signed_in_owner() -> None:
    """A future POST route must take the `signed_in` dependency (the access-code guard is checked separately)."""
    from fastapi.routing import APIRoute

    from app.api import router, signed_in

    writes = [
        r
        for r in router.routes
        if isinstance(r, APIRoute) and (r.methods or set()) & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert writes
    for route in writes:
        assert any(d.call is signed_in for d in route.dependant.dependencies), route.path


def test_every_route_that_reads_investigations_is_scoped_to_the_caller() -> None:
    """Routes under /investigations, plus the incident views that mix in investigation state, take a caller."""
    from fastapi.routing import APIRoute

    from app.api import caller, router

    per_user = {
        "/api/incidents",
        "/api/incidents/{incident_id}",
        "/api/incidents/{incident_id}/timeline",
        "/api/incidents/{incident_id}/investigations/latest",
        "/api/investigations/{investigation_id}",
    }
    seen = set()
    for r in router.routes:
        if isinstance(r, APIRoute) and r.path in per_user and "GET" in (r.methods or set()):
            seen.add(r.path)

            def depends_on_caller(route: APIRoute = r) -> bool:
                stack = list(route.dependant.dependencies)
                while stack:
                    d = stack.pop()
                    if d.call is caller:
                        return True
                    stack.extend(d.dependencies)
                return False

            assert depends_on_caller(), r.path
    assert seen == per_user
