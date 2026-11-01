"""Sign-in: an Amazon Cognito user pool with Cognito's hosted sign-in page.

Kept in its own stack, ahead of the backend, so the Lambda can be told which pool to trust and a code deploy can
never replace the pool (and with it every account). The app client, which needs the CloudFront address as its
callback, is created in the frontend stack instead; see frontend_stack.py.

Self sign-up is on, so anyone can try the demo; each person must prove they own the email address. Passwords
are long, MFA with an authenticator app is optional, and Cognito's own email sender is used (about 50 messages a
day, which is plenty for a demo and costs nothing). The first 10,000 monthly active users are free.
"""

from typing import Any

from aws_cdk import Aws, CfnOutput, Fn, RemovalPolicy, Stack, Tags
from aws_cdk import aws_cognito as cognito
from constructs import Construct

from agentops_infra.config import StageConfig


class AuthStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, cfg: StageConfig, **kwargs: Any) -> None:
        # Accounts are data: protect production the way the table is protected.
        super().__init__(scope, construct_id, termination_protection=cfg.is_prod, **kwargs)
        keep = RemovalPolicy.RETAIN if cfg.is_prod else RemovalPolicy.DESTROY

        self.pool = cognito.UserPool(
            self,
            "Users",
            user_pool_name=f"{cfg.prefix}-users",
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True, username=False),
            sign_in_case_sensitive=False,
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
            ),
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            mfa=cognito.Mfa.OPTIONAL,
            mfa_second_factor=cognito.MfaSecondFactor(sms=False, otp=True),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            user_verification=cognito.UserVerificationConfig(
                email_subject="Your AgentOps AI verification code",
                email_body="Your AgentOps AI verification code is {####}. If you did not sign up, ignore this email.",
                email_style=cognito.VerificationEmailStyle.CODE,
            ),
            email=cognito.UserPoolEmail.with_cognito(),
            deletion_protection=cfg.is_prod,
            removal_policy=keep,
        )

        # The hosted page's address must be unique in the region. The first part of this stack's own id is
        # unique per deployment and says nothing about the account, so no account number is ever published.
        stack_uuid = Fn.select(2, Fn.split("/", Aws.STACK_ID))
        self.domain_prefix = f"{cfg.prefix}-{Fn.select(0, Fn.split('-', stack_uuid))}"
        self.domain = self.pool.add_domain(
            "HostedUi", cognito_domain=cognito.CognitoDomainOptions(domain_prefix=self.domain_prefix)
        )
        # From the domain resource itself, so it is right whichever stack reads it (a stack-id token would not be).
        self.hosted_ui_url = self.domain.base_url()

        Tags.of(self).add("Component", "auth")
        CfnOutput(self, "UserPoolId", value=self.pool.user_pool_id)
        CfnOutput(self, "HostedUiUrl", value=self.hosted_ui_url)
