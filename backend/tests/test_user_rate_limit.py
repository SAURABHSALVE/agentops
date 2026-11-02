"""One person cannot use up everyone's investigations: a cap per signed-in user, on top of the shared one."""

from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import RateLimitedError
from app.main import create_app
from app.security import KeyedRateLimiter
from app.simulator.service import Simulator
from tests.auth_helpers import USER_A, USER_B, FakePool, bearer


@pytest.fixture(scope="module")
def pool() -> FakePool:
    return FakePool()


def _client(sim: Simulator, pool: FakePool, **settings: Any) -> TestClient:
    s = replace(Settings(storage_mode="memory"), **settings)
    return TestClient(create_app(simulator=sim, settings=s, verifier=pool.verifier()))


def _start(c: TestClient, pool: FakePool, sub: str, incident: str) -> int:
    return int(c.post(f"/api/incidents/{incident}/investigations", headers=bearer(pool.token(sub))).status_code)


def test_one_user_over_their_cap_is_refused_and_another_is_not(sim: Simulator, pool: FakePool) -> None:
    c = _client(sim, pool, user_rate_per_minute=2)
    assert [_start(c, pool, USER_A, i) for i in ("INC-1001", "INC-1002")] == [202, 202]
    refused = c.post("/api/incidents/INC-1003/investigations", headers=bearer(pool.token(USER_A)))
    assert refused.status_code == 429 and refused.json()["error"]["code"] == "rate_limited"
    assert int(refused.headers["retry-after"]) > 0
    assert _start(c, pool, USER_B, "INC-1001") == 202  # B has their own allowance


def test_a_refusal_leaves_no_running_investigation_behind(sim: Simulator, pool: FakePool) -> None:
    c = _client(sim, pool, user_rate_per_minute=1)
    _start(c, pool, USER_A, "INC-1001")
    assert _start(c, pool, USER_A, "INC-1002") == 429
    assert c.get("/api/incidents/INC-1002/investigations/latest", headers=bearer(pool.token(USER_A))).json() is None


def test_the_shared_cap_still_applies_to_everyone_together(sim: Simulator, pool: FakePool) -> None:
    c = _client(sim, pool, rate_per_minute=2, user_rate_per_minute=10)
    assert _start(c, pool, USER_A, "INC-1001") == 202
    assert _start(c, pool, USER_B, "INC-1001") == 202
    assert _start(c, pool, USER_A, "INC-1002") == 429  # the whole process is at its limit


def test_a_user_turned_away_does_not_spend_the_shared_allowance(sim: Simulator, pool: FakePool) -> None:
    c = _client(sim, pool, rate_per_minute=3, user_rate_per_minute=1)
    assert _start(c, pool, USER_A, "INC-1001") == 202
    for _ in range(5):
        assert _start(c, pool, USER_A, "INC-1002") == 429  # A hammers while over their cap
    assert _start(c, pool, USER_B, "INC-1001") == 202  # B still has the shared allowance A did not burn


def test_the_limiter_forgets_old_users_so_memory_stays_bounded() -> None:
    now = [0.0]
    limiter = KeyedRateLimiter(1, 10, clock=lambda: now[0], max_keys=3)
    for user in ("a", "b", "c", "d"):  # "a" is the least recently seen when "d" arrives
        limiter.check(user)
    assert len(limiter._limiters) == 3  # noqa: SLF001
    limiter.check("a")  # a was forgotten, so it starts fresh instead of being refused
    with pytest.raises(RateLimitedError):
        limiter.check("d")  # d is remembered, and has used its one start this minute


def test_the_per_user_caps_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USER_INVESTIGATIONS_PER_MINUTE", "3")
    monkeypatch.setenv("USER_INVESTIGATIONS_PER_HOUR", "9")
    s = Settings.from_env()
    assert (s.user_rate_per_minute, s.user_rate_per_hour) == (3, 9)
    monkeypatch.setenv("USER_INVESTIGATIONS_PER_MINUTE", "0")
    from app.config import ConfigError

    with pytest.raises(ConfigError):
        Settings.from_env()
