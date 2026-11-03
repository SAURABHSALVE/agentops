"""The role template: read-only, exactly the permissions the screen lists, and impossible to inject into."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.connections.models import ROLE_NAME, AwsConnection, ConnectionSettings, RoleRegistration
from app.connections.permissions import PERMISSIONS, all_actions
from app.connections.template import render_template

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
PRINCIPAL = "arn:aws:iam::111122223333:root"
EXTERNAL_ID = "agentops-abcdefghijklmnopqrstuvwx"

# Written out on purpose: adding a permission means changing this list in the same commit, where a reviewer sees it.
EXPECTED_ACTIONS = {
    "cloudwatch:DescribeAlarms",
    "cloudwatch:DescribeAlarmHistory",
    "cloudwatch:GetMetricData",
    "cloudwatch:ListMetrics",
    "logs:FilterLogEvents",
    "cloudtrail:LookupEvents",
}


def connection(log_groups: tuple[str, ...] = ("/aws/lambda/checkout", "/ecs/payments")) -> AwsConnection:
    return AwsConnection(
        status="draft",
        external_id=EXTERNAL_ID,
        region="eu-west-1",
        log_groups=log_groups,
        consent_version="v",
        consent_provider="mock",
        consented_at=NOW,
        updated_at=NOW,
    )


def statements(template: dict[str, Any]) -> list[dict[str, Any]]:
    role = template["Resources"]["AgentOpsReadOnlyRole"]["Properties"]
    out: list[dict[str, Any]] = role["Policies"][0]["PolicyDocument"]["Statement"]
    return out


def test_the_template_is_json_with_one_role_and_one_output() -> None:
    template = render_template(connection(), principal_arn=PRINCIPAL)
    assert json.loads(json.dumps(template)) == template
    assert template["AWSTemplateFormatVersion"] == "2010-09-09"
    assert set(template["Resources"]) == {"AgentOpsReadOnlyRole"}
    role = template["Resources"]["AgentOpsReadOnlyRole"]
    assert role["Type"] == "AWS::IAM::Role" and role["Properties"]["RoleName"] == ROLE_NAME
    assert set(template["Outputs"]) == {"RoleArn"}
    assert len(role["Properties"]["Policies"]) == 1  # no managed policy attached: nothing broader than what is listed


def test_only_the_stated_principal_with_the_external_id_may_assume_it() -> None:
    trust = render_template(connection(), principal_arn=PRINCIPAL)["Resources"]["AgentOpsReadOnlyRole"]["Properties"][
        "AssumeRolePolicyDocument"
    ]
    assert len(trust["Statement"]) == 1
    only = trust["Statement"][0]
    assert only["Effect"] == "Allow" and only["Action"] == "sts:AssumeRole"
    assert only["Principal"] == {"AWS": PRINCIPAL}
    assert only["Condition"] == {"StringEquals": {"sts:ExternalId": EXTERNAL_ID}}


def test_the_permissions_are_exactly_the_reviewed_read_only_list() -> None:
    granted = {a for s in statements(render_template(connection(), principal_arn=PRINCIPAL)) for a in s["Action"]}
    assert granted == EXPECTED_ACTIONS
    assert all_actions() == EXPECTED_ACTIONS  # the screen the user reads lists the same thing


def test_nothing_in_the_policy_can_change_read_or_widen_anything_else() -> None:
    for s in statements(render_template(connection(), principal_arn=PRINCIPAL)):
        assert s["Effect"] == "Allow"
        assert set(s) == {"Sid", "Effect", "Action", "Resource"}  # no NotAction, NotResource or Condition games
        for action in s["Action"]:
            service, _, verb = action.partition(":")
            assert service in {"cloudwatch", "logs", "cloudtrail"}, action
            assert "*" not in action, action
            assert not verb.startswith(("Put", "Delete", "Create", "Update", "Set", "Tag", "Enable", "Disable")), action
    forbidden = ("s3:", "dynamodb:", "secretsmanager:", "kms:", "ssm:", "iam:", "sts:", "lambda:", "ec2:", "rds:")
    text = json.dumps(statements(render_template(connection(), principal_arn=PRINCIPAL)))
    assert not any(f in text for f in forbidden)


def test_log_search_is_limited_to_the_listed_groups_and_the_rest_cannot_read_logs() -> None:
    template = render_template(connection(), principal_arn=PRINCIPAL)
    by_sid = {s["Sid"]: s for s in statements(template)}
    assert by_sid["LogsSearch"]["Action"] == ["logs:FilterLogEvents"]
    assert by_sid["LogsSearch"]["Resource"] == [
        {"Fn::Sub": "arn:aws:logs:eu-west-1:${AWS::AccountId}:log-group:/aws/lambda/checkout:*"},
        {"Fn::Sub": "arn:aws:logs:eu-west-1:${AWS::AccountId}:log-group:/ecs/payments:*"},
    ]
    # Only these three are account-wide, because AWS offers no narrower form for them; none of them can read a log.
    wide = {sid for sid, s in by_sid.items() if s["Resource"] == "*"}
    assert wide == {"Alarms", "Metrics", "Changes"}
    assert all(not a.startswith("logs:") for sid in wide for a in by_sid[sid]["Action"])


def test_every_permission_in_the_catalogue_is_one_statement_with_a_distinct_id() -> None:
    sids = [s["Sid"] for s in statements(render_template(connection(), principal_arn=PRINCIPAL))]
    assert sids == [p.sid for p in PERMISSIONS] and len(set(sids)) == len(sids)
    assert all(sid.isalnum() for sid in sids)
    assert {p.applies_to for p in PERMISSIONS} == {"account", "log_groups"}


def test_a_different_user_gets_a_different_external_id_in_their_template() -> None:
    other = connection().model_copy(update={"external_id": "agentops-zzzzzzzzzzzzzzzzzzzzzzzz"})
    a = render_template(connection(), principal_arn=PRINCIPAL)
    b = render_template(other, principal_arn=PRINCIPAL)
    assert EXTERNAL_ID in json.dumps(a) and EXTERNAL_ID not in json.dumps(b)


# ---- nothing a user types can reach the template as anything but a name ------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "/aws/lambda/checkout",
        "checkout-service",
        "/ecs/payments",
        "/aws/lambda/My_Func.v2#1",
        "a" * 200,
    ],
)
def test_ordinary_log_group_names_are_accepted(name: str) -> None:
    body = ConnectionSettings(region="us-east-1", log_groups=[name], consent_version="v", accept_data_use=True)
    assert body.log_groups == [name]


@pytest.mark.parametrize(
    "name",
    [
        "*",  # everything
        "**",
        "/*",
        "ab",  # too short to be a deliberate prefix
        "a b",
        "x:y",  # a colon would end the name inside an ARN
        "${AWS::AccountId}",  # would be expanded by Fn::Sub
        'a"b',
        "a\nb",
        "a*b",  # no wildcards: a log group is searched by its exact name
        "/ecs/payments*",
        "/aws/lambda/checkout-*",
        "a**",
        "<script>alert(1)</script>",
        "a" * 201,
        "",
    ],
)
def test_names_that_could_widen_or_break_the_policy_are_refused(name: str) -> None:
    with pytest.raises(ValidationError):
        ConnectionSettings(region="us-east-1", log_groups=[name], consent_version="v", accept_data_use=True)


def test_at_most_ten_groups_and_duplicates_collapse() -> None:
    names = [f"/aws/lambda/fn-{i}" for i in range(11)]
    with pytest.raises(ValidationError):
        ConnectionSettings(region="us-east-1", log_groups=names, consent_version="v", accept_data_use=True)
    twice = ConnectionSettings(
        region="us-east-1", log_groups=["/a/b", "/a/c", "/a/b"], consent_version="v", accept_data_use=True
    )
    assert twice.log_groups == ["/a/b", "/a/c"]


@pytest.mark.parametrize("region", ["us-east-1", "eu-west-2", "ap-southeast-1", "sa-east-1", "il-central-1"])
def test_commercial_regions_are_accepted(region: str) -> None:
    assert ConnectionSettings(region=region, log_groups=["/a/b"], consent_version="v", accept_data_use=True)


@pytest.mark.parametrize(
    "region", ["us-gov-west-1", "cn-north-1", "us-east-1.evil.com", "us-east-1/", "US-EAST-1", "", "east", "us-east-"]
)
def test_other_partitions_and_odd_regions_are_refused(region: str) -> None:
    with pytest.raises(ValidationError):
        ConnectionSettings(region=region, log_groups=["/a/b"], consent_version="v", accept_data_use=True)


def test_the_agreement_must_be_true_and_no_other_field_is_accepted() -> None:
    with pytest.raises(ValidationError):
        ConnectionSettings(region="us-east-1", log_groups=["/a/b"], consent_version="v", accept_data_use=False)
    with pytest.raises(ValidationError):
        ConnectionSettings.model_validate(
            {
                "region": "us-east-1",
                "log_groups": ["/a/b"],
                "consent_version": "v",
                "accept_data_use": True,
                "external_id": "agentops-chosen-by-the-client",
            }
        )


@pytest.mark.parametrize(
    "arn",
    [
        "arn:aws:iam::111122223333:role/Admin",
        "arn:aws:iam::111122223333:role/path/AgentOpsReadOnly",
        "arn:aws:iam::111122223333:role/AgentOpsReadOnly2",
        "arn:aws:iam::111122223333:role/AgentOpsReadOnly\n",
        "arn:aws-us-gov:iam::111122223333:role/AgentOpsReadOnly",
        "arn:aws:iam::11112222333:role/AgentOpsReadOnly",
        "arn:aws:iam::111122223333:user/AgentOpsReadOnly",
        " arn:aws:iam::111122223333:role/AgentOpsReadOnly",
    ],
)
def test_only_the_role_this_template_creates_can_be_registered(arn: str) -> None:
    with pytest.raises(ValidationError):
        RoleRegistration(role_arn=arn)


def test_the_expected_role_arn_is_accepted() -> None:
    assert RoleRegistration(role_arn="arn:aws:iam::111122223333:role/AgentOpsReadOnly")
