"""The real AwsReader: boto3 clients built from short-lived credentials for the user's own role.

Nothing here can change an account: only Describe/Get/Filter/Lookup calls are made, and the role itself allows nothing
else. Credentials live in memory for their 15-minute life, are never logged and are never stored. AWS error text is
never passed on (it can name resources); the caller gets a fixed sentence and the code is logged.
"""

import hashlib
import logging
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.connections.models import AwsConnection
from app.errors import AwsReadFailedError, AwsRoleUnreachableError
from app.live.models import LiveAlarm
from app.live.reader import Change, ChangeScan, LogHit, LogScan

logger = logging.getLogger("agentops.live")

SESSION_SECONDS = 900
MAX_ALARMS = 25
LOG_PAGES = 2  # pages per group; one page is up to 1 MB scanned
CHANGE_PAGES = 4  # CloudTrail LookupEvents allows two calls per second per account, so keep this small
LOG_WORKERS = 5
METRIC_PAGES = 3
CACHE_LIMIT = 100
# Events that are about signing in or about AgentOps itself, not about changes to the user's workloads.
_NOISE_SOURCES = frozenset({"sts.amazonaws.com", "signin.amazonaws.com"})

# Short timeouts and no retries: a whole investigation has to fit inside one 29-second Lambda call.
_CONFIG = Config(
    connect_timeout=2, read_timeout=4, retries={"max_attempts": 1}, user_agent_extra="agentops-ai/readonly"
)

_THROTTLED = frozenset({"Throttling", "ThrottlingException", "TooManyRequestsException", "RequestLimitExceeded"})
_DENIED = frozenset({"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"})

ClientFactory = Callable[[str, str, dict[str, Any]], Any]  # (service, region, credentials) -> client


def read_failed(exc: Exception, what: str) -> AwsReadFailedError:
    code = str(exc.response.get("Error", {}).get("Code", "")) if isinstance(exc, ClientError) else type(exc).__name__
    logger.warning("aws read failed: %s (%s)", what, code)
    if code in _THROTTLED:
        return AwsReadFailedError("AWS is slowing down reads from your account right now. Try again in a minute.")
    if code in _DENIED:
        return AwsReadFailedError(
            f"AWS did not allow AgentOps to read {what}. Check that the role in your account was created from the "
            "current template."
        )
    return AwsReadFailedError(f"AgentOps could not read {what} from your account just now. Try again in a minute.")


def _millis(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _filter_pattern(terms: Sequence[str]) -> str:
    """A CloudWatch filter pattern matching any term as an exact phrase. Quotes and backslashes are removed first, so a
    term can never end its own quoting or add a clause."""
    phrases = sorted({" ".join(t.replace('"', " ").replace("\\", " ").split()) for t in terms} - {""})
    if not phrases:
        return ""
    if len(phrases) == 1:
        return f'"{phrases[0]}"'
    return " ".join(f'?"{p}"' for p in phrases)


class BotoReader:
    def __init__(self, region: str, *, cloudwatch: Any, logs: Any, cloudtrail: Any) -> None:
        self._region = region
        self._cloudwatch = cloudwatch
        self._logs = logs
        self._cloudtrail = cloudtrail

    # ---- alarms and metrics --------------------------------------------------------------------

    def firing_alarms(self) -> list[dict[str, Any]]:
        try:
            response = self._cloudwatch.describe_alarms(
                AlarmTypes=["MetricAlarm"], StateValue="ALARM", MaxRecords=MAX_ALARMS
            )
        except (ClientError, BotoCoreError) as exc:
            raise read_failed(exc, "your alarms") from None
        alarms: list[dict[str, Any]] = list(response.get("MetricAlarms", []))
        return alarms[:MAX_ALARMS]

    def metric_points(self, alarm: LiveAlarm, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        period = max(60, alarm.period_seconds - alarm.period_seconds % 60)
        query = {
            "Id": "m1",
            "MetricStat": {
                "Metric": {
                    "Namespace": alarm.namespace,
                    "MetricName": alarm.metric_name,
                    "Dimensions": [{"Name": d.name, "Value": d.value} for d in alarm.dimensions],
                },
                "Period": period,
                "Stat": alarm.statistic or "Average",
            },
            "ReturnData": True,
        }
        points: list[tuple[datetime, float]] = []
        token: str | None = None
        try:
            for _ in range(METRIC_PAGES):
                page = self._cloudwatch.get_metric_data(
                    MetricDataQueries=[query],
                    StartTime=start,
                    EndTime=end,
                    ScanBy="TimestampAscending",
                    **({"NextToken": token} if token else {}),
                )
                for result in page.get("MetricDataResults", []):
                    points.extend(zip(result.get("Timestamps", []), result.get("Values", []), strict=False))
                token = page.get("NextToken")
                if not token:
                    break
        except (ClientError, BotoCoreError) as exc:
            raise read_failed(exc, "the alarm's metric") from None
        return sorted((_aware(t), float(v)) for t, v in points)

    # ---- logs ---------------------------------------------------------------------------------

    def search_logs(
        self, groups: Sequence[str], start: datetime, end: datetime, terms: Sequence[str], limit: int
    ) -> LogScan:
        pattern = _filter_pattern(terms)
        if not pattern or not groups:
            return LogScan(hits=[], complete=True)
        with ThreadPoolExecutor(max_workers=LOG_WORKERS, thread_name_prefix="aws-logs") as pool:
            outcomes = list(pool.map(lambda g: self._search_group(g, start, end, pattern, limit), groups))
        if all(o is None for o in outcomes):
            raise AwsReadFailedError(
                "None of the log groups you listed could be read. Check their names, and that the role was created "
                "from the current template."
            )
        hits: list[LogHit] = []
        complete = True
        for outcome in outcomes:
            if outcome is None:
                complete = False
                continue
            hits.extend(outcome[0])
            complete = complete and outcome[1]
        return LogScan(hits=sorted(hits, key=lambda h: h.at), complete=complete)

    def _search_group(
        self, group: str, start: datetime, end: datetime, pattern: str, limit: int
    ) -> tuple[list[LogHit], bool] | None:
        """(hits, complete) for one group; None if it cannot be read (it does not exist, or access was refused)."""
        hits: list[LogHit] = []
        token: str | None = None
        for _ in range(LOG_PAGES):
            try:
                page = self._logs.filter_log_events(
                    logGroupName=group,
                    startTime=_millis(start),
                    endTime=_millis(end),
                    filterPattern=pattern,
                    limit=limit,
                    **({"nextToken": token} if token else {}),
                )
            except ClientError as exc:
                logger.warning("aws read failed: log group (%s)", exc.response.get("Error", {}).get("Code", ""))
                return None
            except BotoCoreError as exc:
                logger.warning("aws read failed: log group (%s)", type(exc).__name__)
                return None
            for event in page.get("events", []):
                stamp, message = event.get("timestamp"), event.get("message")
                if isinstance(stamp, int) and isinstance(message, str):
                    hits.append(
                        LogHit(
                            group=group,
                            event_id=str(event.get("eventId", f"{stamp}")),
                            at=datetime.fromtimestamp(stamp / 1000, UTC),
                            message=message,
                        )
                    )
            token = page.get("nextToken")
            if not token or len(hits) >= limit:
                break
        return hits[:limit], token is None and len(hits) < limit

    # ---- changes ------------------------------------------------------------------------------

    def recent_changes(self, start: datetime, end: datetime, limit: int) -> ChangeScan:
        found: list[Change] = []
        token: str | None = None
        try:
            for _ in range(CHANGE_PAGES):
                page = self._cloudtrail.lookup_events(
                    LookupAttributes=[{"AttributeKey": "ReadOnly", "AttributeValue": "false"}],
                    StartTime=start,
                    EndTime=end,
                    MaxResults=50,
                    **({"NextToken": token} if token else {}),
                )
                for event in page.get("Events", []):
                    # Only these five fields are ever read. `CloudTrailEvent` (the request and response bodies) and
                    # `Resources` are deliberately not touched: they can hold parameters and values.
                    source = event.get("EventSource")
                    if source in _NOISE_SOURCES or not isinstance(source, str):
                        continue
                    when, name = event.get("EventTime"), event.get("EventName")
                    if isinstance(when, datetime) and isinstance(name, str):
                        found.append(
                            Change(
                                event_id=str(event.get("EventId", "")),
                                at=_aware(when),
                                name=name,
                                source=source,
                                actor=str(event.get("Username") or "unknown"),
                            )
                        )
                token = page.get("NextToken")
                if not token or len(found) >= limit:
                    break
        except (ClientError, BotoCoreError) as exc:
            raise read_failed(exc, "recent changes (CloudTrail)") from None
        found.sort(key=lambda c: c.at, reverse=True)
        return ChangeScan(changes=found[:limit], complete=token is None and len(found) < limit)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _default_client(service: str, region: str, creds: dict[str, Any]) -> Any:
    import boto3  # imported lazily: a run that never reads a real account never needs it

    return boto3.client(
        service,
        region_name=region,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        config=_CONFIG,
    )


class BotoReaderFactory:
    """Assumes a user's role (with their ExternalId) and hands back a reader. Credentials are cached for their life."""

    def __init__(
        self,
        *,
        sts: Any | None = None,
        client_factory: ClientFactory = _default_client,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._sts = sts
        self._client = client_factory
        self._clock = clock
        self._cache: dict[tuple[str, str, str], tuple[dict[str, Any], float]] = {}
        self._lock = threading.Lock()

    def _sts_client(self) -> Any:
        if self._sts is None:
            import boto3

            self._sts = boto3.client("sts", config=_CONFIG)
        return self._sts

    def _credentials(self, owner: str, connection: AwsConnection) -> dict[str, Any]:
        key = (owner, str(connection.role_arn), connection.external_id)
        now = self._clock()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and cached[1] - 120 > now:
                return cached[0]
        try:
            response = self._sts_client().assume_role(
                RoleArn=connection.role_arn,
                RoleSessionName="agentops-" + hashlib.sha256(owner.encode()).hexdigest()[:16],
                ExternalId=connection.external_id,
                DurationSeconds=SESSION_SECONDS,
            )
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            logger.warning("assume role failed (%s)", code)
            if code in _THROTTLED:
                raise AwsReadFailedError("AWS is slowing down requests right now. Try again in a minute.") from None
            raise AwsRoleUnreachableError(
                "AgentOps could not use the role in your AWS account. Check that the CloudFormation stack finished "
                "creating, that the role ARN is the one the stack shows, and that the stack was made from the "
                "template shown here (its ExternalId must match)."
            ) from None
        except BotoCoreError as exc:
            logger.warning("assume role failed (%s)", type(exc).__name__)
            raise AwsReadFailedError("AgentOps could not reach AWS just now. Try again in a minute.") from None
        creds = dict(response["Credentials"])
        expires = creds.get("Expiration")
        expires_at = expires.timestamp() if isinstance(expires, datetime) else now + SESSION_SECONDS
        with self._lock:
            if len(self._cache) >= CACHE_LIMIT:
                self._cache.pop(min(self._cache, key=lambda k: self._cache[k][1]))
            self._cache[key] = (creds, expires_at)
        return creds

    def reader_for(self, owner: str, connection: AwsConnection) -> BotoReader:
        creds = self._credentials(owner, connection)
        region = connection.region
        return BotoReader(
            region,
            cloudwatch=self._client("cloudwatch", region, creds),
            logs=self._client("logs", region, creds),
            cloudtrail=self._client("cloudtrail", region, creds),
        )
