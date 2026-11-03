"""Connecting an AWS account: who can use it, what each step does, and that one user's connection is theirs alone."""

import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings
from app.connections.models import ConnectionSettings, RoleRegistration
from app.connections.service import CONSENT_VERSION, ConnectionService
from app.connections.store import InMemoryConnectionStore
from app.errors import ConsentOutOfDateError
from app.main import create_app
from app.security import ACCESS_HEADER, AccessGate
from app.simulator.service import Simulator
from tests.auth_helpers import USER_A, USER_B, FakePool, bearer

ACCOUNT = "111122223333"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/AgentOpsReadOnly"
OTHER_ROLE = "arn:aws:iam::999988887777:role/AgentOpsReadOnly"
SETTINGS = {
    "region": "eu-west-1",
    "log_groups": ["/aws/lambda/checkout", "/ecs/payments"],
    "consent_version": CONSENT_VERSION,
    "accept_data_use": True,
}
URL = "/api/me/aws-connection"


@pytest.fixture(scope="module")
def pool() -> FakePool:
    return FakePool()


def make(sim: Simulator, pool: FakePool | None, **settings: Any) -> TestClient:
    return TestClient(
        create_app(
            simulator=sim,
            settings=Settings(storage_mode="memory", **settings),
            verifier=pool.verifier() if pool is not None else None,
        )
    )


@pytest.fixture
def on(sim: Simulator, pool: FakePool) -> TestClient:
    return make(sim, pool, aws_connect_account_id=ACCOUNT)


def h(pool: FakePool, sub: str = USER_A) -> dict[str, str]:
    return bearer(pool.token(sub))


# ---- when it is available ---------------------------------------------------------------------------


def test_it_is_off_unless_an_account_is_configured(sim: Simulator, pool: FakePool) -> None:
    c = make(sim, pool)
    assert c.get("/api/config").json()["aws_connect"] == {"available": False}
    view = c.get(URL, headers=h(pool)).json()
    assert view["available"] is False and view["reason"] == "not_configured"
    assert view["permissions"] == [] and view["consent"] is None and view["external_id"] is None
    for r in (c.put(URL, headers=h(pool), json=SETTINGS), c.delete(URL, headers=h(pool))):
        assert r.status_code == 409 and r.json()["error"]["code"] == "aws_connect_unavailable"


def test_it_needs_sign_in_to_be_on(sim: Simulator) -> None:
    c = make(sim, None, aws_connect_account_id=ACCOUNT)  # no user pool: everyone is one shared anonymous owner
    assert c.get("/api/config").json()["aws_connect"] == {"available": False}
    assert c.get(URL).json()["reason"] == "sign_in_off"
    r = c.put(URL, json=SETTINGS)
    assert r.status_code == 409 and r.json()["error"]["code"] == "aws_connect_unavailable"
    assert c.get(f"{URL}/template").status_code == 409


def test_a_visitor_who_is_not_signed_in_sees_why_and_cannot_write(on: TestClient) -> None:
    assert on.get("/api/config").json()["aws_connect"] == {"available": True}
    view = on.get(URL).json()
    assert view["available"] is False and view["reason"] == "sign_in_required"
    for r in (on.put(URL, json=SETTINGS), on.put(f"{URL}/role", json={"role_arn": ROLE}), on.delete(URL)):
        assert r.status_code == 401 and r.json()["error"]["code"] == "sign_in_required"
    assert on.get(f"{URL}/template").status_code == 401


# ---- the three steps -------------------------------------------------------------------------------


def test_a_new_user_sees_what_they_would_agree_to_and_every_permission(on: TestClient, pool: FakePool) -> None:
    view = on.get(URL, headers=h(pool)).json()
    assert view["available"] is True and view["status"] == "not_connected" and view["reason"] is None
    assert view["consent"]["version"] == CONSENT_VERSION and len(view["consent"]["points"]) >= 4
    assert view["setup"] == {
        "role_name": "AgentOpsReadOnly",
        "template_file": "agentops-read-only-role.template.json",
        "trusted_account_id": ACCOUNT,
    }
    actions = {a for p in view["permissions"] for a in p["actions"]}
    assert "logs:FilterLogEvents" in actions and "cloudtrail:LookupEvents" in actions
    assert not any(a.startswith(("s3:", "dynamodb:", "iam:", "secretsmanager:")) for a in actions)


def test_the_whole_flow_then_disconnect(on: TestClient, pool: FakePool) -> None:
    saved = on.put(URL, headers=h(pool), json=SETTINGS)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["status"] == "draft" and body["region"] == "eu-west-1" and body["consent_current"] is True
    assert body["log_groups"] == ["/aws/lambda/checkout", "/ecs/payments"] and body["role_arn"] is None
    assert body["external_id"].startswith("agentops-") and len(body["external_id"]) >= 30

    template = on.get(f"{URL}/template", headers=h(pool))
    assert template.status_code == 200
    assert template.headers["content-disposition"] == 'attachment; filename="agentops-read-only-role.template.json"'
    assert template.headers["cache-control"] == "no-store"
    trust = template.json()["Resources"]["AgentOpsReadOnlyRole"]["Properties"]["AssumeRolePolicyDocument"]
    assert trust["Statement"][0]["Principal"] == {"AWS": f"arn:aws:iam::{ACCOUNT}:root"}
    assert trust["Statement"][0]["Condition"]["StringEquals"]["sts:ExternalId"] == body["external_id"]

    registered = on.put(f"{URL}/role", headers=h(pool), json={"role_arn": ROLE})
    assert registered.status_code == 200, registered.text
    assert registered.json()["status"] == "registered" and registered.json()["role_arn"] == ROLE
    assert on.get(URL, headers=h(pool)).json()["role_arn"] == ROLE

    gone = on.delete(URL, headers=h(pool))
    assert gone.status_code == 200 and gone.json()["status"] == "not_connected" and gone.json()["role_arn"] is None
    assert on.get(f"{URL}/template", headers=h(pool)).json()["error"]["code"] == "aws_connection_not_set_up"
    assert on.delete(URL, headers=h(pool)).status_code == 200  # disconnecting twice is fine


def test_the_template_and_the_role_need_the_settings_first(on: TestClient, pool: FakePool) -> None:
    for r in (
        on.get(f"{URL}/template", headers=h(pool)),
        on.put(f"{URL}/role", headers=h(pool), json={"role_arn": ROLE}),
    ):
        assert r.status_code == 409 and r.json()["error"]["code"] == "aws_connection_not_set_up"


def test_the_external_id_never_changes_but_new_settings_need_a_new_role(on: TestClient, pool: FakePool) -> None:
    first = on.put(URL, headers=h(pool), json=SETTINGS).json()
    on.put(f"{URL}/role", headers=h(pool), json={"role_arn": ROLE})

    reordered = ["/ecs/payments", "/aws/lambda/checkout"]
    same = on.put(URL, headers=h(pool), json={**SETTINGS, "log_groups": reordered}).json()
    assert same["status"] == "registered" and same["role_arn"] == ROLE  # same groups in another order: nothing to redo
    assert same["log_groups"] == first["log_groups"] and same["external_id"] == first["external_id"]

    changed = on.put(URL, headers=h(pool), json={**SETTINGS, "log_groups": ["/aws/lambda/checkout"]}).json()
    assert changed["status"] == "draft" and changed["role_arn"] is None  # the role grants the old list: make it again
    assert changed["external_id"] == first["external_id"]  # but the id the role trusts is unchanged

    moved = on.put(URL, headers=h(pool), json={**SETTINGS, "region": "us-west-2"}).json()
    assert moved["status"] == "draft" and moved["region"] == "us-west-2"
    assert moved["external_id"] == first["external_id"]


def test_the_client_cannot_choose_the_external_id_or_the_role_in_the_settings(on: TestClient, pool: FakePool) -> None:
    for extra in ({"external_id": "agentops-chosen-by-me-12345"}, {"role_arn": ROLE}, {"status": "registered"}):
        r = on.put(URL, headers=h(pool), json={**SETTINGS, **extra})
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert on.get(URL, headers=h(pool)).json()["status"] == "not_connected"
    assert on.put(f"{URL}/role", headers=h(pool), json={"role_arn": ROLE, "external_id": "x"}).status_code == 422


# ---- one user cannot see or use another's ------------------------------------------------------------


def test_each_user_has_their_own_connection_and_their_own_external_id(on: TestClient, pool: FakePool) -> None:
    a = on.put(URL, headers=h(pool, USER_A), json=SETTINGS).json()
    on.put(f"{URL}/role", headers=h(pool, USER_A), json={"role_arn": ROLE})

    b_before = on.get(URL, headers=h(pool, USER_B)).json()
    assert b_before["status"] == "not_connected" and b_before["external_id"] is None and b_before["role_arn"] is None
    assert on.get(f"{URL}/template", headers=h(pool, USER_B)).status_code == 409

    b = on.put(URL, headers=h(pool, USER_B), json={**SETTINGS, "log_groups": ["/other/app"]}).json()
    assert b["external_id"] != a["external_id"] and b["log_groups"] == ["/other/app"] and b["role_arn"] is None
    assert on.get(URL, headers=h(pool, USER_A)).json()["log_groups"] == SETTINGS["log_groups"]

    on.delete(URL, headers=h(pool, USER_B))
    assert on.get(URL, headers=h(pool, USER_A)).json()["status"] == "registered"  # B's disconnect left A's alone


def test_a_client_cannot_name_another_owner(on: TestClient, pool: FakePool) -> None:
    on.put(URL, headers=h(pool, USER_A), json=SETTINGS)
    spoof = {**h(pool, USER_B), "X-Owner": USER_A, "X-User-Id": USER_A}
    r = on.get(URL, headers=spoof, params={"owner": USER_A, "sub": USER_A})
    assert r.json()["status"] == "not_connected"


# ---- which accounts, and what was agreed to ------------------------------------------------------------


def test_only_the_configured_accounts_may_be_registered(sim: Simulator, pool: FakePool) -> None:
    only_mine = make(sim, pool, aws_connect_account_id=ACCOUNT)
    only_mine.put(URL, headers=h(pool), json=SETTINGS)
    refused = only_mine.put(f"{URL}/role", headers=h(pool), json={"role_arn": OTHER_ROLE})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "aws_account_not_enabled"
    assert only_mine.get(URL, headers=h(pool)).json()["role_arn"] is None  # and nothing was stored

    widened = make(sim, pool, aws_connect_account_id=ACCOUNT, aws_connect_allowed_accounts=("999988887777",))
    widened.put(URL, headers=h(pool), json=SETTINGS)
    assert widened.put(f"{URL}/role", headers=h(pool), json={"role_arn": OTHER_ROLE}).status_code == 200
    # An explicit list replaces the default: AgentOps's own account is no longer implied.
    assert widened.put(f"{URL}/role", headers=h(pool), json={"role_arn": ROLE}).status_code == 409


def test_agreeing_to_the_wrong_wording_is_refused(on: TestClient, pool: FakePool) -> None:
    stale = on.put(URL, headers=h(pool), json={**SETTINGS, "consent_version": "1999-01-01"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "consent_out_of_date"
    declined = on.put(URL, headers=h(pool), json={**SETTINGS, "accept_data_use": False})
    assert declined.status_code == 422
    assert on.get(URL, headers=h(pool)).json()["status"] == "not_connected"


def _service(store: InMemoryConnectionStore, kind: str) -> ConnectionService:
    return ConnectionService(
        store,
        Settings(aws_connect_account_id=ACCOUNT),
        sign_in_on=True,
        provider_kind=kind,
        provider_label="label",
        now=lambda: datetime(2026, 11, 2, tzinfo=UTC),
    )


def test_switching_to_a_real_ai_needs_a_fresh_agreement_before_anything_else_happens() -> None:
    store = InMemoryConnectionStore()
    settings = ConnectionSettings.model_validate(SETTINGS)
    offline = _service(store, "mock")
    offline.save_settings(USER_A, settings)
    assert offline.template(USER_A)  # fine while nothing leaves AgentOps

    online = _service(store, "openai")  # the same stored connection, but the site now sends excerpts to OpenAI
    assert online.view(USER_A).consent_current is False
    assert any("OpenAI" in p or "label" in p for p in online.view(USER_A).consent.points)  # type: ignore[union-attr]
    with pytest.raises(ConsentOutOfDateError):
        online.template(USER_A)
    with pytest.raises(ConsentOutOfDateError):
        online.register_role(USER_A, RoleRegistration(role_arn=ROLE))

    again = online.save_settings(USER_A, settings)  # agrees again: nothing else about the connection changes
    assert again.consent_current is True and again.external_id == offline.view(USER_A).external_id
    assert online.template(USER_A)


def test_the_consent_wording_follows_who_analyses_the_logs() -> None:
    store = InMemoryConnectionStore()
    offline = " ".join(_service(store, "mock").view(USER_A).consent.points)  # type: ignore[union-attr]
    online = " ".join(_service(store, "openai").view(USER_A).consent.points)  # type: ignore[union-attr]
    assert "nothing you connect is sent outside" in offline
    assert "sent to the AI service" in online and "nothing you connect is sent outside" not in online


# ---- the usual guards -------------------------------------------------------------------------------


def test_writes_need_the_access_code_when_one_is_set(sim: Simulator, pool: FakePool) -> None:
    c = TestClient(
        create_app(
            simulator=sim,
            settings=Settings(storage_mode="memory", aws_connect_account_id=ACCOUNT),
            verifier=pool.verifier(),
            access=AccessGate("open-sesame-1"),
        )
    )
    assert c.put(URL, headers=h(pool), json=SETTINGS).status_code == 401
    assert c.put(f"{URL}/role", headers=h(pool), json={"role_arn": ROLE}).status_code == 401
    assert c.delete(URL, headers=h(pool)).status_code == 401
    ok = c.put(URL, headers={**h(pool), ACCESS_HEADER: "open-sesame-1"}, json=SETTINGS)
    assert ok.status_code == 200
    assert c.get(URL, headers=h(pool)).status_code == 200  # reading what you have saved needs no code
    assert c.get(f"{URL}/template", headers=h(pool)).status_code == 200


def test_a_rejected_value_is_never_echoed_back(on: TestClient, pool: FakePool) -> None:
    r = on.put(URL, headers=h(pool), json={**SETTINGS, "log_groups": ["<script>alert(1)</script>"]})
    assert r.status_code == 422 and "<script>" not in r.text
    r = on.put(f"{URL}/role", headers=h(pool), json={"role_arn": "arn:aws:iam::1:role/NotMine-secret-value"})
    assert r.status_code == 422 and "secret-value" not in r.text


def test_the_external_id_is_not_written_to_the_log(
    on: TestClient, pool: FakePool, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        body = on.put(URL, headers=h(pool), json=SETTINGS).json()
        on.get(f"{URL}/template", headers=h(pool))
    assert body["external_id"] not in caplog.text


def test_a_stored_record_that_no_longer_validates_is_treated_as_absent() -> None:
    store = InMemoryConnectionStore()
    store._items[USER_A] = '{"status": "registered", "external_id": "short"}'
    assert store.get(USER_A) is None
    assert _service(store, "mock").view(USER_A).status == "not_connected"


# ---- configuration ----------------------------------------------------------------------------------


def test_settings_read_the_connect_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AWS_CONNECT_ACCOUNT_ID", "AWS_CONNECT_PRINCIPAL_ARN", "AWS_CONNECT_ALLOWED_ACCOUNTS"):
        monkeypatch.delenv(name, raising=False)
    off = Settings.from_env()
    assert (off.aws_connect_account_id, off.aws_connect_principal_arn, off.aws_connect_allowed_accounts) == ("", "", ())

    monkeypatch.setenv("AWS_CONNECT_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("AWS_CONNECT_PRINCIPAL_ARN", f"arn:aws:iam::{ACCOUNT}:role/AgentOps-Broker")
    monkeypatch.setenv("AWS_CONNECT_ALLOWED_ACCOUNTS", f"{ACCOUNT}, 999988887777")
    on_ = Settings.from_env()
    assert on_.aws_connect_principal_arn.endswith("role/AgentOps-Broker")
    assert on_.aws_connect_allowed_accounts == (ACCOUNT, "999988887777")


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"AWS_CONNECT_ACCOUNT_ID": "1234"}, "12-digit"),
        ({"AWS_CONNECT_ACCOUNT_ID": "abcdefghijkl"}, "12-digit"),
        ({"AWS_CONNECT_ALLOWED_ACCOUNTS": "111122223333"}, "only applies together"),
        ({"AWS_CONNECT_ACCOUNT_ID": ACCOUNT, "AWS_CONNECT_ALLOWED_ACCOUNTS": "111122223333,oops"}, "12-digit"),
        ({"AWS_CONNECT_PRINCIPAL_ARN": f"arn:aws:iam::{ACCOUNT}:root"}, "must be a role or root"),  # no account set
        (
            {"AWS_CONNECT_ACCOUNT_ID": ACCOUNT, "AWS_CONNECT_PRINCIPAL_ARN": "arn:aws:iam::999988887777:root"},
            "must be a role or root",  # someone else's account
        ),
        (
            {"AWS_CONNECT_ACCOUNT_ID": ACCOUNT, "AWS_CONNECT_PRINCIPAL_ARN": f"arn:aws:iam::{ACCOUNT}:role/*"},
            "must be a role or root",  # a wildcard
        ),
        ({"AWS_CONNECT_ACCOUNT_ID": ACCOUNT, "AWS_CONNECT_PRINCIPAL_ARN": "*"}, "must be a role or root"),
    ],
)
def test_bad_connect_configuration_fails_at_start_up(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], message: str
) -> None:
    for name in ("AWS_CONNECT_ACCOUNT_ID", "AWS_CONNECT_PRINCIPAL_ARN", "AWS_CONNECT_ALLOWED_ACCOUNTS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=message):
        Settings.from_env()


def test_a_named_principal_role_is_what_the_template_trusts(sim: Simulator, pool: FakePool) -> None:
    principal = f"arn:aws:iam::{ACCOUNT}:role/AgentOps-Broker"
    c = make(sim, pool, aws_connect_account_id=ACCOUNT, aws_connect_principal_arn=principal)
    c.put(URL, headers=h(pool), json=SETTINGS)
    trust = c.get(f"{URL}/template", headers=h(pool)).json()["Resources"]["AgentOpsReadOnlyRole"]["Properties"]
    assert trust["AssumeRolePolicyDocument"]["Statement"][0]["Principal"] == {"AWS": principal}
