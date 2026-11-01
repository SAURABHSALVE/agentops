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


class RemediationStateError(AgentOpsError):
    """The requested approval transition is not valid from the current state."""

    code = "invalid_remediation_state"


class FeedbackNotAllowedError(AgentOpsError):
    """Feedback is for a finished investigation; a running one has nothing to rate yet."""

    code = "feedback_not_allowed"


class UnauthorizedError(AgentOpsError):
    code = "unauthorized"


class SignInRequiredError(AgentOpsError):
    """Sign-in is configured and this action needs a signed-in user."""

    code = "sign_in_required"


class InvalidTokenError(AgentOpsError):
    """A bearer token was presented but is not acceptable (expired, forged, wrong pool, wrong kind)."""

    code = "invalid_token"


class AuthUnavailableError(AgentOpsError):
    """The signing keys could not be fetched, so no token can be checked right now. Safe to retry."""

    code = "auth_unavailable"


class RateLimitedError(AgentOpsError):
    code = "rate_limited"

    def __init__(self, message: str, retry_after: int = 60) -> None:
        super().__init__(message)
        self.retry_after = retry_after
