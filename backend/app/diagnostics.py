"""The four read-only functions the agent's tools call, as an interface.

`Simulator` implements it over demo telemetry (shared, fictional or captured from the lab). `LiveDiagnostics`
(app/live) implements it over one user's own AWS account. The tool runner only ever sees this interface, so what a
model may ask for is the same in both cases; only where the answers come from differs.
"""

from typing import Protocol

from app.schemas import (
    DeploymentsResult,
    GetDeploymentsArgs,
    GetIncidentArgs,
    GetMetricsArgs,
    IncidentDetail,
    LogSearchResult,
    MetricsResult,
    SearchLogsArgs,
)


class Diagnostics(Protocol):
    # True when the data comes from a real account: the prompt, the labels and the limits on remediation follow from it.
    is_live: bool

    def prepare(self) -> None:
        """Do any slow or fallible set-up (such as assuming a role) before the first tool call, outside its timeout."""
        ...

    def get_incident(self, args: GetIncidentArgs) -> IncidentDetail: ...

    def search_logs(self, args: SearchLogsArgs) -> LogSearchResult: ...

    def get_service_metrics(self, args: GetMetricsArgs) -> MetricsResult: ...

    def get_recent_deployments(self, args: GetDeploymentsArgs) -> DeploymentsResult: ...
