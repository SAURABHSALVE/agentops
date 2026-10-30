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

from agentops_infra.config import StageConfig
from agentops_infra.data_stack import DataStack
from agentops_infra.lambda_bundling import ARCHITECTURE, RUNTIME, backend_code

# Sized for the mock provider, which answers in milliseconds. API Gateway cuts a request off at 30s, so the
# function must finish first. Live OpenAI calls (Phase 3B) need an asynchronous design; see DECISIONS.md D-023.
LAMBDA_TIMEOUT = Duration.seconds(29)
# Public endpoint with no auth: cap what a stranger can do to the bill. Requests per second, per stage.
THROTTLE_RATE, THROTTLE_BURST = 10, 20


class BackendStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cfg: StageConfig,
        data: DataStack,
        backend_dir: Path,
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
            environment={
                "STORAGE_MODE": "dynamodb",
                "DYNAMODB_TABLE": data.table.table_name,
                "AGENT_PROVIDER": "mock",
                "STAGE": cfg.stage,
            },
        )
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
