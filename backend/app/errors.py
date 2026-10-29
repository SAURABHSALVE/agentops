class AgentOpsError(Exception):
    """Domain error with a stable machine-readable code and a client-safe message."""

    code = "error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class IncidentNotFoundError(AgentOpsError):
    code = "incident_not_found"


class MetricNotAvailableError(AgentOpsError):
    code = "metric_not_available"


class UnknownRemediationError(AgentOpsError):
    code = "unknown_remediation"


class RemediationNotApplicableError(AgentOpsError):
    code = "remediation_not_applicable"


class InvestigationNotFoundError(AgentOpsError):
    code = "investigation_not_found"


class InvestigationInProgressError(AgentOpsError):
    code = "investigation_in_progress"


class ProviderError(Exception):
    """A model provider failed. The message is for server logs only and is never returned to clients."""


class TransientProviderError(ProviderError):
    """A provider failure that is safe to retry once (timeout, rate limit, 5xx). Tools are read-only."""
