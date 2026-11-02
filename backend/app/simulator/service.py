"""Diagnostic tools, simulated remediation and verification, over an IncidentRepository.

The four diagnostic methods are the read-only functions the Phase 3 agent will be allowed to
call. They take already-validated *Args models, return bounded results, and attach
`reference_ids` that are the only IDs a finding may cite as evidence.
"""

import re
from datetime import timedelta

from app.errors import (
    RemediationNotApplicableError,
    UnknownRemediationError,
)
from app.identity import ANONYMOUS_OWNER
from app.redaction import redact
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
    VerificationResult,
)
from app.simulator.metrics import LEAD
from app.simulator.remediation import REMEDIATIONS
from app.simulator.source import DataMode, FixtureSource, TelemetrySource

MAX_MESSAGE_CHARS = 300  # bound per-entry size: log text is untrusted and may be long


def _no_match_id(service: str, query: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:40] or "query"
    return f"logs:{service}:no-match:{slug}"


class Simulator:
    def __init__(self, repo: IncidentRepository, source: TelemetrySource | None = None) -> None:
        self._repo = repo
        self._source: TelemetrySource = source or FixtureSource()

    @property
    def data_mode(self) -> DataMode:
        return self._source.data_mode

    # ---- read-only diagnostic tools ----------------------------------------------------------

    def list_incidents(self) -> list[IncidentSummary]:
        return self._repo.list_incidents()

    def get_incident(self, args: GetIncidentArgs) -> IncidentDetail:
        return self._repo.get_incident(args.incident_id)

    def search_logs(self, args: SearchLogsArgs) -> LogSearchResult:
        incident = self._repo.get_incident(args.incident_id)
        start = incident.started_at - LEAD
        needle = args.query.lower()
        # Redact first, then match: a search must not be able to confirm a secret it cannot see.
        safe = (e.model_copy(update={"message": redact(e.message)}) for e in self._source.logs(incident.id))
        matches = [
            e
            for e in safe
            if start <= e.timestamp <= incident.window_end
            and (needle in e.message.lower() or needle == e.level.lower())
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
            # An empty search is itself evidence ("looked, found nothing") and gets a citable id, like deployments.
            reference_ids=[e.id for e in entries] or [_no_match_id(incident.service, args.query)],
        )

    def get_service_metrics(self, args: GetMetricsArgs) -> MetricsResult:
        incident = self._repo.get_incident(args.incident_id)
        return self._source.metrics(incident, args.metric)

    def get_recent_deployments(self, args: GetDeploymentsArgs) -> DeploymentsResult:
        incident = self._repo.get_incident(args.incident_id)
        as_of = incident.window_end
        since = as_of - timedelta(hours=args.hours)
        found = [
            d.model_copy(update={"summary": redact(d.summary)})
            for d in self._source.deployments(incident.service, incident.started_at)
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

    def execute_remediation(self, incident_id: str, action_id: str, owner: str = ANONYMOUS_OWNER) -> ActionResult:
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
        newly_applied = self._repo.record_remediation(incident.id, spec.action_id, owner)
        return ActionResult(
            incident_id=incident.id,
            action_id=spec.action_id,
            status="executed" if newly_applied else "already_applied",
            summary=spec.description if newly_applied else "Action was already applied; nothing re-run.",
        )

    def forget_owner(self, owner: str) -> None:
        """Forget every simulated action this owner applied."""
        self._repo.forget_owner(owner)

    def verify_resolution(self, incident_id: str, owner: str = ANONYMOUS_OWNER) -> VerificationResult:
        """Check simulated metrics against thresholds. This is separate from action success:
        an action can succeed while verification still fails."""
        incident = self._repo.get_incident(incident_id)
        applied = self._repo.get_applied_remediation(incident.id, owner) is not None
        checks = self._source.verification(incident, applied)
        if not checks:
            return VerificationResult(incident_id=incident.id, status="not_verifiable", checks=[])
        status = "resolved" if all(c.passed for c in checks) else "not_resolved"
        return VerificationResult(incident_id=incident.id, status=status, checks=checks)
