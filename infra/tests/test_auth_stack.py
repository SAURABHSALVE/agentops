"""Sign-in infrastructure: the user pool, its one browser client, and how the backend and site are told about it."""

import json
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from agentops_infra.config import ConfigError, stage_config
from tests.conftest import EMAIL, Built


def only(template: Template, kind: str) -> dict[str, Any]:
    found = template.find_resources(kind)
    assert len(found) == 1, f"expected exactly one {kind}, found {len(found)}"
    return dict(next(iter(found.values())))


def flat(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


# ---- configuration ----------------------------------------------------------------------------


def test_sign_in_is_on_by_default_and_can_be_turned_off() -> None:
    on = stage_config(cdk.App(context={"stage": "dev", "alertEmail": EMAIL}))
    off = stage_config(cdk.App(context={"stage": "dev", "alertEmail": EMAIL, "auth": "none"}))
    assert on.sign_in and not off.sign_in


@pytest.mark.parametrize("bad", ["", "oauth", "COGNITO", "true"])
def test_auth_mode_is_validated(bad: str) -> None:
    ctx = {"stage": "dev", "alertEmail": EMAIL, "auth": bad}
    if bad == "":
        assert stage_config(cdk.App(context=ctx)).sign_in  # an empty value means the default
        return
    with pytest.raises(ConfigError):
        stage_config(cdk.App(context=ctx))


def test_switching_sign_in_off_deploys_none_of_it(open_demo: Built) -> None:
    assert open_demo.auth is None
    open_demo.frontend.resource_count_is("AWS::Cognito::UserPoolClient", 0)
    env = only(open_demo.backend, "AWS::Lambda::Function")["Properties"]["Environment"]["Variables"]
    assert "COGNITO_USER_POOL_ID" not in env


# ---- the user pool ----------------------------------------------------------------------------


def test_the_pool_asks_people_to_prove_their_email_and_use_long_passwords(dev: Built) -> None:
    assert dev.auth is not None
    pool = only(dev.auth, "AWS::Cognito::UserPool")["Properties"]
    assert pool["UsernameAttributes"] == ["email"]
    assert pool["AutoVerifiedAttributes"] == ["email"]
    assert pool["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"] is False  # people can sign themselves up
    policy = pool["Policies"]["PasswordPolicy"]
    assert policy["MinimumLength"] >= 12
    assert all(policy[k] for k in ("RequireLowercase", "RequireUppercase", "RequireNumbers", "RequireSymbols"))
    assert pool["MfaConfiguration"] == "OPTIONAL" and pool["EnabledMfas"] == ["SOFTWARE_TOKEN_MFA"]  # no SMS, no cost
    assert pool["AccountRecoverySetting"]["RecoveryMechanisms"] == [{"Name": "verified_email", "Priority": 1}]


def test_accounts_are_kept_in_prod_and_disposable_in_dev(dev: Built, prod: Built) -> None:
    assert dev.auth is not None and prod.auth is not None
    keep, drop = only(prod.auth, "AWS::Cognito::UserPool"), only(dev.auth, "AWS::Cognito::UserPool")
    assert keep["DeletionPolicy"] == "Retain" and keep["Properties"]["DeletionProtection"] == "ACTIVE"
    assert drop["DeletionPolicy"] == "Delete" and drop["Properties"].get("DeletionProtection") != "ACTIVE"


def test_the_hosted_page_address_is_unique_and_reveals_no_account_number(prod: Built) -> None:
    assert prod.auth is not None
    domain = only(prod.auth, "AWS::Cognito::UserPoolDomain")["Properties"]["Domain"]
    text = flat(domain)
    assert "agentops-prod-" in text and "AWS::StackId" in text  # unique per deployment
    assert "123456789012" not in text


# ---- the browser client -----------------------------------------------------------------------


def _client(built: Built) -> dict[str, Any]:
    props: dict[str, Any] = only(built.frontend, "AWS::Cognito::UserPoolClient")["Properties"]
    return props


def test_the_client_is_a_public_pkce_client_with_the_code_grant_only(dev: Built) -> None:
    c = _client(dev)
    assert c["GenerateSecret"] is False  # a browser cannot keep one
    assert c["AllowedOAuthFlows"] == ["code"] and c["AllowedOAuthFlowsUserPoolClient"] is True  # never implicit
    assert c["AllowedOAuthScopes"] == ["openid", "email"]  # no admin or aws.cognito.signin.user.admin scope
    assert c["SupportedIdentityProviders"] == ["COGNITO"]
    assert c["EnableTokenRevocation"] is True and c["PreventUserExistenceErrors"] == "ENABLED"


def test_the_client_offers_no_plain_password_or_custom_flows(dev: Built) -> None:
    flows = _client(dev)["ExplicitAuthFlows"]
    assert set(flows) == {"ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"}


def test_tokens_are_short_lived(dev: Built) -> None:
    c = _client(dev)
    assert c["TokenValidityUnits"] == {"AccessToken": "minutes", "IdToken": "minutes", "RefreshToken": "minutes"}
    assert c["AccessTokenValidity"] <= 60 and c["IdTokenValidity"] <= 60 and c["RefreshTokenValidity"] <= 24 * 60


def test_production_redirects_only_to_the_https_site(prod: Built) -> None:
    c = _client(prod)
    text = flat([c["CallbackURLs"], c["LogoutURLs"]])
    assert "localhost" not in text and "http://" not in text
    assert len(c["CallbackURLs"]) == 1 and "/auth/callback" in flat(c["CallbackURLs"])
    assert "DomainName" in flat(c["CallbackURLs"])  # the distribution's own address


def test_a_dev_pool_also_accepts_a_local_site(dev: Built) -> None:
    urls = flat(_client(dev)["CallbackURLs"])
    assert "http://localhost:3000/auth/callback" in urls


# ---- wiring -----------------------------------------------------------------------------------


def test_the_lambda_is_told_which_pool_to_trust_and_gets_no_cognito_permission(dev: Built) -> None:
    env = only(dev.backend, "AWS::Lambda::Function")["Properties"]["Environment"]["Variables"]
    assert "COGNITO_USER_POOL_ID" in env
    for resource in dev.backend.find_resources("AWS::IAM::Policy").values():
        for statement in resource["Properties"]["PolicyDocument"]["Statement"]:
            assert "cognito" not in flat(statement.get("Action")).lower()  # it only reads public keys


def test_the_site_learns_the_client_at_deploy_time_not_build_time(dev: Built) -> None:
    deployment = only(dev.frontend, "Custom::CDKBucketDeployment")["Properties"]
    markers = flat(deployment["SourceMarkers"])
    assert "WebClient" in markers  # the client id
    assert len(deployment["SourceObjectKeys"]) == 2  # the built site, plus the generated auth-config.json


def test_a_deployment_without_sign_in_ships_only_the_site(open_demo: Built) -> None:
    deployment = only(open_demo.frontend, "Custom::CDKBucketDeployment")["Properties"]
    assert len(deployment["SourceObjectKeys"]) == 1


def test_the_auth_stack_publishes_the_pool_and_hosted_address(dev: Built) -> None:
    assert dev.auth is not None
    dev.auth.has_output("UserPoolId", Match.any_value())
    dev.auth.has_output("HostedUiUrl", Match.any_value())
