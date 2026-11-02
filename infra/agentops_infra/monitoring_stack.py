"""Alarms and the cost budget, all notifying one SNS topic that emails the owner."""

from typing import Any

from aws_cdk import Duration, Stack, Tags
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct

from agentops_infra.backend_stack import LAMBDA_TIMEOUT, BackendStack
from agentops_infra.config import StageConfig

_PERIOD = Duration.minutes(5)


class MonitoringStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *, cfg: StageConfig, backend: BackendStack, **kwargs: Any
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.topic = sns.Topic(self, "Alerts", display_name=f"AgentOps {cfg.stage} alerts", enforce_ssl=True)
        self.topic.add_subscription(subs.EmailSubscription(cfg.alert_email))
        action = cw_actions.SnsAction(self.topic)

        fn = backend.function
        alarms = {
            "LambdaErrors": (
                "The function crashed or timed out (an uncaught exception).",
                fn.metric_errors(period=_PERIOD, statistic="Sum"),
                1,
            ),
            "LambdaThrottles": (
                "Invocations were rejected for lack of concurrency.",
                fn.metric_throttles(period=_PERIOD, statistic="Sum"),
                1,
            ),
            # The app turns unexpected exceptions into a JSON 500, which Lambda counts as a success, so the
            # Errors metric alone would miss them.
            "Api5xx": (
                "The API returned server errors (includes handled 500s that Lambda does not count as errors).",
                backend.api.metric_server_error(period=_PERIOD, statistic="Sum"),
                1,
            ),
            "LambdaDurationNearTimeout": (
                "p95 duration is above 80% of the timeout: requests are about to start timing out.",
                fn.metric_duration(period=_PERIOD, statistic="p95"),
                int(LAMBDA_TIMEOUT.to_milliseconds() * 0.8),
            ),
        }
        if cfg.sign_in:
            # Every refused token is one log line ("token refused: <reason>"). A handful is normal (an expired tab);
            # a burst means someone is probing with forged or stolen tokens, or a pool mismatch is locking people out.
            namespace = f"AgentOps/{cfg.stage}"
            logs.MetricFilter(
                self,
                "TokenRefusals",
                log_group=backend.log_group,
                filter_pattern=logs.FilterPattern.literal('"token refused"'),
                metric_namespace=namespace,
                metric_name="TokenRefused",
                metric_value="1",
                default_value=0,
            )
            alarms["TokenRefusalsSpike"] = (
                "Many sign-in tokens were refused in five minutes: probing, or a pool mismatch locking people out.",
                cloudwatch.Metric(namespace=namespace, metric_name="TokenRefused", period=_PERIOD, statistic="Sum"),
                50,
            )
        for name, (description, metric, threshold) in alarms.items():
            alarm = cloudwatch.Alarm(
                self,
                name,
                alarm_name=f"{cfg.prefix}-{name}",
                alarm_description=description,
                metric=metric,
                threshold=threshold,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,  # no traffic is not an outage
            )
            alarm.add_alarm_action(action)
            alarm.add_ok_action(action)

        # AWS Budgets are account-wide, so this watches total spend, not just this stage.
        email = [budgets.CfnBudget.SubscriberProperty(subscription_type="EMAIL", address=cfg.alert_email)]
        budgets.CfnBudget(
            self,
            "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name=f"{cfg.prefix}-monthly-cost",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=cfg.budget_limit_usd, unit="USD"),
            ),
            notifications_with_subscribers=[
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        notification_type=kind,
                        comparison_operator="GREATER_THAN",
                        threshold=threshold,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=email,
                )
                for kind, threshold in (("ACTUAL", 50), ("ACTUAL", 80), ("FORECASTED", 100))
            ],
        )

        Tags.of(self).add("Component", "monitoring")
