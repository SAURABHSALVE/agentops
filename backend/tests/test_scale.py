"""What lets many people use the site safely: any account may connect, an invited user need not repeat the access code,
a real model costs a bounded amount per user and per day, and a model gets one chance to fix an ungrounded answer."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.models import ProviderInfo
from app.agent.prompts import REPAIR_NOTICE
from app.config import ConfigError, Settings
from app.errors import RateLimitedError
from app.main import create_app
from app.security import ACCESS_HEADER, AccessGate
from app.simulator.service import Simulator
from app.usage import DailyCaps, InMemoryUsageStore, SqliteUsageStore, UsageStore
from tests.auth_helpers import USER_A, USER_B, FakePool, bearer
from tests.harness import run_investigation
from tests.scripted_provider import ScriptedProvider, call, calls, final
from tests.test_aws_connection import ACCOUNT, OTHER_ROLE, ROLE, SETTINGS, URL
from tests.test_aws_connection import make as make_connect

CODE = "shared-invite-code"
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


POOL = FakePool()


@pytest.fixture
def pool() -> FakePool:
    return POOL


class AiScripted(ScriptedProvider):
    """A scripted provider that says it is a real model (so the costs and the repair retry apply to it)."""

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(kind="openai", label="Scripted AI")


def authed(pool: FakePool, sub: str = USER_A, code: str | None = None) -> dict[str, str]:
    return {**bearer(pool.token(sub)), **({ACCESS_HEADER: code} if code else {})}


# ---- any account may connect ----------------------------------------------------------------------


def test_any_account_can_register_a_role_when_the_list_is_a_star(sim: Simulator, pool: FakePool) -> None:
    client = make_connect(sim, pool, aws_connect_account_id=ACCOUNT, aws_connect_allowed_accounts=("*",))
    assert client.put(URL, headers=authed(pool), json=SETTINGS).status_code == 200
    r = client.put(f"{URL}/role", headers=authed(pool), json={"role_arn": OTHER_ROLE})
    assert r.status_code == 200 and r.json()["role_arn"] == OTHER_ROLE


def test_a_specific_list_still_refuses_other_accounts(sim: Simulator, pool: FakePool) -> None:
    client = make_connect(sim, pool, aws_connect_account_id=ACCOUNT, aws_connect_allowed_accounts=(ACCOUNT,))
    client.put(URL, headers=authed(pool), json=SETTINGS)
    r = client.put(f"{URL}/role", headers=authed(pool), json={"role_arn": OTHER_ROLE})
    assert r.status_code == 409 and r.json()["error"]["code"] == "aws_account_not_enabled"
    assert client.put(f"{URL}/role", headers=authed(pool), json={"role_arn": ROLE}).status_code == 200


def test_the_star_is_accepted_alone_but_not_mixed_with_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_CONNECT_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("AWS_CONNECT_ALLOWED_ACCOUNTS", "*")
    assert Settings.from_env().aws_connect_allowed_accounts == ("*",)
    for bad in ("*,999988887777", "**", "123"):
        monkeypatch.setenv("AWS_CONNECT_ALLOWED_ACCOUNTS", bad)
        with pytest.raises(ConfigError):
            Settings.from_env()


# ---- the access code is needed once ----------------------------------------------------------------


def gated(sim: Simulator, pool: FakePool) -> TestClient:
    return TestClient(
        create_app(
            simulator=sim,
            settings=Settings(storage_mode="memory"),
            access=AccessGate(CODE),
            verifier=pool.verifier(),
        )
    )


def write(client: TestClient, headers: dict[str, str]) -> int:
    return int(client.delete("/api/me/investigations", headers=headers).status_code)


def test_a_user_who_presents_the_code_once_is_invited_and_need_not_repeat_it(sim: Simulator, pool: FakePool) -> None:
    client = gated(sim, pool)
    assert write(client, authed(pool)) == 401  # no code
    assert write(client, authed(pool, code="wrong-code-123")) == 401
    assert client.get("/api/config", headers=authed(pool)).json()["write_access"] == "code_required"
    assert write(client, authed(pool, code=CODE)) == 200  # presented once
    assert write(client, authed(pool)) == 200  # and remembered
    assert client.get("/api/config", headers=authed(pool)).json()["write_access"] == "open"


def test_being_invited_is_personal_and_a_wrong_code_invites_nobody(sim: Simulator, pool: FakePool) -> None:
    client = gated(sim, pool)
    write(client, authed(pool, USER_A, CODE))
    assert write(client, authed(pool, USER_B)) == 401  # B was never invited
    assert write(client, authed(pool, USER_B, "wrong-code-123")) == 401
    assert write(client, authed(pool, USER_B)) == 401  # still not invited after the failed try
    assert client.get("/api/config", headers=authed(pool, USER_B)).json()["write_access"] == "code_required"


def test_a_visitor_who_is_not_signed_in_is_never_invited(sim: Simulator, pool: FakePool) -> None:
    client = gated(sim, pool)
    assert client.delete("/api/me/investigations", headers={ACCESS_HEADER: CODE}).status_code == 401  # needs sign-in
    assert client.get("/api/config").json()["write_access"] == "code_required"


# ---- daily caps ------------------------------------------------------------------------------------


@pytest.fixture(params=["memory", "sqlite", "dynamodb"])
def store(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[UsageStore]:
    if request.param == "memory":
        yield InMemoryUsageStore()
    elif request.param == "sqlite":
        yield SqliteUsageStore(tmp_path / "agentops.sqlite3")
    else:
        boto3 = pytest.importorskip("boto3")
        mock_aws = pytest.importorskip("moto").mock_aws
        from app.usage import DynamoDbUsageStore

        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            monkeypatch.setenv(name, "testing")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        with mock_aws():
            ddb = boto3.resource("dynamodb")
            ddb.create_table(
                TableName="inv",
                KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )
            yield DynamoDbUsageStore(ddb.Table("inv"))


def test_a_counter_stops_exactly_at_its_limit_and_a_release_gives_one_back(store: UsageStore) -> None:
    assert [store.consume("k", 3, 2_000_000_000) for _ in range(5)] == [True, True, True, False, False]
    store.release("k")
    assert store.consume("k", 3, 2_000_000_000) is True and store.consume("k", 3, 2_000_000_000) is False
    for _ in range(5):
        store.release("k")  # never below zero
    assert [store.consume("k", 2, 2_000_000_000) for _ in range(3)] == [True, True, False]


def test_counters_and_invitations_are_kept_apart(store: UsageStore) -> None:
    assert store.consume("a", 1, 2_000_000_000) and store.consume("b", 1, 2_000_000_000)
    assert not store.consume("a", 1, 2_000_000_000)
    assert not store.is_invited(USER_A)
    store.invite(USER_A)
    store.invite(USER_A)  # inviting twice is harmless
    assert store.is_invited(USER_A) and not store.is_invited(USER_B)


def test_caps_refuse_with_a_clear_message_and_a_wait_until_midnight() -> None:
    caps = DailyCaps(InMemoryUsageStore(), per_user=2, total=3, costs_money=True, clock=lambda: NOW)
    caps.charge(USER_A)
    caps.charge(USER_A)
    with pytest.raises(RateLimitedError) as user_cap:
        caps.charge(USER_A)
    assert "2 investigations for today" in user_cap.value.message and user_cap.value.retry_after == 12 * 3600
    caps.charge(USER_B)
    with pytest.raises(RateLimitedError) as site_cap:
        caps.charge(USER_B)
    assert "daily investigation limit" in site_cap.value.message


def test_a_site_refusal_does_not_use_up_the_users_allowance_and_a_refund_gives_it_back() -> None:
    store = InMemoryUsageStore()
    caps = DailyCaps(store, per_user=2, total=1, costs_money=True, clock=lambda: NOW)
    caps.charge(USER_A)
    with pytest.raises(RateLimitedError):
        caps.charge(USER_B)
    assert store._counts[f"user#{USER_B}#20261104"] == 0  # noqa: SLF001 - the refusal was the site's, not B's
    caps.refund(USER_A)
    caps.charge(USER_B)  # the site's slot came back


def test_the_next_day_starts_fresh() -> None:
    now = [NOW]
    caps = DailyCaps(InMemoryUsageStore(), per_user=1, total=0, costs_money=True, clock=lambda: now[0])
    caps.charge(USER_A)
    with pytest.raises(RateLimitedError):
        caps.charge(USER_A)
    now[0] = NOW + timedelta(days=1)
    caps.charge(USER_A)


def test_the_free_offline_assistant_and_a_zero_cap_are_never_limited() -> None:
    for caps in (
        DailyCaps(InMemoryUsageStore(), per_user=1, total=1, costs_money=False, clock=lambda: NOW),
        DailyCaps(InMemoryUsageStore(), per_user=0, total=0, costs_money=True, clock=lambda: NOW),
    ):
        for _ in range(5):
            caps.charge(USER_A)


def capped_client(sim: Simulator, pool: FakePool, kind: str, per_user: int = 0, total: int = 0) -> TestClient:
    provider: ScriptedProvider = (
        AiScripted(*[final(confidence="low")] * 20)
        if kind == "openai"
        else ScriptedProvider(*[final(confidence="low")] * 20)
    )
    return TestClient(
        create_app(
            simulator=sim,
            settings=Settings(
                storage_mode="memory", user_investigations_per_day=per_user, investigations_per_day=total
            ),
            provider=provider,
            verifier=pool.verifier(),
        )
    )


def start(client: TestClient, sub: str, incident: str = "INC-1001") -> Any:
    return client.post(f"/api/incidents/{incident}/investigations", headers=authed(POOL, sub))


def test_a_user_stops_at_their_daily_number_and_another_user_is_unaffected(sim: Simulator) -> None:
    client = capped_client(sim, POOL, "openai", per_user=2, total=50)
    assert [start(client, USER_A).status_code for _ in range(2)] == [202, 202]
    third = start(client, USER_A)
    assert third.status_code == 429 and third.json()["error"]["code"] == "rate_limited"
    assert "2 investigations for today" in third.json()["error"]["message"] and "Retry-After" in third.headers
    assert start(client, USER_B).status_code == 202


def test_the_site_stops_at_its_daily_number_without_charging_the_user_who_was_turned_away(sim: Simulator) -> None:
    client = capped_client(sim, POOL, "openai", per_user=5, total=3)
    assert [start(client, USER_A).status_code for _ in range(3)] == [202, 202, 202]
    refused = start(client, USER_B)
    assert refused.status_code == 429 and "daily investigation limit" in refused.json()["error"]["message"]
    counts = client.app.state.usage._counts  # type: ignore[attr-defined]
    assert counts[f"user#{USER_B}#{datetime.now(UTC):%Y%m%d}"] == 0


def test_an_investigation_that_cannot_start_costs_nothing(sim: Simulator) -> None:
    client = capped_client(sim, POOL, "openai", per_user=1)
    assert start(client, USER_A, "INC-9999").status_code == 404
    assert start(client, USER_A).status_code == 202  # the one allowed run is still there


def test_the_offline_assistant_is_never_capped(sim: Simulator) -> None:
    client = capped_client(sim, POOL, "mock", per_user=1, total=1)
    assert [start(client, USER_A).status_code for _ in range(4)] == [202, 202, 202, 202]


# ---- one chance to fix an ungrounded answer -------------------------------------------------------

BAD = {"source": "logs", "reference_id": "never-returned", "observation": "Invented."}


def test_a_real_model_that_cites_evidence_it_never_retrieved_gets_one_chance_to_correct_itself(sim: Simulator) -> None:
    provider = AiScripted(
        calls(call("search_logs", {"query": "timeout", "limit": 5})),
        final(confidence="medium", evidence=[BAD]),
        final(confidence="low", evidence=[]),
    )
    done = run_investigation(sim, provider, "INC-1005")[0]
    assert done.status == "completed" and done.findings is not None and done.findings.confidence == "low"
    last = provider.requests[-1].messages
    assert last[-1].content == REPAIR_NOTICE and last[-2].role == "assistant" and "never-returned" in last[-2].content


def test_a_second_ungrounded_answer_still_fails_the_run(sim: Simulator) -> None:
    provider = AiScripted(
        calls(call("search_logs", {"query": "timeout", "limit": 5})),
        final(confidence="medium", evidence=[BAD]),
        final(confidence="medium", evidence=[BAD]),
    )
    done = run_investigation(sim, provider, "INC-1005")[0]
    assert done.status == "failed" and done.failure is not None and done.failure.code == "ungrounded_evidence"
    assert len(provider.requests) == 3


def test_the_offline_provider_is_not_given_a_second_chance(sim: Simulator) -> None:
    provider = ScriptedProvider(
        calls(call("search_logs", {"query": "timeout", "limit": 5})), final(confidence="medium", evidence=[BAD])
    )
    done = run_investigation(sim, provider, "INC-1005")[0]
    assert done.status == "failed" and len(provider.requests) == 2
