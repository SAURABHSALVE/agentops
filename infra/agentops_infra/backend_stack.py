"""FastAPI on Lambda behind an API Gateway HTTP API."""

from pathlib import Path
from typing import Any

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack, Tags
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

from agentops_infra.auth_stack import AuthStack
from agentops_infra.config import StageConfig
from agentops_infra.data_stack import DataStack
from agentops_infra.lambda_bundling import ARCHITECTURE, RUNTIME, backend_code

# Sized for the mock provider, which answers in milliseconds. API Gateway cuts a request off at 30s, so the
# function must finish first. Live OpenAI calls (Phase 3B) need an asynchronous design; see DECISIONS.md D-023.
LAMBDA_TIMEOUT = Duration.seconds(29)
# The role a user creates in their own account (backend/app/connections/models.py ROLE_NAME).
ROLE_NAME = "AgentOpsReadOnly"
# Reads are open and writes need sign-in, but still cap what a stranger can do to the bill. Requests/second, per stage.
THROTTLE_RATE, THROTTLE_BURST = 25, 50


class BackendStack(Stack):
    @staticmethod
    def _environment(
        cfg: StageConfig, data: DataStack, auth: AuthStack | None, account: str, role: iam.IRole | None
    ) -> dict[str, str]:
        env = {
            "STORAGE_MODE": "dynamodb",
            "DYNAMODB_TABLE": data.table.table_name,
            "AGENT_PROVIDER": "mock",
            "STAGE": cfg.stage,
            # One person's share. Per Lambda container, like the other backstops, so not a global cap.
            "USER_INVESTIGATIONS_PER_MINUTE": "5",
            "USER_INVESTIGATIONS_PER_HOUR": "40",
        }
        if cfg.live and data.access_code is not None:
            env.update(
                {
                    "AGENT_PROVIDER": "openai",
                    "OPENAI_MODEL": cfg.openai_model,
                    "OPENAI_SECRET_ARN": data.openai_key.secret_arn,
                    # A live run takes 6 to 12 s; give up well before the 29 s Lambda limit instead of hanging.
                    "OPENAI_TIMEOUT_SECONDS": "12",
                    # Backstops behind the API throttle; per Lambda container, so they are not a global cap.
                    "INVESTIGATIONS_PER_MINUTE": "6",
                    "INVESTIGATIONS_PER_HOUR": "60",
                    "USER_INVESTIGATIONS_PER_MINUTE": "3",  # a real model costs money: one person gets less
                    "USER_INVESTIGATIONS_PER_HOUR": "20",
                    # Counted in the database, so they hold across every Lambda container (the ones above do not).
                    "USER_INVESTIGATIONS_PER_DAY": str(cfg.user_daily_cap),
                    "INVESTIGATIONS_PER_DAY": str(cfg.daily_cap),
                }
            )
        if cfg.live and data.access_code is not None and cfg.require_access_code:
            env["ACCESS_SECRET_ARN"] = data.access_code.secret_arn
        if auth is not None:
            # Which pool's tokens to trust. The Lambda only reads the pool's public keys, so it needs no permission.
            env["COGNITO_USER_POOL_ID"] = auth.pool.user_pool_id
        if role is not None:
            # Switches the feature on (it also needs sign-in, which the config check above guarantees). The user's role
            # trusts exactly this function's role, not the whole account, and only the listed accounts may register.
            env["AWS_CONNECT_ACCOUNT_ID"] = account
            env["AWS_CONNECT_PRINCIPAL_ARN"] = role.role_arn
            if cfg.connect_accounts:
                env["AWS_CONNECT_ALLOWED_ACCOUNTS"] = ",".join(cfg.connect_accounts)
        return env

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cfg: StageConfig,
        data: DataStack,
        backend_dir: Path,
        auth: AuthStack | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        log_group = logs.LogGroup(
            self,
            "ApiLogs",
            retention=logs.RetentionDays.ONE_MONTH if cfg.is_prod else logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.log_group = log_group
        # With the account-connect feature on, the function runs as a role with a fixed name: its ARN goes into the
        # trust policy of every user's read-only role, so it must not change when the function is replaced. Otherwise
        # the function keeps the role CDK would have made for it, exactly as before.
        self.role: iam.Role | None = None
        self.connect_any = "*" in cfg.connect_accounts  # any user's own account may connect
        if cfg.aws_connect:
            self.role = iam.Role(
                self,
                "ApiRole",
                role_name=f"{cfg.prefix}-api",
                assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
                ],
            )
        self.function = lambda_.Function(
            self,
            "Api",
            runtime=RUNTIME,
            architecture=ARCHITECTURE,
            handler="app.lambda_handler.handler",
            code=backend_code(backend_dir),
            memory_size=512,
            timeout=LAMBDA_TIMEOUT,
            log_group=log_group,
            environment=self._environment(cfg, data, auth, self.account, self.role),
            role=self.role,
        )
        if cfg.live and data.access_code is not None:
            # Read-only, one named secret each (no wildcard, no list). Fetched once per cold start.
            data.openai_key.grant_read(self.function)
            if cfg.require_access_code:
                data.access_code.grant_read(self.function)
        # Exactly the four calls the store makes, on exactly one table (no Scan, Query, batch or index access).
        self.function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"],
                resources=[data.table.table_arn],
            )
        )

        if self.role is not None:
            # The one new permission. Not a wildcard: a fixed role name in each listed account (default: this account).
            self.function.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["sts:AssumeRole"],
                    resources=[f"arn:aws:iam::{a}:role/{ROLE_NAME}" for a in (cfg.connect_accounts or (self.account,))],
                )
            )

        self.api = apigwv2.HttpApi(
            self,
            "HttpApi",
            description=f"AgentOps API ({cfg.stage})",
            default_integration=integrations.HttpLambdaIntegration("Lambda", self.function),
            create_default_stage=False,
        )
        apigwv2.HttpStage(
            self,
            "DefaultStage",
            http_api=self.api,
            auto_deploy=True,
            throttle=apigwv2.ThrottleSettings(rate_limit=THROTTLE_RATE, burst_limit=THROTTLE_BURST),
        )

        Tags.of(self).add("Component", "backend")
        CfnOutput(self, "ApiEndpoint", value=self.api.api_endpoint, description="Direct URL; users go via CloudFront")
        CfnOutput(self, "FunctionName", value=self.function.function_name)
