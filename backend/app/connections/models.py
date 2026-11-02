"""What is stored about a connection, what a client may send, and what the API shows."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

ROLE_NAME = "AgentOpsReadOnly"
MAX_LOG_GROUPS = 10

# Commercial-partition regions only. The value ends up in an ARN and, later, in a service host name, so it is a
# closed shape rather than "anything that looks like a region".
Region = Annotated[str, StringConstraints(pattern=r"^(us|eu|ap|sa|ca|me|af|il|mx)-[a-z]+-\d$")]
# A CloudWatch log group name, or a prefix ending in one `*`. No ':' (it would end the name inside an ARN), no spaces,
# no '$' or '{' (the name is placed inside a CloudFormation Fn::Sub string).
LogGroupName = Annotated[str, StringConstraints(pattern=r"^/?[A-Za-z0-9_.#/-]{3,200}\*?$")]
# The role AgentOps's own template creates, in some account: nothing else is accepted.
RoleArn = Annotated[str, StringConstraints(pattern=rf"^arn:aws:iam::\d{{12}}:role/{ROLE_NAME}$")]

ConnectionStatus = Literal["draft", "registered"]
UnavailableReason = Literal["sign_in_off", "sign_in_required", "not_configured"]


class AwsConnection(BaseModel):
    """One user's saved connection. `draft`: settings saved, waiting for them to create the role. `registered`:
    they gave us the role's ARN (not yet proven to work: that needs a call to their account)."""

    model_config = ConfigDict(frozen=True)

    status: ConnectionStatus
    external_id: Annotated[str, StringConstraints(pattern=r"^agentops-[A-Za-z0-9_-]{20,64}$")]
    region: Region
    log_groups: tuple[LogGroupName, ...] = Field(min_length=1, max_length=MAX_LOG_GROUPS)
    role_arn: RoleArn | None = None
    consent_version: str
    # Which provider the user was told their log excerpts go to. A different one later needs a fresh agreement.
    consent_provider: str
    consented_at: datetime
    updated_at: datetime


class ConnectionSettings(BaseModel):
    """Step 1: where to look, and the agreement to how the data is used. Nothing else can be set by a client."""

    model_config = ConfigDict(extra="forbid")

    region: Region
    log_groups: list[LogGroupName] = Field(min_length=1, max_length=MAX_LOG_GROUPS)
    consent_version: str = Field(max_length=40)
    accept_data_use: Literal[True]

    @field_validator("log_groups")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))  # log group names are case-sensitive; keep the order they were typed in


class RoleRegistration(BaseModel):
    """Step 3: the ARN of the role the template created."""

    model_config = ConfigDict(extra="forbid")

    role_arn: RoleArn


class PermissionView(BaseModel):
    id: str
    title: str
    why: str
    actions: list[str]
    applies_to: Literal["account", "log_groups"]
    reads: str
    never: str


class ConsentView(BaseModel):
    version: str
    points: list[str]


class SetupView(BaseModel):
    """What the user needs to carry out step 2 in their own AWS console."""

    role_name: str
    template_file: str
    trusted_account_id: str


class ConnectionView(BaseModel):
    available: bool
    # Why it is not available: the page shows this instead of a form. Absent when it is.
    reason: UnavailableReason | None = None
    status: Literal["not_connected", "draft", "registered"] = "not_connected"
    region: str | None = None
    log_groups: list[str] = Field(default_factory=list)
    log_groups_use_wildcard: bool = False
    role_arn: str | None = None
    external_id: str | None = None
    consent_current: bool = False
    consent: ConsentView | None = None
    permissions: list[PermissionView] = Field(default_factory=list)
    setup: SetupView | None = None
    updated_at: datetime | None = None
