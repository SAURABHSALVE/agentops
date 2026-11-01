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
# Reads are open and writes need sign-in, but still cap what a stranger can do to the bill. Requests/second, per stage.
THROTTLE_RATE, THROTTLE_BURST = 10, 20


class BackendStack(Stack):
    @staticmethod
    def _environment(cfg: StageConfig, data: DataStack, auth: AuthStack | None) -> dict[str, str]:
        env = {
            "STORAGE_MODE": "dynamodb",
            "DYNAMODB_TABLE": data.table.table_name,
            "AGENT_PROVIDER": "mock",
            "STAGE": cfg.stage,
        }
        if cfg.live and data.access_code is not None:
            env.update(
                {
                    "AGENT_PROVIDER": "openai",
                    "OPENAI_MODEL": cfg.openai_model,
                    "OPENAI_SECRET_ARN": data.openai_key.secret_arn,
                    "ACCESS_SECRET_ARN": data.access_code.secret_arn,
                    # A live run takes 6 to 12 s; give up well before the 29 s Lambda limit instead of hanging.
                    "OPENAI_TIMEOUT_SECONDS": "12",
                    # Backstops behind the API throttle; per Lambda container, so they are not a global cap.
                    "INVESTIGATIONS_PER_MINUTE": "6",
                    "INVESTIGATIONS_PER_HOUR": "60",
                }
            )
        if auth is not None:
            # Which pool's tokens to trust. The Lambda only reads the pool's public keys, so it needs no permission.
            env["COGNITO_USER_POOL_ID"] = auth.pool.user_pool_id
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
            environment=self._environment(cfg, data, auth),
        )
        if cfg.live and data.access_code is not None:
            # Read-only, one named secret each (no wildcard, no list). Fetched once per cold start.
            data.openai_key.grant_read(self.function)
            data.access_code.grant_read(self.function)
        # Exactly the four calls the store makes, on exactly one table (no Scan, Query, batch or index access).
        self.function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"],
                resources=[data.table.table_arn],
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
