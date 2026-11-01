"""What the templates must guarantee: security posture, stage differences, and the wiring between stacks."""

import json
import re
from pathlib import Path
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from agentops_infra.config import ConfigError, github_repo, github_subject_prefix, stage_config
from tests.conftest import ACCOUNT, EMAIL, REPO, SUBJECT_PREFIX, Built


def only(template: Template, kind: str) -> dict[str, Any]:
    found = template.find_resources(kind)
    assert len(found) == 1, f"expected exactly one {kind}, found {len(found)}"
    return dict(next(iter(found.values())))


def statements(template: Template) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for resource in template.find_resources("AWS::IAM::Policy").values():
        out += list(resource["Properties"]["PolicyDocument"]["Statement"])
    return out


# ---- configuration ---------------------------------------------------------------------------------


def _ctx(**ctx: str) -> cdk.App:
    return cdk.App(context=ctx)


def test_stage_must_be_chosen_explicitly() -> None:
    for bad in ({}, {"stage": "staging"}, {"stage": ""}):
        with pytest.raises(ConfigError):
            stage_config(_ctx(alertEmail=EMAIL, **bad))


@pytest.mark.parametrize("email", ["", "not-an-email", "a@b", "two words@example.com"])
def test_alert_email_is_required_and_validated(email: str) -> None:
    with pytest.raises(ConfigError):
        stage_config(_ctx(stage="dev", alertEmail=email))


@pytest.mark.parametrize("limit", ["0", "-5", "ten", "1000000"])
def test_budget_must_be_a_sane_whole_number(limit: str) -> None:
    with pytest.raises(ConfigError):
        stage_config(_ctx(stage="dev", alertEmail=EMAIL, budgetLimitUsd=limit))


def test_repository_must_look_like_owner_slash_name() -> None:
    assert github_repo(_ctx(githubRepo=REPO)) == REPO
    for bad in ("", "no-slash", "a/b/c", "a b/c"):
        with pytest.raises(ConfigError):
            github_repo(_ctx(githubRepo=bad))


def test_subject_prefix_defaults_to_the_name_form_and_accepts_the_immutable_form_for_the_same_repo() -> None:
    assert github_subject_prefix(_ctx(githubRepo=REPO)) == f"repo:{REPO}"
    assert github_subject_prefix(_ctx(githubRepo=REPO, githubSubjectPrefix=SUBJECT_PREFIX)) == SUBJECT_PREFIX


@pytest.mark.parametrize(
    "prefix",
    [
        "repo:someone-else@1/agentops@2",  # a different owner
        "repo:SAURABHSALVE@90780335/other@2",  # a different repository
        "SAURABHSALVE@90780335/agentops@1437085899",  # missing the repo: scheme
        "repo:SAURABHSALVE@90780335/agentops@1437085899:ref:refs/heads/main",  # a full subject, not a prefix
        "repo:SAURABHSALVE@*/agentops@*",  # wildcards would trust more than one repository
        "repo:SAURABHSALVE@abc/agentops@def",  # ids are numeric
    ],
)
def test_a_subject_prefix_for_another_repository_or_with_wildcards_is_refused(prefix: str) -> None:
    with pytest.raises(ConfigError):
        github_subject_prefix(_ctx(githubRepo=REPO, githubSubjectPrefix=prefix))


def test_the_committed_cdk_json_trusts_the_repositorys_immutable_subject() -> None:
    cfg = json.loads((Path(__file__).resolve().parents[1] / "cdk.json").read_text(encoding="utf-8"))["context"]
    assert cfg["githubRepo"] == REPO and cfg["githubSubjectPrefix"] == SUBJECT_PREFIX


# ---- data ------------------------------------------------------------------------------------------


def test_table_shape_matches_what_the_store_expects(dev: Built) -> None:
    dev.data.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TableName": "agentops-dev-investigations",
            "KeySchema": [{"AttributeName": "pk", "KeyType": "HASH"}],
            "BillingMode": "PAY_PER_REQUEST",
            "TimeToLiveSpecification": {"AttributeName": "expires_at", "Enabled": True},
        },
    )


def test_prod_data_is_protected_and_dev_data_is_disposable(dev: Built, prod: Built) -> None:
    table = only(prod.data, "AWS::DynamoDB::Table")
    assert table["DeletionPolicy"] == "Retain" and table["Properties"]["DeletionProtectionEnabled"] is True
    assert table["Properties"]["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"] is True
    assert only(prod.data, "AWS::SecretsManager::Secret")["DeletionPolicy"] == "Retain"

    table = only(dev.data, "AWS::DynamoDB::Table")
    assert table["DeletionPolicy"] == "Delete" and table["Properties"]["DeletionProtectionEnabled"] is False
    assert only(dev.data, "AWS::SecretsManager::Secret")["DeletionPolicy"] == "Delete"


def test_the_secret_slot_holds_only_a_placeholder(dev: Built) -> None:
    secret = only(dev.data, "AWS::SecretsManager::Secret")["Properties"]
    assert secret["SecretString"] == "REPLACE_ME_set_by_hand"
    assert not any(k in json.dumps(dev.data.to_json()) for k in ("sk-", "OPENAI_API_KEY="))


# ---- backend ---------------------------------------------------------------------------------------


def test_lambda_runtime_architecture_and_timeout(dev: Built) -> None:
    props = next(
        r["Properties"]
        for r in dev.backend.find_resources("AWS::Lambda::Function").values()
        if r["Properties"].get("Handler") == "app.lambda_handler.handler"
    )
    assert props["Runtime"] == "python3.13" and props["Architectures"] == ["arm64"]
    assert props["Timeout"] == 29, "must stay under API Gateway's 30 second integration limit"
    env = props["Environment"]["Variables"]
    assert env["STORAGE_MODE"] == "dynamodb" and env["AGENT_PROVIDER"] == "mock"


def test_lambda_may_only_make_the_four_store_calls_on_one_table(dev: Built) -> None:
    dynamo = [s for s in statements(dev.backend) if any(a.startswith("dynamodb:") for a in s["Action"])]
    assert len(dynamo) == 1
    assert sorted(dynamo[0]["Action"]) == [
        "dynamodb:DeleteItem",
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
    ]
    assert dynamo[0]["Effect"] == "Allow" and dynamo[0]["Resource"] != "*"


def test_no_policy_grants_wildcard_actions_or_touches_the_secret(dev: Built) -> None:
    for stack in (dev.backend, dev.frontend, dev.monitoring):
        for s in statements(stack):
            actions = s["Action"] if isinstance(s["Action"], list) else [s["Action"]]
            assert "*" not in actions, s
            assert not any(a.startswith("secretsmanager:") for a in actions), s


def test_api_is_throttled_and_uses_the_default_stage(dev: Built) -> None:
    dev.backend.has_resource_properties(
        "AWS::ApiGatewayV2::Stage",
        {
            "StageName": "$default",
            "AutoDeploy": True,
            "DefaultRouteSettings": {"ThrottlingRateLimit": 10, "ThrottlingBurstLimit": 20},
        },
    )


def test_log_retention_is_bounded_and_longer_in_prod(dev: Built, prod: Built) -> None:
    assert only(dev.backend, "AWS::Logs::LogGroup")["Properties"]["RetentionInDays"] == 7
    assert only(prod.backend, "AWS::Logs::LogGroup")["Properties"]["RetentionInDays"] == 30


# ---- frontend --------------------------------------------------------------------------------------


def test_bucket_is_private_encrypted_and_https_only(dev: Built) -> None:
    dev.frontend.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "BucketEncryption": Match.object_like({}),
        },
    )
    policy = json.dumps(dev.frontend.find_resources("AWS::S3::BucketPolicy"))
    assert "aws:SecureTransport" in policy and "cloudfront.amazonaws.com" in policy
    assert '"Principal": "*"' not in policy.replace(" ", ""), "no anonymous principal"


def test_cloudfront_serves_the_site_from_s3_with_oac_and_routes_the_api(dev: Built) -> None:
    dev.frontend.resource_count_is("AWS::CloudFront::OriginAccessControl", 1)
    cfg = only(dev.frontend, "AWS::CloudFront::Distribution")["Properties"]["DistributionConfig"]
    assert cfg["DefaultRootObject"] == "index.html" and cfg["HttpVersion"] == "http2and3"
    assert cfg["DefaultCacheBehavior"]["ViewerProtocolPolicy"] == "redirect-to-https"
    behaviors = {b["PathPattern"]: b for b in cfg["CacheBehaviors"]}
    assert set(behaviors) == {"/api/*", "/health"}
    for b in behaviors.values():
        assert "POST" in b["AllowedMethods"], "starting an investigation is a POST"
        assert b["ViewerProtocolPolicy"] == "redirect-to-https"
    codes = {(e["ErrorCode"], e["ResponseCode"]) for e in cfg["CustomErrorResponses"]}
    assert codes == {(403, 404), (404, 404)}


def test_security_headers_are_attached(dev: Built) -> None:
    dev.frontend.has_resource_properties(
        "AWS::CloudFront::ResponseHeadersPolicy",
        {
            "ResponseHeadersPolicyConfig": Match.object_like(
                {
                    "SecurityHeadersConfig": Match.object_like(
                        {
                            "ContentTypeOptions": {"Override": True},
                            "FrameOptions": {"FrameOption": "DENY", "Override": True},
                            "StrictTransportSecurity": Match.object_like({"AccessControlMaxAgeSec": 31536000}),
                        }
                    )
                }
            )
        },
    )


# ---- monitoring ------------------------------------------------------------------------------------


def test_every_alarm_notifies_the_topic_and_missing_data_is_not_an_outage(dev: Built) -> None:
    alarms = dev.monitoring.find_resources("AWS::CloudWatch::Alarm")
    assert {r["Properties"]["AlarmName"] for r in alarms.values()} == {
        "agentops-dev-LambdaErrors",
        "agentops-dev-LambdaThrottles",
        "agentops-dev-Api5xx",
        "agentops-dev-LambdaDurationNearTimeout",
    }
    for r in alarms.values():
        assert r["Properties"]["TreatMissingData"] == "notBreaching"
        assert r["Properties"]["AlarmActions"] and r["Properties"]["OKActions"]


def test_duration_alarm_fires_before_the_timeout(dev: Built) -> None:
    dev.monitoring.has_resource_properties(
        "AWS::CloudWatch::Alarm", {"AlarmName": "agentops-dev-LambdaDurationNearTimeout", "Threshold": 23200}
    )


def test_email_subscription_and_budget(dev: Built) -> None:
    dev.monitoring.has_resource_properties("AWS::SNS::Subscription", {"Protocol": "email", "Endpoint": EMAIL})
    budget = only(dev.monitoring, "AWS::Budgets::Budget")["Properties"]
    assert budget["Budget"]["BudgetLimit"] == {"Amount": 10, "Unit": "USD"}
    assert len(budget["NotificationsWithSubscribers"]) == 3


# ---- GitHub OIDC -----------------------------------------------------------------------------------


def _role(oidc: Template, name: str) -> dict[str, Any]:
    return next(
        r["Properties"]
        for r in oidc.find_resources("AWS::IAM::Role").values()
        if r["Properties"].get("RoleName") == name
    )


def test_the_provider_trusts_only_the_sts_audience(oidc: Template) -> None:
    oidc.has_resource_properties(
        "AWS::IAM::OIDCProvider",
        {"Url": "https://token.actions.githubusercontent.com", "ClientIdList": ["sts.amazonaws.com"]},
    )


def test_deploy_role_trusts_exactly_one_repository_and_the_main_branch(oidc: Template) -> None:
    trust = _role(oidc, "agentops-github-deploy-prod")["AssumeRolePolicyDocument"]["Statement"]
    assert len(trust) == 1 and trust[0]["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert trust[0]["Condition"]["StringEquals"] == {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": f"{SUBJECT_PREFIX}:ref:refs/heads/main",
    }
    assert "StringLike" not in trust[0]["Condition"], "no wildcard matching on the subject"


def test_diff_role_is_limited_to_pull_requests_and_the_lookup_role(oidc: Template) -> None:
    role = _role(oidc, "agentops-github-diff")
    sub = role["AssumeRolePolicyDocument"]["Statement"][0]["Condition"]["StringEquals"]
    assert sub["token.actions.githubusercontent.com:sub"] == f"{SUBJECT_PREFIX}:pull_request"
    (policy,) = [
        p for p in oidc.find_resources("AWS::IAM::Policy").values() if "Diff" in json.dumps(p["Properties"]["Roles"])
    ]
    (statement,) = policy["Properties"]["PolicyDocument"]["Statement"]
    assert statement["Action"] == "sts:AssumeRole"
    assert "lookup-role" in json.dumps(statement["Resource"]) and "deploy-role" not in json.dumps(statement["Resource"])


def test_roles_can_only_assume_cdk_bootstrap_roles_in_this_account(oidc: Template) -> None:
    for policy in oidc.find_resources("AWS::IAM::Policy").values():
        for s in policy["Properties"]["PolicyDocument"]["Statement"]:
            assert s["Action"] == "sts:AssumeRole"
            resources = s["Resource"] if isinstance(s["Resource"], list) else [s["Resource"]]
            for r in resources:
                text = json.dumps(r)
                assert "role/cdk-hnb659fds-" in text and "*" not in text
    for name in ("agentops-github-deploy-prod", "agentops-github-diff"):
        assert _role(oidc, name)["MaxSessionDuration"] == 3600


def test_an_existing_provider_can_be_reused() -> None:
    from aws_cdk.assertions import Template as T

    from agentops_infra.github_oidc_stack import GitHubOidcStack
    from tests.conftest import ENV

    arn = f"arn:aws:iam::{ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"
    template = T.from_stack(
        GitHubOidcStack(cdk.App(), "Reuse", subject_prefix=SUBJECT_PREFIX, existing_provider_arn=arn, env=ENV)
    )
    template.resource_count_is("AWS::IAM::OIDCProvider", 0)
    template.resource_count_is("AWS::IAM::Role", 2)


def test_no_aws_account_id_is_written_down_in_the_source() -> None:
    root = Path(__file__).resolve().parent.parent
    files = [*root.glob("*.py"), *root.glob("agentops_infra/*.py"), root / "cdk.json"]
    offenders = [f.name for f in files if re.search(r"(?<!\d)\d{12}(?!\d)", f.read_text(encoding="utf-8"))]
    assert offenders == [], "the account comes from the deploying credentials (CDK_DEFAULT_ACCOUNT)"


# ---- live model mode (opt-in: -c agentProvider=openai) -----------------------------------------------


def lambda_env(built: Built) -> dict[str, str]:
    props = next(
        r["Properties"]
        for r in built.backend.find_resources("AWS::Lambda::Function").values()
        if r["Properties"].get("Handler") == "app.lambda_handler.handler"
    )
    return dict(props["Environment"]["Variables"])


def test_default_deployment_has_no_live_model_access_code_or_secret_access(dev: Built) -> None:
    env = lambda_env(dev)
    assert env["AGENT_PROVIDER"] == "mock"
    assert not any(k in env for k in ("OPENAI_SECRET_ARN", "ACCESS_SECRET_ARN", "OPENAI_MODEL"))
    assert len(dev.data.find_resources("AWS::SecretsManager::Secret")) == 1


def test_live_mode_adds_an_access_code_and_read_only_access_to_exactly_two_secrets(live: Built) -> None:
    assert len(live.data.find_resources("AWS::SecretsManager::Secret")) == 2
    env = lambda_env(live)
    assert env["AGENT_PROVIDER"] == "openai" and env["OPENAI_MODEL"] == "gpt-4.1-mini"
    assert {"OPENAI_SECRET_ARN", "ACCESS_SECRET_ARN"} <= set(env)
    assert int(env["OPENAI_TIMEOUT_SECONDS"]) < 29, "the model call must give up before the Lambda does"
    assert (env["INVESTIGATIONS_PER_MINUTE"], env["INVESTIGATIONS_PER_HOUR"]) == ("6", "60")

    secret_statements = [
        s
        for s in statements(live.backend)
        if any(
            a.startswith("secretsmanager:") for a in (s["Action"] if isinstance(s["Action"], list) else [s["Action"]])
        )
    ]
    assert secret_statements, "the function must be able to read the secrets"
    for s in secret_statements:
        actions = s["Action"] if isinstance(s["Action"], list) else [s["Action"]]
        assert set(actions) <= {"secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"}, s
        assert s["Effect"] == "Allow" and s["Resource"] != "*", s
        assert len(s["Resource"]) >= 1 if isinstance(s["Resource"], list) else True


def test_the_access_code_is_generated_by_secrets_manager_not_written_in_the_template(live: Built) -> None:
    generated = [
        r["Properties"]
        for r in live.data.find_resources("AWS::SecretsManager::Secret").values()
        if "GenerateSecretString" in r["Properties"]
    ]
    assert len(generated) == 1 and generated[0]["GenerateSecretString"]["PasswordLength"] >= 24
    assert generated[0]["GenerateSecretString"]["ExcludePunctuation"] is True
    assert "demo-code" not in json.dumps(live.data.to_json())


def test_agent_provider_and_model_are_validated() -> None:
    assert stage_config(_ctx(stage="dev", alertEmail=EMAIL)).agent_provider == "mock"
    live_cfg = stage_config(_ctx(stage="dev", alertEmail=EMAIL, agentProvider="openai", openaiModel="gpt-4.1-mini"))
    assert live_cfg.live and live_cfg.openai_model == "gpt-4.1-mini"
    for bad in ({"agentProvider": "bedrock"}, {"agentProvider": "openai", "openaiModel": "../x"}):
        with pytest.raises(ConfigError):
            stage_config(_ctx(stage="dev", alertEmail=EMAIL, **bad))
