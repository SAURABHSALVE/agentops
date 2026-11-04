"""What AgentOps asks of a user's AWS account, as plain Python: no boto3 here.

`AwsReader` is the whole read surface: firing alarms, one metric's data points, a bounded log search and recent change
events. There is no method that writes, and a reader is created per user from their own role. The real implementation
is `app.live.boto_reader`; tests use a fake that answers from memory.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.connections.models import AwsConnection
from app.live.models import LiveAlarm
from app.live.related import RelatedSpec


@dataclass(frozen=True)
class LogHit:
    group: str
    event_id: str
    at: datetime
    message: str  # raw: the caller masks it before it goes anywhere


@dataclass(frozen=True)
class LogScan:
    hits: list[LogHit]
    # False when some group could not be read, or a group had more matches than were fetched. Then "nothing found" does
    # not mean "nothing there".
    complete: bool


@dataclass(frozen=True)
class Change:
    event_id: str
    at: datetime
    name: str  # e.g. UpdateFunctionCode20150331v2
    source: str  # e.g. lambda.amazonaws.com
    actor: str  # the user or role name; never the request details


@dataclass(frozen=True)
class ChangeScan:
    changes: list[Change]
    complete: bool


class AwsReader(Protocol):
    def firing_alarms(self) -> list[dict[str, Any]]:
        """CloudWatch metric alarms currently in the ALARM state, as DescribeAlarms returns them."""
        ...

    def metric_points(self, alarm: LiveAlarm, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        """The alarm's own metric (same statistic and period) between start and end, oldest first."""
        ...

    def related_metric_points(
        self, alarm: LiveAlarm, specs: Sequence[RelatedSpec], start: datetime, end: datetime
    ) -> dict[str, list[tuple[datetime, float]]]:
        """Other metrics of the same resource (same dimensions and period as the alarm), by metric name. A metric with
        no data is simply absent from the answer."""
        ...

    def search_logs(
        self, groups: Sequence[str], start: datetime, end: datetime, terms: Sequence[str], limit: int
    ) -> LogScan:
        """Log lines containing any of `terms` (matched as exact phrases) from exactly `groups`, oldest first."""
        ...

    def recent_changes(self, start: datetime, end: datetime, limit: int) -> ChangeScan:
        """Write (non-read-only) management events from CloudTrail, newest first."""
        ...


class ReaderFactory(Protocol):
    def reader_for(self, owner: str, connection: AwsConnection) -> AwsReader:
        """Use the connection's role (raising AwsRoleUnreachableError if AWS refuses) and return a reader for it."""
        ...
