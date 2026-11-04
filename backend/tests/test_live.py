"""Reading a user's own AWS account: alarms become incidents, the four tools answer from the account, and nothing
leaks between users or past the masks. No test here calls AWS: readers are fakes that answer from memory."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.models import ProviderInfo, TokenUsage
from app.agent.prompts import decode_tool_result
from app.agent.provider import CompletionRequest, CompletionResponse
from app.config import Settings
from app.connections.models import AwsConnection
from app.connections.service import CONSENT_VERSION
from app.errors import AwsRoleUnreachableError
from app.live.alarms import alarm_from_raw, alarm_id, to_detail, window_of
from app.live.diagnostics import infer_level
from app.live.models import LiveAlarm
from app.live.reader import Change, ChangeScan, LogHit, LogScan
from app.main import create_app
from app.simulator.service import Simulator
from tests.auth_helpers import USER_A, USER_B, FakePool, bearer
from tests.scripted_provider import call, calls

ACCOUNT = "111122223333"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/AgentOpsReadOnly"
URL = "/api/me/aws-connection"
LIVE = "/api/me/aws/incidents"
GROUP = "/aws/lambda/checkout"
SETTINGS = {
    "region": "eu-west-1",
    "log_groups": [GROUP, "/ecs/payments"],
    "consent_version": CONSENT_VERSION,
    "accept_data_use": True,
}


def raw_alarm(now: datetime, name: str = "checkout-errors", minutes_ago: int = 40, **extra: Any) -> dict[str, Any]:
    return {
        "AlarmName": name,
        "AlarmDescription": "Checkout errors",
        "StateValue": "ALARM",
        "StateReason": "Threshold Crossed: 1 datapoint [12.0] was above the threshold (5.0). Ask ops@example.com",
        "StateTransitionedTimestamp": now - timedelta(minutes=minutes_ago),
        "MetricName": "Errors",
        "Namespace": "AWS/Lambda",
        "Statistic": "Sum",
        "Period": 60,
        "Threshold": 5.0,
        "ComparisonOperator": "GreaterThanThreshold",
        "Dimensions": [{"Name": "FunctionName", "Value": "checkout"}],
        **extra,
    }


class FakeReader:
    def __init__(self, now: datetime, alarms: list[dict[str, Any]] | None = None) -> None:
        self.now = now
        self.alarms = [raw_alarm(now)] if alarms is None else alarms
        self.hits: list[LogHit] = []
        self.logs_complete = True
        self.changes: list[Change] = []
        self.changes_complete = True
        self.no_points = False
        self.calls: list[str] = []
        self.searched: list[tuple[Sequence[str], Sequence[str]]] = []

    def firing_alarms(self) -> list[dict[str, Any]]:
        self.calls.append("alarms")
        return self.alarms

    def metric_points(self, alarm: LiveAlarm, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        self.calls.append("metric")
        if self.no_points:
            return []
        out: list[tuple[datetime, float]] = []
        moment = start.replace(second=0, microsecond=0)
        while moment <= end:
            out.append((moment, 12.0 if moment >= alarm.state_since else 0.0))
            moment += timedelta(minutes=1)
        return out

    def search_logs(
        self, groups: Sequence[str], start: datetime, end: datetime, terms: Sequence[str], limit: int
    ) -> LogScan:
        self.calls.append("logs")
        self.searched.append((tuple(groups), tuple(terms)))
        return LogScan(hits=list(self.hits), complete=self.logs_complete)

    def recent_changes(self, start: datetime, end: datetime, limit: int) -> ChangeScan:
        self.calls.append("changes")
        return ChangeScan(changes=list(self.changes), complete=self.changes_complete)


class FakeFactory:
    def __init__(self, reader: FakeReader) -> None:
        self.reader = reader
        self.opened: list[tuple[str, str, str]] = []
        self.error: Exception | None = None

    def reader_for(self, owner: str, connection: AwsConnection) -> FakeReader:
        self.opened.append((owner, str(connection.role_arn), connection.external_id))
        if self.error is not None:
            raise self.error
        return self.reader


class LiveProvider:
    """Asks for all four tools, then answers citing what actually came back (like a model that read the results)."""

    def __init__(self, kind: str = "openai", remediation: str | None = None, confidence: str = "high") -> None:
        self.kind = kind
        self.remediation = remediation
        self.confidence = confidence
        self.requests: list[CompletionRequest] = []

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(kind=self.kind, label="Test AI")

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        results = [decode_tool_result(m.content) for m in request.messages if m.role == "tool"]
        if not results:
            return calls(
                call("get_incident", {}, "c1"),
                call("search_logs", {"query": "timeout"}, "c2"),
                call("get_service_metrics", {"metric": "alarm_metric"}, "c3"),
                call("get_recent_deployments", {"hours": 6}, "c4"),
            )
        logs = next(p for p in results if "entries" in p)
        metric = next(p for p in results if "points" in p)
        evidence = [{"source": "metrics", "reference_id": metric["reference_ids"][0], "observation": "Errors rose."}]
        if logs["entries"]:
            evidence.append({"source": "logs", "reference_id": logs["entries"][0]["id"], "observation": "A timeout."})
        answer = {
            "summary": "Checkout is failing.",
            "root_cause_hypothesis": "Downstream timeouts.",
            "confidence": self.confidence,
            "confidence_rationale": "Logs and metric agree.",
            "evidence": evidence,
            "alternative_hypotheses": [],
            "recommended_next_step": "Check the payments dependency.",
            "remediation_action_id": self.remediation,
            "limitations": [],
        }
        return CompletionResponse(final_text=json.dumps(answer), usage=TokenUsage())


@pytest.fixture(scope="module")
def pool() -> FakePool:
    return FakePool()


def headers(pool: FakePool, sub: str = USER_A) -> dict[str, str]:
    return bearer(pool.token(sub))


def build(
    sim: Simulator, pool: FakePool, reader: FakeReader, provider: LiveProvider | None = None
) -> tuple[TestClient, FakeFactory, LiveProvider]:
    factory = FakeFactory(reader)
    provider = provider or LiveProvider()
    app = create_app(
        simulator=sim,
        settings=Settings(storage_mode="memory", aws_connect_account_id=ACCOUNT),
        provider=provider,
        verifier=pool.verifier(),
        live_readers=factory,
    )
    return TestClient(app), factory, provider


def connect(client: TestClient, pool: FakePool, sub: str = USER_A) -> None:
    assert client.put(URL, headers=headers(pool, sub), json=SETTINGS).status_code == 200
    assert client.put(f"{URL}/role", headers=headers(pool, sub), json={"role_arn": ROLE}).status_code == 200


@pytest.fixture
def now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
def live(sim: Simulator, pool: FakePool, now: datetime) -> tuple[TestClient, FakeFactory, LiveProvider]:
    client, factory, provider = build(sim, pool, FakeReader(now))
    connect(client, pool)
    return client, factory, provider


def incident_id(client: TestClient, pool: FakePool, sub: str = USER_A) -> str:
    return str(client.get(LIVE, headers=headers(pool, sub)).json()["incidents"][0]["id"])


# ---- an alarm becomes an incident ------------------------------------------------------------------------


def test_an_alarm_becomes_an_incident_with_masked_text(now: datetime) -> None:
    alarm = alarm_from_raw(raw_alarm(now), "eu-west-1", now)
    assert alarm is not None
    assert alarm.id == alarm_id("eu-west-1", "checkout-errors") and alarm.id.startswith("LIVE-")
    detail = to_detail(alarm)
    assert detail.service == "Lambda/checkout" and detail.status == "open"
    assert "ops@example.com" not in detail.summary and "[email]" in detail.summary
    assert {s.label for s in detail.signals} >= {"Data source", "Region", "Alarm metric", "Alarm threshold"}
    assert "Your AWS account (live)" in [s.value for s in detail.signals]
    # The window starts a few minutes before the state change, so the datapoint that tripped the alarm is inside it.
    assert detail.started_at == alarm.state_since - timedelta(minutes=5) and detail.window_end == now
    assert detail.history[0].at == alarm.state_since


def test_the_same_alarm_keeps_its_id_and_different_regions_do_not_collide() -> None:
    assert alarm_id("eu-west-1", "a") == alarm_id("eu-west-1", "a") != alarm_id("us-east-1", "a")


def test_a_long_firing_alarm_is_examined_over_its_last_three_hours_only(now: datetime) -> None:
    alarm = alarm_from_raw(raw_alarm(now, minutes_ago=60 * 48), "eu-west-1", now)
    assert alarm is not None
    started, ended = window_of(alarm)
    assert ended - started == timedelta(hours=3)
    assert "only the last 3 hours" in to_detail(alarm).summary


def test_a_start_in_the_future_is_clamped_and_an_unusable_alarm_is_skipped(now: datetime) -> None:
    future = alarm_from_raw(raw_alarm(now, minutes_ago=-30), "eu-west-1", now)
    assert future is not None and future.state_since == now
    assert alarm_from_raw({"AlarmName": "no-time"}, "eu-west-1", now) is None
    assert alarm_from_raw({"StateUpdatedTimestamp": now}, "eu-west-1", now) is None


def test_a_metric_math_alarm_is_still_an_incident_without_a_metric(now: datetime) -> None:
    raw = raw_alarm(now, MetricName=None, Namespace=None)
    raw.pop("MetricName"), raw.pop("Namespace")
    alarm = alarm_from_raw(raw, "eu-west-1", now)
    assert alarm is not None and alarm.metric_name is None
    assert "not a single metric" in to_detail(alarm).signals[2].value


@pytest.mark.parametrize(
    ("text", "level"),
    [
        ("ERROR connecting to db", "ERROR"),
        ("Task timed out after 3.00 seconds", "ERROR"),
        ("WARN slow response", "WARN"),
        ("debug: cache miss", "DEBUG"),
        ("START RequestId: abc", "INFO"),
    ],
)
def test_a_level_is_read_off_the_text(text: str, level: str) -> None:
    assert infer_level(text) == level


# ---- listing the account's alarms ----------------------------------------------------------------------


def test_the_list_shows_firing_alarms_and_marks_the_role_as_working(
    live: tuple[TestClient, FakeFactory, LiveProvider], pool: FakePool
) -> None:
    client, factory, _ = live
    assert client.get(URL, headers=headers(pool)).json()["verified_at"] is None
    body = client.get(LIVE, headers=headers(pool)).json()
    assert body["data_source"] == "your AWS account (live)" and body["region"] == "eu-west-1"
    assert [i["title"] for i in body["incidents"]] == ["checkout-errors"]
    assert body["incidents"][0]["id"].startswith("LIVE-")
    assert client.get(URL, headers=headers(pool)).json()["verified_at"] is not None
    # The role the account is asked to assume is the one registered, with the user's own ExternalId.
    owner, role, external_id = factory.opened[0]
    assert role == ROLE and external_id.startswith("agentops-")


def test_asking_again_straight_away_does_not_call_the_account_again(
    live: tuple[TestClient, FakeFactory, LiveProvider], pool: FakePool
) -> None:
    client, factory, _ = live
    client.get(LIVE, headers=headers(pool))
    client.get(LIVE, headers=headers(pool))
    assert len(factory.opened) == 1


def test_changing_the_role_clears_the_working_mark(
    live: tuple[TestClient, FakeFactory, LiveProvider], pool: FakePool
) -> None:
    client, _, _ = live
    client.get(LIVE, headers=headers(pool))
    changed = {**SETTINGS, "log_groups": [GROUP]}
    assert client.put(URL, headers=headers(pool), json=changed).json()["verified_at"] is None


def test_nothing_can_be_listed_before_a_role_is_registered(sim: Simulator, pool: FakePool, now: datetime) -> None:
    client, factory, _ = build(sim, pool, FakeReader(now))
    r = client.get(LIVE, headers=headers(pool))
    assert r.status_code == 409 and r.json()["error"]["code"] == "aws_connection_not_set_up"
    client.put(URL, headers=headers(pool), json=SETTINGS)
    assert client.get(LIVE, headers=headers(pool)).status_code == 409
    assert factory.opened == []


def test_a_role_aws_refuses_is_reported_in_plain_words(
    live: tuple[TestClient, FakeFactory, LiveProvider], pool: FakePool
) -> None:
    client, factory, _ = live
    factory.error = AwsRoleUnreachableError("AgentOps could not use the role in your AWS account.")
    r = client.get(LIVE, headers=headers(pool))
    assert r.status_code == 409 and r.json()["error"]["code"] == "aws_role_unreachable"
    assert client.get(URL, headers=headers(pool)).json()["verified_at"] is None


def test_a_visitor_who_is_not_signed_in_cannot_list(
    live: tuple[TestClient, FakeFactory, LiveProvider],
) -> None:
    client, factory, _ = live
    r = client.get(LIVE)
    assert r.status_code == 401 and factory.opened == []


# ---- the tools --------------------------------------------------------------------------------------


def run(client: TestClient, pool: FakePool, iid: str, sub: str = USER_A) -> dict[str, Any]:
    r = client.post(f"/api/incidents/{iid}/investigations", headers=headers(pool, sub))
    assert r.status_code == 202, r.text
    got: dict[str, Any] = client.get(f"/api/investigations/{r.json()['id']}", headers=headers(pool, sub)).json()
    return got


def test_an_investigation_reads_the_account_and_says_so(
    live: tuple[TestClient, FakeFactory, LiveProvider], pool: FakePool, now: datetime
) -> None:
    client, factory, provider = live
    reader = factory.reader
    reader.hits = [
        LogHit(GROUP, "e1", now - timedelta(minutes=20), "ERROR upstream timeout calling payments for bob@example.com")
    ]
    inv = run(client, pool, incident_id(client, pool))
    assert inv["status"] == "completed", inv
    assert inv["simulated"] is False
    assert [t["name"] for t in inv["tool_calls"]] == [
        "get_incident",
        "search_logs",
        "get_service_metrics",
        "get_recent_deployments",
    ]
    assert all(t["status"] == "ok" for t in inv["tool_calls"])
    findings = inv["findings"]
    assert findings["confidence"] == "high" and findings["remediation"] is None
    assert any("read-only role" in lim for lim in findings["limitations"])
    # The groups searched are exactly the ones the user listed, and the term was sent in its usual spellings.
    assert reader.searched[0][0] == (GROUP, "/ecs/payments")
    assert set(reader.searched[0][1]) >= {"timeout", "TIMEOUT", "Timeout"}
    # The model was told this is a real account with no actions, and was offered no action to choose.
    first = provider.requests[0]
    assert "REAL AWS" in first.system and "SIMULATED" not in first.system
    assert first.answer_schema["properties"]["remediation_action_id"]["type"] == "null"


def test_an_action_a_model_names_for_a_real_account_is_ignored_not_offered(
    sim: Simulator, pool: FakePool, now: datetime
) -> None:
    client, _, _ = build(sim, pool, FakeReader(now), LiveProvider(remediation="restart_payments_workers"))
    connect(client, pool)
    inv = run(client, pool, incident_id(client, pool))
    assert inv["status"] == "completed" and inv["findings"]["remediation"] is None
    assert inv["remediation_state"] is None
    assert any("does not offer actions" in lim for lim in inv["findings"]["limitations"])
    assert client.post(f"/api/investigations/{inv['id']}/remediation/approve", headers=headers(pool)).status_code == 409


def test_log_text_is_masked_before_a_model_or_the_screen_sees_it(
    live: tuple[TestClient, FakeFactory, LiveProvider], pool: FakePool, now: datetime
) -> None:
    client, factory, provider = live
    factory.reader.hits = [
        LogHit(GROUP, "e1", now, "timeout for bob@example.com password=hunter2hunter2 key AKIAABCDEFGHIJKLMNOP")
    ]
    inv = run(client, pool, incident_id(client, pool))
    sent = " ".join(m.content for r in provider.requests for m in r.messages)
    assert "[email]" in sent and "[aws-key-id]" in sent
    for secret in ("bob@example.com", "hunter2hunter2", "AKIAABCDEFGHIJKLMNOP"):
        assert secret not in sent and secret not in json.dumps(inv)


def test_a_search_cannot_confirm_a_secret_it_cannot_see(
    live: tuple[TestClient, FakeFactory, LiveProvider], pool: FakePool, now: datetime
) -> None:
    client, factory, _ = live
    factory.reader.hits = [LogHit(GROUP, "e1", now, "login failed for bob@example.com")]
    iid = incident_id(client, pool)
    sim_less = client.app.state.live.diagnostics(USER_A)  # type: ignore[attr-defined]
    from app.schemas import SearchLogsArgs

    found = sim_less.search_logs(SearchLogsArgs(incident_id=iid, query="bob@example.com"))
    assert found.total_matches == 0


def test_a_search_that_could_not_look_everywhere_never_says_nothing_was_found(
    live: tuple[TestClient, FakeFactory, LiveProvider], pool: FakePool
) -> None:
    client, factory, _ = live
    iid = incident_id(client, pool)
    diag = client.app.state.live.diagnostics(USER_A)  # type: ignore[attr-defined]
    from app.schemas import SearchLogsArgs

    complete = diag.search_logs(SearchLogsArgs(incident_id=iid, query="timeout"))
    assert complete.total_matches == 0 and complete.reference_ids and not complete.truncated
    factory.reader.logs_complete = False
    partial = diag.search_logs(SearchLogsArgs(incident_id=iid, query="timeout"))
    assert partial.reference_ids == [] and partial.truncated


def test_metrics_changes_and_other_metric_names(
    live: tuple[TestClient, FakeFactory, LiveProvider], pool: FakePool, now: datetime
) -> None:
    client, factory, _ = live
    factory.reader.changes = [
        Change("ev1", now - timedelta(minutes=50), "UpdateFunctionCode20150331v2", "lambda.amazonaws.com", "dev-role"),
        Change(
            "ev2",
            now - timedelta(minutes=300),
            "UpdateFunctionConfiguration",
            "lambda.amazonaws.com",
            "admin@example.com",
        ),
    ]
    iid = incident_id(client, pool)
    diag = client.app.state.live.diagnostics(USER_A)  # type: ignore[attr-defined]
    from app.errors import MetricNotAvailableError
    from app.schemas import GetDeploymentsArgs, GetMetricsArgs

    metric = diag.get_service_metrics(GetMetricsArgs(incident_id=iid, metric="alarm_metric"))
    assert metric.summary.baseline == 0 and metric.summary.peak == 12 and metric.summary.elevated_fraction > 0.8
    assert metric.reference_ids and metric.reference_ids[0].startswith("metric:alarm_metric:")
    with pytest.raises(MetricNotAvailableError):
        diag.get_service_metrics(GetMetricsArgs(incident_id=iid, metric="error_rate"))
    factory.reader.no_points = True
    with pytest.raises(MetricNotAvailableError):
        diag.get_service_metrics(GetMetricsArgs(incident_id=iid, metric="alarm_metric"))

    changes = diag.get_recent_deployments(GetDeploymentsArgs(incident_id=iid, hours=6))
    assert [d.change_type for d in changes.deployments] == ["config", "code"]
    assert all(d.id.startswith("ct:") for d in changes.deployments)
    assert "admin@example.com" not in json.dumps(changes.model_dump(mode="json"))
    assert changes.deployments[1].minutes_before_incident_start == 5  # made 50 min ago; the window starts 45 min ago


def test_quiet_minutes_on_a_zero_baseline_metric_are_not_elevated(
    live: tuple[TestClient, FakeFactory, LiveProvider], pool: FakePool, now: datetime
) -> None:
    client, factory, _ = live
    iid = incident_id(client, pool)
    diag = client.app.state.live.diagnostics(USER_A)  # type: ignore[attr-defined]
    from app.schemas import GetMetricsArgs

    alarm = next(a for a in client.app.state.live._snapshots.load(USER_A) if a.id == iid)  # type: ignore[attr-defined]
    reader = factory.reader

    def never_fires(a: LiveAlarm, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        return [(start.replace(second=0, microsecond=0), 0.0)]

    reader.metric_points = never_fires  # type: ignore[assignment]
    result = diag.get_service_metrics(GetMetricsArgs(incident_id=iid, metric="alarm_metric"))
    assert alarm.metric_name == "Errors" and result.summary.elevated_fraction == 0


# ---- who may see what ---------------------------------------------------------------------------------


def test_another_user_cannot_see_open_or_investigate_my_alarms(
    live: tuple[TestClient, FakeFactory, LiveProvider], pool: FakePool
) -> None:
    client, factory, _ = live
    iid = incident_id(client, pool)
    for path in (
        f"/api/incidents/{iid}",
        f"/api/incidents/{iid}/timeline",
        f"/api/incidents/{iid}/investigations/latest",
    ):
        assert client.get(path, headers=headers(pool, USER_B)).status_code == 404
    assert client.post(f"/api/incidents/{iid}/investigations", headers=headers(pool, USER_B)).status_code == 409
    assert client.get(LIVE, headers=headers(pool, USER_B)).status_code == 409
    assert client.get(f"/api/incidents/{iid}", headers=headers(pool)).status_code == 200
    assert len(factory.opened) == 1


def test_a_visitor_cannot_open_a_live_incident_and_the_public_diagnostics_never_serve_one(
    live: tuple[TestClient, FakeFactory, LiveProvider], pool: FakePool
) -> None:
    client, _, _ = live
    iid = incident_id(client, pool)
    assert client.get(f"/api/incidents/{iid}").status_code == 404
    for tail in ("logs?query=timeout", "metrics?metric=alarm_metric", "deployments"):
        assert client.get(f"/api/incidents/{iid}/{tail}", headers=headers(pool)).status_code == 422


def test_my_investigations_lists_a_live_one_and_delete_removes_it(
    live: tuple[TestClient, FakeFactory, LiveProvider], pool: FakePool
) -> None:
    client, _, _ = live
    iid = incident_id(client, pool)
    run(client, pool, iid)
    mine = client.get("/api/me/investigations", headers=headers(pool)).json()
    assert [row["incident_id"] for row in mine] == [iid] and mine[0]["incident_title"] == "checkout-errors"
    assert client.delete("/api/me/investigations", headers=headers(pool)).json() == {"deleted": 1}
    assert client.get("/api/me/investigations", headers=headers(pool)).json() == []
    assert client.get(f"/api/incidents/{iid}/investigations/latest", headers=headers(pool)).status_code == 404
    # The connection itself is untouched.
    assert client.get(URL, headers=headers(pool)).json()["status"] == "registered"


# ---- refusals ---------------------------------------------------------------------------------------


def test_the_offline_assistant_cannot_investigate_a_real_account(sim: Simulator, pool: FakePool, now: datetime) -> None:
    client, factory, _ = build(sim, pool, FakeReader(now), LiveProvider(kind="mock"))
    connect(client, pool)
    iid = incident_id(client, pool)  # listing alarms is fine: nothing is analysed
    r = client.post(f"/api/incidents/{iid}/investigations", headers=headers(pool))
    assert r.status_code == 409 and r.json()["error"]["code"] == "live_needs_ai"
    assert client.get(f"/api/incidents/{iid}/investigations/latest", headers=headers(pool)).json() is None


def test_a_role_that_stops_working_refuses_the_start_and_records_nothing(
    live: tuple[TestClient, FakeFactory, LiveProvider], pool: FakePool
) -> None:
    client, factory, _ = live
    iid = incident_id(client, pool)
    factory.error = AwsRoleUnreachableError("AgentOps could not use the role in your AWS account.")
    r = client.post(f"/api/incidents/{iid}/investigations", headers=headers(pool))
    assert r.status_code == 409 and r.json()["error"]["code"] == "aws_role_unreachable"
    assert client.get(f"/api/incidents/{iid}/investigations/latest", headers=headers(pool)).json() is None


def test_consent_to_a_different_wording_blocks_reading(
    live: tuple[TestClient, FakeFactory, LiveProvider], pool: FakePool
) -> None:
    client, factory, _ = live
    store = client.app.state.connections._store  # type: ignore[attr-defined]
    stale = store.get(USER_A).model_copy(update={"consent_version": "1999-01-01"})
    store.put(USER_A, stale)
    r = client.get(LIVE, headers=headers(pool))
    assert r.status_code == 409 and r.json()["error"]["code"] == "consent_out_of_date"
    assert factory.opened == []


def test_the_feature_being_off_hides_every_live_route(sim: Simulator, pool: FakePool) -> None:
    app = create_app(simulator=sim, settings=Settings(storage_mode="memory"), verifier=pool.verifier())
    client = TestClient(app)
    assert client.get(LIVE, headers=headers(pool)).status_code == 409
    assert client.get("/api/incidents/LIVE-0123456789ab", headers=headers(pool)).status_code == 404


def test_deleting_hands_the_store_the_live_incident_ids_too(
    live: tuple[TestClient, FakeFactory, LiveProvider], pool: FakePool
) -> None:
    """DynamoDB finds a user's records through per-incident pointers, so the ids it is given decide what is deleted."""
    client, _, _ = live
    iid = incident_id(client, pool)
    run(client, pool, iid)
    store = client.app.state.investigations._store  # type: ignore[attr-defined]
    seen: list[list[str]] = []
    original = store.delete_for_owner

    def spy(owner: str, incident_ids: list[str]) -> int:
        seen.append(list(incident_ids))
        return int(original(owner, incident_ids))

    store.delete_for_owner = spy
    assert client.delete("/api/me/investigations", headers=headers(pool)).status_code == 200
    assert iid in seen[0] and "INC-1001" in seen[0]


def test_a_fresh_alarms_own_trigger_is_inside_the_window_and_a_slow_period_widens_the_lead(now: datetime) -> None:
    fresh = alarm_from_raw(raw_alarm(now, minutes_ago=1), "eu-west-1", now)
    assert fresh is not None
    started, ended = window_of(fresh)
    assert started < fresh.state_since - timedelta(minutes=4) and ended - started >= timedelta(minutes=5)
    slow = alarm_from_raw(raw_alarm(now, minutes_ago=1, Period=900), "eu-west-1", now)
    assert slow is not None
    assert window_of(slow)[0] == slow.state_since - timedelta(minutes=30)  # two periods of 15 minutes


def test_changes_to_other_services_are_left_out_but_deployment_services_are_kept(
    live: tuple[TestClient, FakeFactory, LiveProvider], pool: FakePool, now: datetime
) -> None:
    client, factory, _ = live
    factory.reader.changes = [
        Change(
            "a", now - timedelta(minutes=30), "UpdateFunctionConfiguration20150331v2", "lambda.amazonaws.com", "dev"
        ),
        Change("b", now - timedelta(minutes=31), "PutBucketPolicy", "s3.amazonaws.com", "dev"),
        Change("c", now - timedelta(minutes=32), "UpdateStack", "cloudformation.amazonaws.com", "dev"),
        Change("d", now - timedelta(minutes=33), "CreateUser", "iam.amazonaws.com", "dev"),
    ]
    iid = incident_id(client, pool)
    diag = client.app.state.live.diagnostics(USER_A)  # type: ignore[attr-defined]
    from app.schemas import GetDeploymentsArgs

    got = diag.get_recent_deployments(GetDeploymentsArgs(incident_id=iid, hours=6))
    assert sorted(d.id for d in got.deployments) == ["ct:a", "ct:c"]  # a Lambda alarm: Lambda and CloudFormation only


def test_an_unknown_namespace_is_not_filtered() -> None:
    from app.live.diagnostics import related_sources

    assert related_sources("MyCompany/Orders") is None and related_sources(None) is None
    assert related_sources("AWS/Lambda") == frozenset(
        {"lambda", "cloudformation", "codedeploy", "codepipeline", "codebuild"}
    )
