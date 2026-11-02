"""Public-demo protections: access code on writes, rate limiting, headers, secret loading."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.factory import build_provider
from app.config import ConfigError, Settings
from app.errors import RateLimitedError
from app.main import create_app
from app.security import ACCESS_HEADER, AccessGate, RateLimiter, load_access_code
from app.simulator.service import Simulator

CODE = "demo-code-1234"


def gated(sim: Simulator, **settings: Any) -> TestClient:
    return TestClient(
        create_app(simulator=sim, access=AccessGate(CODE), settings=Settings(storage_mode="memory", **settings))
    )


def test_reads_stay_open_but_writes_need_the_code(sim: Simulator) -> None:
    c = gated(sim)
    assert c.get("/api/incidents").status_code == 200
    assert c.get("/api/incidents/INC-1001/timeline").status_code == 200
    r = c.post("/api/incidents/INC-1001/investigations")
    assert r.status_code == 401 and r.json() == {
        "error": {"code": "unauthorized", "message": "A valid access code is required for this action."}
    }
    assert c.get("/api/incidents/INC-1001/investigations/latest").json() is None  # nothing was started


@pytest.mark.parametrize("wrong", ["", "x", CODE.upper(), CODE + " ", "demo-code-123"])
def test_wrong_codes_are_rejected_without_hints(sim: Simulator, wrong: str) -> None:
    c = gated(sim)
    r = c.post("/api/incidents/INC-1001/investigations", headers={ACCESS_HEADER: wrong})
    assert r.status_code == 401 and CODE not in r.text


def test_the_right_code_unlocks_start_approve_and_reject(sim: Simulator) -> None:
    c = gated(sim)
    h = {ACCESS_HEADER: CODE}
    inv = c.post("/api/incidents/INC-1001/investigations", headers=h)
    assert inv.status_code == 202
    iid = inv.json()["id"]
    assert c.post(f"/api/investigations/{iid}/remediation/approve").status_code == 401
    assert c.post(f"/api/investigations/{iid}/remediation/reject").status_code == 401
    ok = c.post(f"/api/investigations/{iid}/remediation/approve", headers=h)
    assert ok.status_code == 200 and ok.json()["remediation_state"]["status"] == "resolved"


def test_no_code_configured_means_open_as_before(client: TestClient) -> None:
    assert client.post("/api/incidents/INC-1001/investigations").status_code == 202
    assert client.get("/api/config").json()["write_access"] == "open"


def test_config_endpoint_tells_the_ui_what_it_needs_and_nothing_secret(sim: Simulator) -> None:
    body = gated(sim).get("/api/config").json()
    assert body == {
        "data_mode": "simulated",
        "write_access": "code_required",
        "auth": {"mode": "none"},
        "provider": {"kind": "mock", "label": "Mock provider: deterministic rule-based policy, not an LLM"},
        "aws_connect": {"available": False},  # off unless an account is configured and sign-in is on
    }
    assert CODE not in str(body)


def test_cors_allows_the_access_header_only_from_the_configured_origin(sim: Simulator) -> None:
    c = gated(sim)
    pre = c.options(
        "/api/incidents/INC-1001/investigations",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": ACCESS_HEADER,
        },
    )
    assert pre.status_code == 200 and ACCESS_HEADER.lower() in pre.headers["access-control-allow-headers"].lower()


def test_responses_carry_security_headers_and_api_is_not_cached(client: TestClient) -> None:
    r = client.get("/api/incidents")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"
    assert client.get("/health").headers["x-content-type-options"] == "nosniff"


# ---- rate limit -------------------------------------------------------------------------------


def test_rate_limit_per_minute_then_recovers() -> None:
    now = [0.0]
    limiter = RateLimiter(per_minute=2, per_hour=100, clock=lambda: now[0])
    limiter.check()
    limiter.check()
    with pytest.raises(RateLimitedError) as err:
        limiter.check()
    assert 1 <= err.value.retry_after <= 61
    now[0] = 61.0
    limiter.check()


def test_rate_limit_per_hour_and_refusals_are_not_counted() -> None:
    now = [0.0]
    limiter = RateLimiter(per_minute=100, per_hour=3, clock=lambda: now[0])
    for _ in range(3):
        limiter.check()
    for _ in range(5):  # refused calls must not extend the lockout
        with pytest.raises(RateLimitedError):
            limiter.check()
    now[0] = 3601.0
    limiter.check()


def test_api_returns_429_with_retry_after_and_creates_no_record(sim: Simulator) -> None:
    c = TestClient(create_app(simulator=sim, settings=Settings(storage_mode="memory", rate_per_minute=2)))
    assert c.post("/api/incidents/INC-1001/investigations").status_code == 202
    assert c.post("/api/incidents/INC-1002/investigations").status_code == 202
    r = c.post("/api/incidents/INC-1003/investigations")
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert int(r.headers["retry-after"]) >= 1
    assert c.get("/api/incidents/INC-1003/investigations/latest").json() is None  # nothing left 'running'


def test_a_rejected_unauthenticated_request_does_not_use_the_rate_limit(sim: Simulator) -> None:
    c = gated(sim, rate_per_minute=1)
    for _ in range(5):
        assert c.post("/api/incidents/INC-1001/investigations").status_code == 401
    assert c.post("/api/incidents/INC-1001/investigations", headers={ACCESS_HEADER: CODE}).status_code == 202


# ---- configuration and secrets --------------------------------------------------------------


def test_access_code_comes_from_the_environment_and_must_be_long_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACCESS_CODE", raising=False)
    monkeypatch.delenv("ACCESS_SECRET_ARN", raising=False)
    assert load_access_code() is None
    monkeypatch.setenv("ACCESS_CODE", "short")
    with pytest.raises(ConfigError):
        load_access_code()
    monkeypatch.setenv("ACCESS_CODE", CODE)
    assert load_access_code() == CODE


def test_secrets_are_fetched_from_secrets_manager_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched: list[str] = []

    def fake_secret(arn: str) -> str:
        fetched.append(arn)
        return "from-secrets-manager-1234"

    monkeypatch.setattr("app.security._secret_from_arn", fake_secret)
    monkeypatch.delenv("ACCESS_CODE", raising=False)
    monkeypatch.setenv("ACCESS_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:111111111111:secret:x")
    assert load_access_code() == "from-secrets-manager-1234" and len(fetched) == 1

    def broken(arn: str) -> str:
        raise RuntimeError("AccessDenied: arn:aws:... secret-detail")

    monkeypatch.setattr("app.security._secret_from_arn", broken)
    with pytest.raises(ConfigError) as err:
        load_access_code()
    assert "AccessDenied" not in str(err.value)


def test_openai_key_can_come_from_a_secret_and_failures_do_not_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:111111111111:secret:openai")
    settings = Settings.from_env()
    monkeypatch.setattr("app.agent.factory._read_secret", lambda arn: "sk-secret-value-123456")
    assert build_provider(settings).info.kind == "openai"

    def broken(arn: str) -> str:
        raise ConfigError("OPENAI_SECRET_ARN could not be read.")

    monkeypatch.setattr("app.agent.factory._read_secret", broken)
    with pytest.raises(ConfigError, match="could not be read"):
        build_provider(settings)


def test_rate_limit_settings_are_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INVESTIGATIONS_PER_MINUTE", "0")
    with pytest.raises(ConfigError):
        Settings.from_env()
    monkeypatch.setenv("INVESTIGATIONS_PER_MINUTE", "5")
    monkeypatch.setenv("INVESTIGATIONS_PER_HOUR", "50")
    s = Settings.from_env()
    assert (s.rate_per_minute, s.rate_per_hour) == (5, 50)


def test_every_write_route_is_behind_the_access_gate() -> None:
    """Structural guard: a future POST route that forgets the dependency fails here, not in production."""
    from fastapi.routing import APIRoute

    from app.api import require_access, router

    writes = [
        r
        for r in router.routes
        if isinstance(r, APIRoute) and (r.methods or set()) & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    # start, approve, reject, feedback, delete my investigations, then save settings, register role and disconnect
    assert len(writes) == 8
    for route in writes:
        assert any(d.call is require_access for d in route.dependant.dependencies), route.path
