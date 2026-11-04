"""The boto3 layer, checked with botocore's Stubber: every request is validated against the AWS service model and
no network call is made. What matters: only read calls are sent, a search term cannot change its own filter, CloudTrail
request details are never read, AWS error text is never passed on, and the role is assumed with the ExternalId."""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from app.connections.models import AwsConnection
from app.errors import AwsReadFailedError, AwsRoleUnreachableError
from app.live.alarms import alarm_from_raw
from app.live.boto_reader import (
    CHANGE_PAGES,
    LOG_PAGES,
    BotoReader,
    BotoReaderFactory,
    _filter_pattern,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
ROLE = "arn:aws:iam::111122223333:role/AgentOpsReadOnly"
OWNER = "11111111-1111-4111-8111-111111111111"
CREDS = {"aws_access_key_id": "testing", "aws_secret_access_key": "testing", "region_name": "eu-west-1"}


def client(service: str) -> Any:
    return boto3.client(service, **CREDS)


def connection() -> AwsConnection:
    return AwsConnection(
        status="registered",
        external_id="agentops-abcdefghijklmnopqrstuvwx",
        region="eu-west-1",
        log_groups=("/aws/lambda/checkout", "/ecs/payments"),
        role_arn=ROLE,
        consent_version="v",
        consent_provider="openai",
        consented_at=NOW,
        updated_at=NOW,
    )


def reader(cw: Any = None, logs: Any = None, trail: Any = None) -> BotoReader:
    return BotoReader(
        "eu-west-1",
        cloudwatch=cw or client("cloudwatch"),
        logs=logs or client("logs"),
        cloudtrail=trail or client("cloudtrail"),
    )


# ---- alarms and metrics -----------------------------------------------------------------------------


def test_only_firing_metric_alarms_are_requested() -> None:
    cw = client("cloudwatch")
    with Stubber(cw) as stub:
        stub.add_response(
            "describe_alarms",
            {"MetricAlarms": [{"AlarmName": "a", "StateValue": "ALARM"}]},
            {"AlarmTypes": ["MetricAlarm"], "StateValue": "ALARM", "MaxRecords": 25},
        )
        assert reader(cw=cw).firing_alarms() == [{"AlarmName": "a", "StateValue": "ALARM"}]
        stub.assert_no_pending_responses()


def test_the_alarms_own_metric_is_read_with_its_statistic_and_period() -> None:
    raw = {
        "AlarmName": "a",
        "StateTransitionedTimestamp": NOW - timedelta(minutes=30),
        "MetricName": "Errors",
        "Namespace": "AWS/Lambda",
        "Statistic": "Sum",
        "Period": 300,
        "Dimensions": [{"Name": "FunctionName", "Value": "checkout"}],
    }
    alarm = alarm_from_raw(raw, "eu-west-1", NOW)
    assert alarm is not None
    cw = client("cloudwatch")
    start, end = NOW - timedelta(hours=1), NOW
    with Stubber(cw) as stub:
        stub.add_response(
            "get_metric_data",
            {
                "MetricDataResults": [
                    {
                        "Id": "m1",
                        "Timestamps": [NOW - timedelta(minutes=10), NOW - timedelta(minutes=20)],
                        "Values": [3.0, 1.0],
                    }
                ]
            },
            {
                "MetricDataQueries": [
                    {
                        "Id": "m1",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": "AWS/Lambda",
                                "MetricName": "Errors",
                                "Dimensions": [{"Name": "FunctionName", "Value": "checkout"}],
                            },
                            "Period": 300,
                            "Stat": "Sum",
                        },
                        "ReturnData": True,
                    }
                ],
                "StartTime": start,
                "EndTime": end,
                "ScanBy": "TimestampAscending",
            },
        )
        points = reader(cw=cw).metric_points(alarm, start, end)
    assert points == [(NOW - timedelta(minutes=20), 1.0), (NOW - timedelta(minutes=10), 3.0)]  # oldest first


def test_the_neighbouring_metrics_are_read_in_one_call_with_the_alarms_dimensions_and_period() -> None:
    from app.live.related import related_specs

    raw = {
        "AlarmName": "a",
        "StateTransitionedTimestamp": NOW - timedelta(minutes=30),
        "MetricName": "Errors",
        "Namespace": "AWS/Lambda",
        "Statistic": "Sum",
        "Period": 300,
        "Dimensions": [{"Name": "FunctionName", "Value": "checkout"}],
    }
    alarm = alarm_from_raw(raw, "eu-west-1", NOW)
    assert alarm is not None
    specs = related_specs("AWS/Lambda", "Errors")
    assert [s.name for s in specs] == ["Invocations", "Throttles", "Duration", "ConcurrentExecutions"]  # not Errors
    start, end = NOW - timedelta(hours=1), NOW
    queries = [
        {
            "Id": f"r{i}",
            "MetricStat": {
                "Metric": {
                    "Namespace": "AWS/Lambda",
                    "MetricName": s.name,
                    "Dimensions": [{"Name": "FunctionName", "Value": "checkout"}],
                },
                "Period": 300,
                "Stat": s.stat,
            },
            "ReturnData": True,
        }
        for i, s in enumerate(specs)
    ]
    cw = client("cloudwatch")
    with Stubber(cw) as stub:
        stub.add_response(
            "get_metric_data",
            {
                "MetricDataResults": [
                    {
                        "Id": "r0",
                        "Timestamps": [NOW - timedelta(minutes=10), NOW - timedelta(minutes=20)],
                        "Values": [9.0, 1.0],
                    },
                    {"Id": "r1", "Timestamps": [], "Values": []},  # Throttles: no data
                    {"Id": "r2", "Timestamps": [NOW - timedelta(minutes=10)], "Values": [250.0]},
                ]
            },
            {
                "MetricDataQueries": queries,
                "StartTime": start,
                "EndTime": end,
                "ScanBy": "TimestampAscending",
            },
        )
        got = reader(cw=cw).related_metric_points(alarm, specs, start, end)
        stub.assert_no_pending_responses()
    assert got == {
        "Invocations": [(NOW - timedelta(minutes=20), 1.0), (NOW - timedelta(minutes=10), 9.0)],
        "Duration": [(NOW - timedelta(minutes=10), 250.0)],
    }  # a metric with no data is absent, not an empty list


def test_a_failing_read_gives_a_fixed_sentence_not_amazons_text() -> None:
    cw = client("cloudwatch")
    with Stubber(cw) as stub:
        stub.add_client_error(
            "describe_alarms", "AccessDenied", "User arn:aws:iam::1:role/x is not authorized on secret-name"
        )
        with pytest.raises(AwsReadFailedError) as denied:
            reader(cw=cw).firing_alarms()
    assert "secret-name" not in denied.value.message and "not allow" in denied.value.message
    with Stubber(cw) as stub:
        stub.add_client_error("describe_alarms", "ThrottlingException", "Rate exceeded")
        with pytest.raises(AwsReadFailedError) as slow:
            reader(cw=cw).firing_alarms()
    assert "slowing down" in slow.value.message


# ---- logs -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("terms", "pattern"),
    [
        (["timeout"], '"timeout"'),
        (["timeout", "TIMEOUT", "Timeout"], '?"TIMEOUT" ?"Timeout" ?"timeout"'),
        (['a" ?"b'], '"a ? b"'),  # a quote cannot end its own quoting or add a clause
        (['x\\"y'], '"x y"'),
        ([""], ""),
        (["   "], ""),
    ],
)
def test_a_search_term_cannot_change_its_own_filter(terms: list[str], pattern: str) -> None:
    got = _filter_pattern(terms)
    assert got == pattern
    assert got.count('"') == 2 * max(1, got.count('?"')) or got == ""


def test_logs_are_searched_in_exactly_the_listed_groups_and_hits_are_oldest_first() -> None:
    logs = client("logs")
    start, end = NOW - timedelta(hours=1), NOW
    t1, t2 = int((NOW - timedelta(minutes=5)).timestamp() * 1000), int((NOW - timedelta(minutes=9)).timestamp() * 1000)
    with Stubber(logs) as stub:
        for group, stamp in (("/aws/lambda/checkout", t1), ("/ecs/payments", t2)):
            stub.add_response(
                "filter_log_events",
                {"events": [{"timestamp": stamp, "message": f"timeout in {group}", "eventId": f"id{stamp}"}]},
                {
                    "logGroupName": group,
                    "startTime": int(start.timestamp() * 1000),
                    "endTime": int(end.timestamp() * 1000),
                    "filterPattern": '"timeout"',
                    "limit": 50,
                },
            )
        scan = reader(logs=logs).search_logs(("/aws/lambda/checkout", "/ecs/payments"), start, end, ["timeout"], 50)
    assert [h.group for h in scan.hits] == ["/ecs/payments", "/aws/lambda/checkout"] and scan.complete


class FakeLogs:
    """Answers per group, so the test does not depend on which worker thread asks first."""

    def __init__(self, missing: set[str]) -> None:
        self.missing = missing

    def filter_log_events(self, **kwargs: Any) -> dict[str, Any]:
        if kwargs["logGroupName"] in self.missing:
            raise ClientError(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "no such group /typo"}}, "FilterLogEvents"
            )
        return {"events": []}


def test_a_group_that_cannot_be_read_makes_the_scan_incomplete_and_all_failing_is_an_error() -> None:
    start, end = NOW - timedelta(hours=1), NOW
    partial = BotoReader("eu-west-1", cloudwatch=None, logs=FakeLogs({"/typo"}), cloudtrail=None)
    scan = partial.search_logs(("/ok", "/typo"), start, end, ["x"], 50)
    assert scan.hits == [] and scan.complete is False
    broken = BotoReader("eu-west-1", cloudwatch=None, logs=FakeLogs({"/typo"}), cloudtrail=None)
    with pytest.raises(AwsReadFailedError) as err:
        broken.search_logs(("/typo",), start, end, ["x"], 50)
    assert "None of the log groups" in err.value.message and "/typo" not in err.value.message


def test_a_group_with_more_pages_is_read_a_bounded_number_of_times() -> None:
    logs = client("logs")
    with Stubber(logs) as stub:
        for _ in range(LOG_PAGES):
            stub.add_response("filter_log_events", {"events": [], "nextToken": "more"})
        scan = reader(logs=logs).search_logs(("/g",), NOW - timedelta(hours=1), NOW, ["x"], 50)
        stub.assert_no_pending_responses()
    assert scan.complete is False  # stopped by the cap, so "nothing found" would not be a safe claim


# ---- changes ------------------------------------------------------------------------------------------


def test_only_five_fields_of_a_cloudtrail_event_are_read_and_noise_is_dropped() -> None:
    trail = client("cloudtrail")
    poison = '{"requestParameters": {"password": "hunter2"}}'
    events = [
        {
            "EventId": "e1",
            "EventName": "UpdateFunctionCode20150331v2",
            "EventSource": "lambda.amazonaws.com",
            "EventTime": NOW - timedelta(minutes=30),
            "Username": "dev-role",
            "CloudTrailEvent": poison,
            "Resources": [{"ResourceType": "AWS::Lambda::Function", "ResourceName": "secret-resource"}],
        },
        {
            "EventId": "e2",
            "EventName": "AssumeRole",
            "EventSource": "sts.amazonaws.com",
            "EventTime": NOW,
            "Username": "agentops",
        },
        {
            "EventId": "e3",
            "EventName": "PutRolePolicy",
            "EventSource": "iam.amazonaws.com",
            "EventTime": NOW - timedelta(minutes=5),
        },
    ]
    with Stubber(trail) as stub:
        stub.add_response(
            "lookup_events",
            {"Events": events},
            {
                "LookupAttributes": [{"AttributeKey": "ReadOnly", "AttributeValue": "false"}],
                "StartTime": NOW - timedelta(hours=6),
                "EndTime": NOW,
                "MaxResults": 50,
            },
        )
        scan = reader(trail=trail).recent_changes(NOW - timedelta(hours=6), NOW, 25)
    assert [(c.event_id, c.actor) for c in scan.changes] == [("e3", "unknown"), ("e1", "dev-role")]  # newest first
    dumped = repr(scan)
    assert "hunter2" not in dumped and "secret-resource" not in dumped and scan.complete


def test_change_lookup_is_bounded() -> None:
    trail = client("cloudtrail")
    with Stubber(trail) as stub:
        for _ in range(CHANGE_PAGES):
            stub.add_response("lookup_events", {"Events": [], "NextToken": "t"})
        scan = reader(trail=trail).recent_changes(NOW - timedelta(hours=6), NOW, 25)
        stub.assert_no_pending_responses()
    assert scan.complete is False


# ---- assuming the role ------------------------------------------------------------------------------


class Clients:
    """Hands out stubbed clients and remembers which services were asked for."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str, str]] = []

    def __call__(self, service: str, region: str, creds: dict[str, Any]) -> Any:
        self.asked.append((service, region, creds["AccessKeyId"]))
        return client(service)


def sts_response(expires: datetime) -> dict[str, Any]:
    return {
        "Credentials": {
            "AccessKeyId": "ASIATESTTESTTESTTEST",
            "SecretAccessKey": "s" * 20,
            "SessionToken": "t" * 20,
            "Expiration": expires,
        },
        "AssumedRoleUser": {
            "AssumedRoleId": "AROATEST:agentops",
            "Arn": "arn:aws:sts::111122223333:assumed-role/AgentOpsReadOnly/agentops",
        },
    }


def test_the_role_is_assumed_with_the_users_external_id_for_fifteen_minutes() -> None:
    sts = client("sts")
    clients = Clients()
    session = "agentops-" + hashlib.sha256(OWNER.encode()).hexdigest()[:16]
    with Stubber(sts) as stub:
        stub.add_response(
            "assume_role",
            sts_response(NOW + timedelta(minutes=15)),
            {
                "RoleArn": ROLE,
                "RoleSessionName": session,
                "ExternalId": "agentops-abcdefghijklmnopqrstuvwx",
                "DurationSeconds": 900,
            },
        )
        factory = BotoReaderFactory(sts=sts, client_factory=clients, clock=lambda: NOW.timestamp())
        factory.reader_for(OWNER, connection())
        stub.assert_no_pending_responses()
    assert OWNER not in session  # the user's id is hashed before it reaches their CloudTrail
    assert {s for s, _, _ in clients.asked} == {"cloudwatch", "logs", "cloudtrail"}
    assert {r for _, r, _ in clients.asked} == {"eu-west-1"}


def test_credentials_are_reused_until_shortly_before_they_expire() -> None:
    sts = client("sts")
    now = [NOW.timestamp()]
    with Stubber(sts) as stub:
        stub.add_response("assume_role", sts_response(NOW + timedelta(minutes=15)))
        stub.add_response("assume_role", sts_response(NOW + timedelta(minutes=40)))
        factory = BotoReaderFactory(sts=sts, client_factory=Clients(), clock=lambda: now[0])
        factory.reader_for(OWNER, connection())
        now[0] += 600  # ten minutes later: still good
        factory.reader_for(OWNER, connection())
        assert len(stub._queue) == 1  # noqa: SLF001 - one response left unused
        now[0] += 200  # within two minutes of expiry: fetched again
        factory.reader_for(OWNER, connection())
        stub.assert_no_pending_responses()


def test_a_different_user_never_reuses_someone_elses_credentials() -> None:
    sts = client("sts")
    with Stubber(sts) as stub:
        for _ in range(2):
            stub.add_response("assume_role", sts_response(NOW + timedelta(minutes=15)))
        factory = BotoReaderFactory(sts=sts, client_factory=Clients(), clock=lambda: NOW.timestamp())
        factory.reader_for(OWNER, connection())
        factory.reader_for("22222222-2222-4222-8222-222222222222", connection())
        stub.assert_no_pending_responses()


def test_a_role_that_cannot_be_assumed_says_how_to_fix_it_without_amazons_text() -> None:
    sts = client("sts")
    with Stubber(sts) as stub:
        stub.add_client_error(
            "assume_role", "AccessDenied", "User: arn:aws:sts::999:assumed-role/Secret is not authorized"
        )
        factory = BotoReaderFactory(sts=sts, client_factory=Clients())
        with pytest.raises(AwsRoleUnreachableError) as err:
            factory.reader_for(OWNER, connection())
    assert "CloudFormation stack" in err.value.message and "Secret" not in err.value.message
    with Stubber(sts) as stub:
        stub.add_client_error("assume_role", "Throttling", "slow down")
        with pytest.raises(AwsReadFailedError):
            BotoReaderFactory(sts=sts, client_factory=Clients()).reader_for(OWNER, connection())


def test_nothing_in_the_reader_can_write() -> None:
    """The reader's source calls only these methods on any client; a write verb appearing there fails this test."""
    import re
    from pathlib import Path

    source = (Path(__file__).parent.parent / "app" / "live" / "boto_reader.py").read_text(encoding="utf-8")
    called = set(re.findall(r"self\._(?:cloudwatch|logs|cloudtrail)\.(\w+)\(", source)) | set(
        re.findall(r"_sts_client\(\)\.(\w+)\(", source)
    )
    assert called == {"describe_alarms", "get_metric_data", "filter_log_events", "lookup_events", "assume_role"}
