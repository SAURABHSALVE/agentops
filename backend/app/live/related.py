"""Which neighbouring metrics are worth reading next to an alarm's own metric.

An alarm watches one metric, but a cause is usually visible in the others of the same resource: a Lambda's `Errors`
alarm reads very differently next to `Invocations`, `Duration` and `Throttles`. The list is fixed here, per CloudWatch
namespace, so a model chooses nothing: it can only ask for "the related metrics" and gets these, for the same
dimensions as the alarm. A namespace that is not listed has none. Reading them needs only `cloudwatch:GetMetricData`,
which the role already has.
"""

from dataclasses import dataclass

MAX_RELATED = 6  # the most neighbouring metrics returned to the model


@dataclass(frozen=True)
class RelatedSpec:
    name: str
    stat: str
    unit: str


def _specs(*items: tuple[str, str, str]) -> tuple[RelatedSpec, ...]:
    return tuple(RelatedSpec(*i) for i in items)


# Keyed by the last part of the namespace ("AWS/Lambda" -> "Lambda"). HTTP APIs spell their metrics in lower case
# ("5xx"), REST APIs in upper ("5XXError"); both are listed and whichever has data is used.
_RELATED: dict[str, tuple[RelatedSpec, ...]] = {
    "Lambda": _specs(
        ("Errors", "Sum", "Count"),
        ("Invocations", "Sum", "Count"),
        ("Throttles", "Sum", "Count"),
        ("Duration", "Average", "Milliseconds"),
        ("ConcurrentExecutions", "Maximum", "Count"),
    ),
    "ApiGateway": _specs(
        ("5XXError", "Sum", "Count"),
        ("5xx", "Sum", "Count"),
        ("4XXError", "Sum", "Count"),
        ("4xx", "Sum", "Count"),
        ("Count", "Sum", "Count"),
        ("Latency", "Average", "Milliseconds"),
        ("IntegrationLatency", "Average", "Milliseconds"),
    ),
    "EC2": _specs(
        ("CPUUtilization", "Average", "Percent"),
        ("StatusCheckFailed", "Maximum", "Count"),
        ("CPUCreditBalance", "Minimum", "Count"),
        ("NetworkIn", "Sum", "Bytes"),
        ("NetworkOut", "Sum", "Bytes"),
    ),
    "RDS": _specs(
        ("CPUUtilization", "Average", "Percent"),
        ("DatabaseConnections", "Maximum", "Count"),
        ("FreeableMemory", "Minimum", "Bytes"),
        ("FreeStorageSpace", "Minimum", "Bytes"),
        ("ReadLatency", "Average", "Seconds"),
        ("WriteLatency", "Average", "Seconds"),
    ),
    "DynamoDB": _specs(
        ("ThrottledRequests", "Sum", "Count"),
        ("SystemErrors", "Sum", "Count"),
        ("UserErrors", "Sum", "Count"),
        ("ConsumedReadCapacityUnits", "Sum", "Count"),
        ("ConsumedWriteCapacityUnits", "Sum", "Count"),
        ("SuccessfulRequestLatency", "Average", "Milliseconds"),
    ),
    "SQS": _specs(
        ("ApproximateNumberOfMessagesVisible", "Maximum", "Count"),
        ("ApproximateAgeOfOldestMessage", "Maximum", "Seconds"),
        ("NumberOfMessagesSent", "Sum", "Count"),
        ("NumberOfMessagesReceived", "Sum", "Count"),
        ("NumberOfMessagesDeleted", "Sum", "Count"),
    ),
    "ECS": _specs(
        ("CPUUtilization", "Average", "Percent"),
        ("MemoryUtilization", "Average", "Percent"),
    ),
    "ApplicationELB": _specs(
        ("HTTPCode_Target_5XX_Count", "Sum", "Count"),
        ("HTTPCode_ELB_5XX_Count", "Sum", "Count"),
        ("TargetResponseTime", "Average", "Seconds"),
        ("RequestCount", "Sum", "Count"),
        ("UnHealthyHostCount", "Maximum", "Count"),
    ),
}


def related_specs(namespace: str | None, own_metric: str | None) -> tuple[RelatedSpec, ...]:
    """The neighbouring metrics to read for an alarm on `own_metric` in `namespace` (the alarm's own is left out)."""
    found = _RELATED.get((namespace or "").split("/")[-1], ())
    return tuple(s for s in found if s.name != own_metric)
