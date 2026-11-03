"""What is remembered about a user's firing alarms, and what the API shows of them."""

from datetime import datetime
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from app.connections.models import Region
from app.schemas import IncidentSummary

LiveIncidentId = Annotated[str, StringConstraints(pattern=r"^LIVE-[0-9a-f]{12}$")]
MAX_DIMENSIONS = 10


class Dimension(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    value: Annotated[str, StringConstraints(min_length=1, max_length=1024)]


class LiveAlarm(BaseModel):
    """One CloudWatch alarm that was in ALARM state when the user's account was last read.

    It is the incident: the alarm's own metric is the only metric, its state change is when the incident started.
    Free text (name, description, reason) was written by whoever set the alarm up, so it is masked and bounded before it
    is stored, and it is untrusted wherever it is shown or sent to a model.
    """

    model_config = ConfigDict(frozen=True)

    id: LiveIncidentId
    name: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    region: Region
    namespace: Annotated[str, StringConstraints(max_length=255)] | None = None
    metric_name: Annotated[str, StringConstraints(max_length=255)] | None = None
    dimensions: tuple[Dimension, ...] = Field(default=(), max_length=MAX_DIMENSIONS)
    statistic: Annotated[str, StringConstraints(max_length=20)] | None = None
    period_seconds: int = Field(default=60, ge=1, le=86_400)
    threshold: float | None = None
    comparison: Annotated[str, StringConstraints(max_length=60)] | None = None
    unit: Annotated[str, StringConstraints(max_length=40)] | None = None
    reason: Annotated[str, StringConstraints(max_length=300)] = ""
    description: Annotated[str, StringConstraints(max_length=300)] = ""
    state_since: AwareDatetime
    seen_at: AwareDatetime


class LiveIncidentsView(BaseModel):
    """The alarms firing in the user's account right now, as incidents. `data_source` is always the live account."""

    model_config = ConfigDict(frozen=True)

    data_source: str = "your AWS account (live)"
    region: str
    checked_at: datetime
    incidents: list[IncidentSummary]
