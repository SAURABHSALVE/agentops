"""Diagnostic tools, simulated remediation and verification, over an IncidentRepository.

The four diagnostic methods are the read-only functions the Phase 3 agent will be allowed to
call. They take already-validated *Args models, return bounded results, and attach
`reference_ids` that are the only IDs a finding may cite as evidence.
"""

from datetime import timedelta

from app.errors import (
    MetricNotAvailableError,
    RemediationNotApplicableError,
    UnknownRemediationError,
)
from app.repository import IncidentRepository
from app.schemas import (
    ActionResult,
    DeploymentsResult,
    GetDeploymentsArgs,
    GetIncidentArgs,
    GetMetricsArgs,
    IncidentDetail,
    IncidentSummary,
    LogSearchResult,
    MetricsResult,
    SearchLogsArgs,
    VerificationCheck,
    VerificationResult,
)
from app.simulator import fixtures
from app.simulator.metrics import LEAD, build_metrics_result, raw_value
from app.simulator.remediation import REMEDIATIONS

MAX_MESSAGE_CHARS = 300  # bound per-entry size: log text is untrusted and may be long


class Simulator:
    def __init__(self, repo: IncidentRepository) -> None:
        self._repo = repo

    # ---- read-only diagnostic tools ----------------------------------------------------------

    def list_incidents(self) -> list[IncidentSummary]:
        return self._repo.list_incidents()

    def get_incident(self, args: GetIncidentArgs) -> IncidentDetail:
        return self._repo.get_incident(args.incident_id)

    def search_logs(self, args: SearchLogsArgs) -> LogSearchResult:
        incident = self._repo.get_incident(args.incident_id)
        start = incident.started_at - LEAD
        needle = args.query.lower()
        matches = [
            e
            for e in fixtures.LOGS.get(incident.id, ())
            if start <= e.timestamp <= incident.window_end
            and needle in e.message.lower()
            and (args.level is None or e.level == args.level)
        ]
        entries = [e.model_copy(update={"message": e.message[:MAX_MESSAGE_CHARS]}) for e in matches[: args.limit]]
        return LogSearchResult(
            incident_id=incident.id,
            service=incident.service,
            query=args.query,
            window_start=start,
            window_end=incident.window_end,
            total_matches=len(matches),
            returned=len(entries),
            truncated=len(matches) > len(entries),
            entries=entries,
            reference_ids=[e.id for e in entries],
        )

    def get_service_metrics(self, args: GetMetricsArgs) -> MetricsResult:
        incident = self._repo.get_incident(args.incident_id)
        spec = fixtures.METRICS.get((incident.id, args.metric))
        if spec is None:
            raise MetricNotAvailableError(f"Metric '{args.metric}' is not available for service '{incident.service}'.")
        return build_metrics_result(incident, args.metric, spec)

    def get_recent_deployments(self, args: GetDeploymentsArgs) -> DeploymentsResult:
        incident = self._repo.get_incident(args.incident_id)
        as_of = incident.window_end
        since = as_of - timedelta(hours=args.hours)
        found = [
            d
            for d in fixtures.deployments_for(incident.service, incident.started_at)
            if since <= d.deployed_at <= as_of
        ]
        # An empty result is itself evidence ("no deployments"), so it gets a citable id.
        refs = [d.id for d in found] or [f"deploys:{incident.service}:{args.hours}h"]
        return DeploymentsResult(
            incident_id=incident.id,
            service=incident.service,
            hours=args.hours,
            as_of=as_of,
            deployments=found,
            reference_ids=refs,
        )

    # ---- simulated remediation and verification ----------------------------------------------

    def execute_remediation(self, incident_id: str, action_id: str) -> ActionResult:
        """Run an allowlisted *simulated* action.

        Not reachable over HTTP in Phase 2: the human-approval gate is built in Phase 5, and the
        server (not the model) must check approval state before calling this.
        """
        incident = self._repo.get_incident(incident_id)
        spec = REMEDIATIONS.get(action_id)
        if spec is None:
            raise UnknownRemediationError("Action is not in the remediation allowlist.")
        if incident.service not in spec.applicable_services:
            raise RemediationNotApplicableError(
                f"Action '{spec.action_id}' does not apply to service '{incident.service}'."
            )
        newly_applied = self._repo.record_remediation(incident.id, spec.action_id)
        return ActionResult(
            incident_id=incident.id,
            action_id=spec.action_id,
            status="executed" if newly_applied else "already_applied",
            summary=spec.description if newly_applied else "Action was already applied; nothing re-run.",
        )

    def verify_resolution(self, incident_id: str) -> VerificationResult:
        """Check simulated metrics against thresholds. This is separate from action success:
        an action can succeed while verification still fails."""
        incident = self._repo.get_incident(incident_id)
        specs = fixtures.VERIFICATION.get(incident.id, ())
        if not specs:
            return VerificationResult(incident_id=incident.id, status="not_verifiable", checks=[])
        applied = self._repo.get_applied_remediation(incident.id) is not None
        checks: list[VerificationCheck] = []
        for v in specs:
            if applied:
                observed = v.post_remediation_value
            else:
                observed = raw_value(fixtures.METRICS[(incident.id, v.metric)], incident.window_end)
            checks.append(
                VerificationCheck(
                    metric=v.metric,
                    observed=round(observed, 2),
                    threshold=v.threshold,
                    comparison="lt",
                    passed=observed < v.threshold,
                )
            )
        status = "resolved" if all(c.passed for c in checks) else "not_resolved"
        return VerificationResult(incident_id=incident.id, status=status, checks=checks)
