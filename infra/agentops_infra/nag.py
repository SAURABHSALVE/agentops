"""cdk-nag (AWS Solutions rules) runs on every synth. Each acknowledgement below says why it is accepted;
anything not listed here fails the build."""

from aws_cdk import Acknowledgment, Token, Validations
from constructs import IConstruct

from agentops_infra.auth_stack import AuthStack
from agentops_infra.backend_stack import BackendStack
from agentops_infra.config import StageConfig
from agentops_infra.data_stack import DataStack
from agentops_infra.frontend_stack import FrontendStack
from agentops_infra.github_oidc_stack import GitHubOidcStack

_BASIC_EXECUTION = (
    "AwsSolutions-IAM4[Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole]"
)


def _ack(scope: IConstruct, rule: str, reason: str) -> None:
    Validations.of(scope).acknowledge(Acknowledgment(id=rule, reason=reason))


def acknowledge_oidc(stack: GitHubOidcStack) -> None:
    # Nothing to acknowledge: the roles carry only sts:AssumeRole on named CDK bootstrap roles.
    del stack


def acknowledge_data(stack: DataStack, cfg: StageConfig) -> None:
    _ack(
        stack.openai_key,
        "AwsSolutions-SMG4",
        "Rotation is not possible for a third-party API key; it is rotated by hand in the OpenAI console.",
    )
    if stack.access_code is not None:
        _ack(
            stack.access_code,
            "AwsSolutions-SMG4",
            "A shared demo access code, rotated by replacing the secret value; automatic rotation adds no safety here.",
        )
    if not cfg.is_prod:
        _ack(
            stack.table, "AwsSolutions-DDB3", "Point-in-time recovery is enabled in prod only; dev data is disposable."
        )


def acknowledge_auth(stack: AuthStack) -> None:
    _ack(
        stack.pool,
        "AwsSolutions-COG2",
        "MFA is optional (authenticator app, no SMS cost). Forcing it would stop a visitor trying the demo; the "
        "accounts hold no data beyond an email address and simulated investigations.",
    )
    _ack(
        stack.pool,
        "AwsSolutions-COG8",
        "The Plus plan (threat protection) is billed per user; the demo relies on long passwords, optional MFA, "
        "Cognito's built-in sign-in throttling and API Gateway throttling instead.",
    )


def acknowledge_backend(stack: BackendStack) -> None:
    _ack(
        stack.function,
        _BASIC_EXECUTION,
        "AWSLambdaBasicExecutionRole is the AWS-managed policy that grants only CloudWatch Logs writes.",
    )
    if stack.role is not None:
        _ack(
            stack.role,
            _BASIC_EXECUTION,
            "AWSLambdaBasicExecutionRole is the AWS-managed policy that grants only CloudWatch Logs writes. "
            "The role is named (not generated) so users' read-only roles can trust it by ARN.",
        )
    if stack.role is not None and stack.connect_any:
        _ack(
            stack.role,
            "AwsSolutions-IAM5[Resource::arn:aws:iam::*:role/AgentOpsReadOnly]",
            "Any user's own account may connect, so the account part of the role ARN cannot be named. Only the fixed "
            "role name AgentOpsReadOnly can be assumed. Each user's role trusts this function's role and only with "
            "that user's own ExternalId (made by the server, never repeated), so one user cannot reach another's.",
        )
    _ack(
        stack.function,
        "AwsSolutions-L1",
        "Python 3.13 is pinned on purpose: it is the version CI tests and the bundle is built for.",
    )
    _ack(
        stack.api,
        "AwsSolutions-APIG4",
        "The gateway lets every route through on purpose: reads are public simulated data. The Lambda verifies the "
        "Cognito token itself, because an API Gateway JWT authorizer cannot make sign-in optional per request.",
    )
    _ack(stack, "AwsSolutions-APIG1", "Access logging is off to avoid log cost; Lambda logs and alarms cover the demo.")


def acknowledge_frontend(stack: FrontendStack) -> None:
    _ack(stack.bucket, "AwsSolutions-S1", "Access logs would cost more than the site; CloudFront is the only reader.")
    _ack(stack.distribution, "AwsSolutions-CFR1", "No geo restriction: a public portfolio site.")
    _ack(
        stack.distribution,
        "AwsSolutions-CFR2",
        "WAF costs more per month than the rest of the stack; the API is throttled instead.",
    )
    _ack(stack.distribution, "AwsSolutions-CFR3", "Access logging is off to avoid log-bucket cost.")
    _ack(
        stack.distribution,
        "AwsSolutions-CFR4",
        "The default *.cloudfront.net certificate fixes the viewer TLS policy; a custom domain would lift it.",
    )
    # CDK-owned helper function behind BucketDeployment: its role, scoped by the construct to this stack's bucket,
    # the CDK asset bucket (read) and CloudFront invalidations (which cannot be scoped to a distribution).
    helper = "the BucketDeployment helper"
    # cdk-nag spells the account as it appears in the template: a placeholder while the account is unknown
    # (tests, `cdk synth` without credentials) but the real number in a deploy. Acknowledge whichever applies.
    account = "<AWS::AccountId>" if Token.is_unresolved(stack.account) else stack.account
    for rule, reason in (
        ("AwsSolutions-L1", f"The runtime of {helper} is chosen by CDK, not by this project."),
        (_BASIC_EXECUTION, f"AWS-managed logs-only policy on {helper}."),
        ("AwsSolutions-IAM5[Action::s3:GetBucket*]", f"Read-only bucket metadata, in {helper}."),
        ("AwsSolutions-IAM5[Action::s3:GetObject*]", f"Read of the deployment asset, in {helper}."),
        ("AwsSolutions-IAM5[Action::s3:List*]", f"Listing the site bucket to prune stale files, in {helper}."),
        ("AwsSolutions-IAM5[Action::s3:Abort*]", f"Multipart cleanup on the site bucket, in {helper}."),
        ("AwsSolutions-IAM5[Action::s3:DeleteObject*]", f"Pruning stale files from the site bucket, in {helper}."),
        (
            f"AwsSolutions-IAM5[Resource::arn:aws:s3:::cdk-hnb659fds-assets-{account}-us-east-1/*]",
            f"Read of this account's CDK asset bucket, in {helper}.",
        ),  # fmt: skip
        ("AwsSolutions-IAM5[Resource::<SiteE53D7754.Arn>/*]", f"Objects of the site bucket only, in {helper}."),
        (
            "AwsSolutions-IAM5[Resource::*]",
            f"cloudfront:CreateInvalidation does not support resource scoping, in {helper}.",
        ),
    ):
        _ack(stack, rule, reason)
