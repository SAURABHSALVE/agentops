"""Contract for investigations: what a provider returns, what the API serves.

`FinalAnswer` is what a model may produce; it is validated strictly and never trusted. `Findings`
is what the API serves after the backend has grounded the evidence and resolved the remediation
from its own allowlist.
"""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from app.schemas import IncidentId

Confidence = Literal["low", "medium", "high"]
EvidenceSource = Literal["logs", "metrics", "deployments"]
InvestigationStatus = Literal["running", "completed", "failed"]
FailureCode = Literal[
    "provider_error",
    "budget_exceeded",
    "max_rounds_exceeded",
    "invalid_output",
    "ungrounded_evidence",
    "remediation_not_allowed",
    "internal_error",
]
InvestigationId = Annotated[str, StringConstraints(pattern=r"^inv-\d{4,}$")]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ---- Provider-facing --------------------------------------------------------------------------


class EvidenceItem(_Model):
    source: EvidenceSource
    reference_id: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    observation: Annotated[str, StringConstraints(min_length=1, max_length=500)]


_Short = Annotated[str, StringConstraints(min_length=1, max_length=600)]
_Item = Annotated[str, StringConstraints(min_length=1, max_length=400)]


class FinalAnswer(_Model):
    """The only shape of final answer the backend accepts from any provider."""

    summary: _Short
    root_cause_hypothesis: _Short
    confidence: Confidence
    confidence_rationale: _Short
    evidence: Annotated[list[EvidenceItem], Field(max_length=12)]
    alternative_hypotheses: Annotated[list[_Item], Field(max_length=6)]
    recommended_next_step: _Short
    remediation_action_id: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None
    limitations: Annotated[list[_Item], Field(max_length=6)]


class TokenUsage(_Model):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


# ---- API-facing -------------------------------------------------------------------------------


class Remediation(_Model):
    """Always filled from the server-side allowlist, never from model text."""

    action_id: str
    description: str
    expected_effect: str
    requires_approval: Literal[True] = True


class Findings(_Model):
    summary: str
    root_cause_hypothesis: str
    confidence: Confidence
    confidence_rationale: str
    evidence: list[EvidenceItem]
    alternative_hypotheses: list[str]
    recommended_next_step: str
    remediation: Remediation | None
    limitations: list[str]


ToolStatus = Literal["ok", "error"]


class ToolCallRecord(_Model):
    id: str
    name: str  # sanitised even when the model named a tool that does not exist
    arguments: dict[str, str | int]  # validated values only; empty when the call was rejected
    status: ToolStatus
    error_code: str | None = None
    duration_ms: int
    result_summary: str
    reference_ids: list[str]
    at: AwareDatetime


class ProviderInfo(_Model):
    kind: Literal["mock", "openai"]
    label: str


class Usage(_Model):
    rounds: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class Failure(_Model):
    code: FailureCode
    message: str


class Investigation(_Model):
    id: InvestigationId
    incident_id: IncidentId
    status: InvestigationStatus
    provider: ProviderInfo
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    tool_calls: list[ToolCallRecord] = []
    findings: Findings | None = None
    failure: Failure | None = None
    usage: Usage = Usage()
    simulated: Literal[True] = True
