"""The four diagnostic tools over one user's own AWS account.

Same results, same bounds and same citable ids as the demo tools, so grounding, confidence checks and the UI work
unchanged. What differs is the source (CloudWatch alarms, metrics, log groups and CloudTrail) and one honesty rule:
a search that could not look everywhere never claims "nothing was found".

Everything that comes back is masked (app/redaction.py) before it leaves this module, log text is bounded per line, and
CloudTrail contributes only when, what kind of event, which service and who. Request parameters are never read.
"""

import hashlib
import re
from bisect import bisect_right
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

from app.errors import IncidentNotFoundError, MetricNotAvailableError
from app.live.alarms import to_detail
from app.live.models import LiveAlarm
from app.live.reader import AwsReader
from app.live.related import MAX_RELATED, related_specs
from app.redaction import redact
from app.schemas import (
    DeploymentRecord,
    DeploymentsResult,
    GetDeploymentsArgs,
    GetIncidentArgs,
    GetMetricsArgs,
    IncidentDetail,
    LogEntry,
    LogLevel,
    LogSearchResult,
    MetricsResult,
    RelatedMetric,
    RelatedMetricsResult,
    SearchLogsArgs,
)
from app.simulator.metrics import LEAD, build_metrics_result_from_values, series_span, summarise
from app.simulator.service import MAX_MESSAGE_CHARS, no_match_id

PER_GROUP_LIMIT = 50  # the most lines fetched from one log group for one search
MAX_CHANGES = 25  # the most changes returned to the model
FETCH_CHANGES = 100  # fetched before filtering by service, so unrelated noise cannot push the relevant ones out

# CloudTrail event sources (without ".amazonaws.com") that can explain an alarm in a CloudWatch namespace. Changes made
# through these deployment services can alter anything, so they are always kept. An unknown namespace is not filtered.
_RELATED_SOURCES: dict[str, frozenset[str]] = {
    "Lambda": frozenset({"lambda"}),
    "EC2": frozenset({"ec2", "autoscaling"}),
    "EBS": frozenset({"ec2"}),
    "RDS": frozenset({"rds"}),
    "DynamoDB": frozenset({"dynamodb"}),
    "ApiGateway": frozenset({"apigateway"}),
    "ECS": frozenset({"ecs"}),
    "SQS": frozenset({"sqs"}),
    "SNS": frozenset({"sns"}),
    "S3": frozenset({"s3"}),
    "CloudFront": frozenset({"cloudfront"}),
    "Kinesis": frozenset({"kinesis"}),
    "States": frozenset({"states"}),
    "ElastiCache": frozenset({"elasticache"}),
    "ApplicationELB": frozenset({"elasticloadbalancing"}),
    "NetworkELB": frozenset({"elasticloadbalancing"}),
    "ELB": frozenset({"elasticloadbalancing"}),
}
_DEPLOYMENT_SERVICES = frozenset({"cloudformation", "codedeploy", "codepipeline", "codebuild"})


def related_sources(namespace: str | None) -> frozenset[str] | None:
    """The CloudTrail sources worth showing for an alarm in `namespace`, or None to show everything."""
    own = _RELATED_SOURCES.get((namespace or "").split("/")[-1])
    return None if own is None else own | _DEPLOYMENT_SERVICES


_ERROR = re.compile(r"\b(error|fatal|critical|exception|traceback|panic|failed|timed out)\b", re.IGNORECASE)
_WARN = re.compile(r"\bwarn(ing)?\b", re.IGNORECASE)
_DEBUG = re.compile(r"\bdebug\b", re.IGNORECASE)
_CODE_CHANGES = re.compile(
    r"^(UpdateFunctionCode|CreateFunction|PublishVersion|UpdateService|CreateDeployment|RegisterTaskDefinition|"
    r"StartDeployment|UpdateStack|ExecuteChangeSet|PutImage|CreateStack)"
)


def infer_level(message: str) -> LogLevel:
    """CloudWatch log lines carry no level, so one is read off the text. A guess, and only used for filtering."""
    head = message[:300]
    if _ERROR.search(head):
        return "ERROR"
    if _WARN.search(head):
        return "WARN"
    if _DEBUG.search(head):
        return "DEBUG"
    return "INFO"


def _case_variants(query: str) -> list[str]:
    """CloudWatch filter patterns are case-sensitive; the demo search is not. Ask for the usual spellings, then
    confirm case-insensitively here."""
    return list({query, query.lower(), query.upper(), query.capitalize()})


def _per_minute(
    points: list[tuple[datetime, float]], start: datetime, end: datetime, span: timedelta, *, zero_gaps: bool
) -> list[tuple[datetime, float]]:
    """One value per minute from `start` to `end`, as the demo metrics are, so one summary fits every source.

    A minute that no data point covers is 0 when `zero_gaps` (CloudWatch reports nothing when nothing happened), and
    otherwise repeats the last value seen (the first value, before any), as a level such as memory would.
    """
    stamps = [p[0] for p in points]
    values: list[tuple[datetime, float]] = []
    moment = start
    while moment <= end:
        i = bisect_right(stamps, moment) - 1
        if i >= 0 and moment < stamps[i] + span:
            value = points[i][1]
        elif zero_gaps:
            value = 0.0
        else:
            value = points[max(i, 0)][1]
        values.append((moment, value))
        moment += timedelta(minutes=1)
    return values


def _entry_id(group: str, event_id: str) -> str:
    return "log:" + hashlib.sha1(f"{group}\n{event_id}".encode(), usedforsecurity=False).hexdigest()[:14]


class LiveDiagnostics:
    is_live = True

    def __init__(
        self,
        alarms: Mapping[str, LiveAlarm],
        log_groups: tuple[str, ...],
        open_reader: Callable[[], AwsReader],
    ) -> None:
        self._alarms = alarms
        self._groups = log_groups
        self._open = open_reader
        self._reader: AwsReader | None = None

    def prepare(self) -> None:
        """Assume the role now. If AWS refuses, the run stops here with a clear reason instead of mid-investigation."""
        self._connected()

    def _connected(self) -> AwsReader:
        if self._reader is None:
            self._reader = self._open()
        return self._reader

    def _alarm(self, incident_id: str) -> LiveAlarm:
        alarm = self._alarms.get(incident_id)
        if alarm is None:
            raise IncidentNotFoundError("Incident not found.")
        return alarm

    # ---- the four tools ----------------------------------------------------------------------

    def get_incident(self, args: GetIncidentArgs) -> IncidentDetail:
        return to_detail(self._alarm(args.incident_id))

    def search_logs(self, args: SearchLogsArgs) -> LogSearchResult:
        incident = to_detail(self._alarm(args.incident_id))
        start = incident.started_at - LEAD
        scan = self._connected().search_logs(
            self._groups, start, incident.window_end, _case_variants(args.query), PER_GROUP_LIMIT
        )
        needle = args.query.lower()
        matches: list[LogEntry] = []
        for hit in scan.hits:
            # Mask first, then match: a search must not be able to confirm a secret it cannot see.
            message = redact(hit.message).strip()
            level = infer_level(message)
            if (needle in message.lower() or needle == level.lower()) and (args.level is None or level == args.level):
                matches.append(
                    LogEntry(
                        id=_entry_id(hit.group, hit.event_id),
                        timestamp=hit.at,
                        level=level,
                        service=hit.group,
                        message=message[:MAX_MESSAGE_CHARS],
                    )
                )
        entries = matches[: args.limit]
        # "Looked everywhere and found nothing" is evidence; "could not look everywhere" is not, so it gets no id.
        reference_ids = [e.id for e in entries] or (
            [no_match_id(incident.service, args.query)] if scan.complete else []
        )
        return LogSearchResult(
            incident_id=incident.id,
            service=incident.service,
            query=args.query,
            window_start=start,
            window_end=incident.window_end,
            total_matches=len(matches),
            returned=len(entries),
            truncated=len(matches) > len(entries) or not scan.complete,
            entries=entries,
            reference_ids=reference_ids,
        )

    def get_service_metrics(self, args: GetMetricsArgs) -> MetricsResult:
        alarm = self._alarm(args.incident_id)
        if args.metric != "alarm_metric":
            raise MetricNotAvailableError(
                f"Metric '{args.metric}' does not exist for this incident. Its alarm watches one metric: "
                "ask for 'alarm_metric'."
            )
        if not alarm.metric_name or not alarm.namespace:
            raise MetricNotAvailableError("This alarm is not based on a single metric, so there is no series to read.")
        incident = to_detail(alarm)
        start, end = series_span(incident)
        points = self._connected().metric_points(alarm, start, end)
        if not points:
            raise MetricNotAvailableError("CloudWatch returned no data points for this metric in the window.")

        # A minute no data point covers counts as 0: CloudWatch reports nothing when nothing happened (no errors, no
        # requests), and the alarm's own statistic decides what "elevated" means.
        values = _per_minute(points, start, end, timedelta(seconds=max(60, alarm.period_seconds)), zero_gaps=True)
        return build_metrics_result_from_values(incident, "alarm_metric", alarm.unit or alarm.metric_name, values)

    def get_related_metrics(self, incident_id: str) -> RelatedMetricsResult:
        alarm = self._alarm(incident_id)
        if not alarm.metric_name or not alarm.namespace:
            raise MetricNotAvailableError("This alarm is not based on a single metric, so it has no related metrics.")
        specs = related_specs(alarm.namespace, alarm.metric_name)
        if not specs:
            raise MetricNotAvailableError("No related metrics are known for this kind of alarm.")
        incident = to_detail(alarm)
        start, end = series_span(incident)
        found = self._connected().related_metric_points(alarm, specs, start, end)
        span = timedelta(seconds=max(60, alarm.period_seconds))
        metrics = [
            RelatedMetric(
                name=spec.name,
                statistic=spec.stat,
                unit=spec.unit,
                # Counts are zero when nothing was reported; a level (CPU, memory, queue age) keeps its last value.
                summary=summarise(
                    incident, _per_minute(found[spec.name], start, end, span, zero_gaps=spec.stat == "Sum")
                ),
            )
            for spec in specs
            if found.get(spec.name)
        ][:MAX_RELATED]
        if not metrics:
            raise MetricNotAvailableError(
                "CloudWatch has no data for the other metrics of this resource in the window."
            )
        window = f"{start:%H:%M}-{end:%H:%M}"
        return RelatedMetricsResult(
            incident_id=incident.id,
            service=incident.service,
            window_start=start,
            window_end=end,
            metrics=metrics,
            without_data=len(specs) - len(metrics),
            reference_ids=[f"metric:related:{m.name}:{window}" for m in metrics],
        )

    def get_recent_deployments(self, args: GetDeploymentsArgs) -> DeploymentsResult:
        alarm = self._alarm(args.incident_id)
        incident = to_detail(alarm)
        as_of = incident.window_end
        since = as_of - timedelta(hours=args.hours)
        scan = self._connected().recent_changes(since, as_of, FETCH_CHANGES)
        keep = related_sources(alarm.namespace)
        relevant = [c for c in scan.changes if keep is None or c.source.removesuffix(".amazonaws.com") in keep]
        recent = sorted(relevant, key=lambda c: c.at, reverse=True)[:MAX_CHANGES]
        found = [
            DeploymentRecord(
                id=f"ct:{c.event_id}"[:100],
                deployed_at=c.at,
                service=c.source.removesuffix(".amazonaws.com")[:60],
                change_type="code" if _CODE_CHANGES.match(c.name) else "config",
                summary=redact(f"{c.name} on {c.source.removesuffix('.amazonaws.com')} by {c.actor}")[:200],
                minutes_before_incident_start=int((incident.started_at - c.at).total_seconds() // 60),
            )
            for c in sorted(recent, key=lambda c: c.at)
            if c.event_id
        ]
        refs = [d.id for d in found] or ([f"deploys:{incident.service}:{args.hours}h"] if scan.complete else [])
        return DeploymentsResult(
            incident_id=incident.id,
            service=incident.service,
            hours=args.hours,
            as_of=as_of,
            deployments=found,
            reference_ids=refs,
        )
