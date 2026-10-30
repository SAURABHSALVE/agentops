"""Lets GitHub Actions deploy without long-lived AWS keys.

GitHub signs a short-lived OIDC token per workflow run; AWS accepts it only when the signed `sub` claim
names this repository and an allowed ref. The roles can do nothing except assume the CDK bootstrap roles,
so what a deploy may change is decided by the bootstrap stack, not by these roles.

Deploy this stack by hand with an admin identity, once. It is deliberately NOT in the CI deploy set: a
pipeline must not be able to rewrite its own trust policy.
"""

from typing import Any

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_iam as iam
from constructs import Construct

BOOTSTRAP_QUALIFIER = "hnb659fds"  # the CDK default; change only if bootstrapped with --qualifier
GITHUB_ISSUER = "token.actions.githubusercontent.com"


class GitHubOidcStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        repo: str,
        existing_provider_arn: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # An account can hold one provider per issuer URL. Reuse it if GitHub is already connected.
        if existing_provider_arn:
            provider: iam.IOidcProvider = iam.OidcProviderNative.from_oidc_provider_arn(
                self, "GitHub", existing_provider_arn
            )
        else:
            provider = iam.OidcProviderNative(
                self, "GitHub", url=f"https://{GITHUB_ISSUER}", client_ids=["sts.amazonaws.com"]
            )

        def bootstrap_role(kind: str) -> str:
            name = f"cdk-{BOOTSTRAP_QUALIFIER}-{kind}-role-{self.account}-{self.region}"
            return f"arn:{self.partition}:iam::{self.account}:role/{name}"

        def github_role(
            logical_id: str, role_name: str, subject: str, description: str, assumes: list[str]
        ) -> iam.Role:
            role = iam.Role(
                self,
                logical_id,
                role_name=role_name,
                description=description,
                max_session_duration=Duration.hours(1),
                assumed_by=iam.OpenIdConnectPrincipal(
                    provider,
                    conditions={
                        "StringEquals": {
                            f"{GITHUB_ISSUER}:aud": "sts.amazonaws.com",
                            f"{GITHUB_ISSUER}:sub": subject,
                        },
                    },
                ),
            )
            role.add_to_policy(
                iam.PolicyStatement(actions=["sts:AssumeRole"], resources=[bootstrap_role(k) for k in assumes])
            )
            return role

        # Deploys: only workflows running on the main branch of this repository.
        self.deploy_role = github_role(
            "DeployRole",
            "agentops-github-deploy-prod",
            f"repo:{repo}:ref:refs/heads/main",
            "GitHub Actions on main: cdk deploy via the CDK bootstrap roles",
            ["deploy", "file-publishing", "lookup"],
        )
        # Pull requests: may read deployed state to run `cdk diff`; cannot deploy or publish assets.
        self.diff_role = github_role(
            "DiffRole",
            "agentops-github-diff",
            f"repo:{repo}:pull_request",
            "GitHub Actions on pull requests: cdk diff only (lookup role)",
            ["lookup"],
        )

        CfnOutput(self, "DeployRoleArn", value=self.deploy_role.role_arn)
        CfnOutput(self, "DiffRoleArn", value=self.diff_role.role_arn)
