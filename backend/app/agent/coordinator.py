"""The bounded investigation loop. The backend is the authority; the provider only proposes.

Per run: at most `max_rounds` provider calls, `max_tool_calls` tool executions and
`max_total_tokens` tokens. Every provider output is parsed and validated here: tool names and
arguments (via ToolRunner), the final answer's schema, that every cited evidence id came from a
tool result recorded in THIS run (and from the kind of tool the answer claims), and that any
remediation is on the server-side allowlist and applies to the incident's service. Anything else
ends the run in an honest `failed` state; validation failures are never retried.

Logs record ids, tool names, durations, status and token counts. Never prompts or responses.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.agent.models import (
    EvidenceSource,
    Failure,
    FailureCode,
    FinalAnswer,
    Findings,
    Investigation,
    Remediation,
    RemediationRecord,
    ToolCallRecord,
    Usage,
)
from app.agent.prompts import USER_PROMPT, encode_tool_result, system_prompt
from app.agent.provider import (
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
    Message,
    ToolCallRequest,
)
from app.agent.tools import ToolRunner, safe_tool_name, tool_schemas
from app.errors import TransientProviderError
from app.simulator.remediation import REMEDIATIONS

logger = logging.getLogger("agentops.agent")

MAX_FINAL_TEXT_CHARS = 20_000

FAILURE_MESSAGES: dict[FailureCode, str] = {
    "provider_error": "The model provider failed, so no findings were produced.",
    "budget_exceeded": "The investigation stopped because it exceeded its token budget.",
    "max_rounds_exceeded": "The investigation stopped because it reached its limit of rounds without an answer.",
    "invalid_output": "The model's answer was malformed or did not match the required schema, so it was rejected.",
    "ungrounded_evidence": (
        "The model's answer cited evidence that was not retrieved in this investigation, so it was rejected."
    ),
    "remediation_not_allowed": (
        "The model proposed a remediation that is not allowed for this incident, so the answer was rejected."
    ),
    "internal_error": "The investigation failed unexpectedly.",
}


@dataclass(frozen=True)
class Limits:
    max_rounds: int
    max_total_tokens: int
    max_output_tokens: int
    max_tool_calls: int = 12
    max_calls_per_round: int = 6


def answer_schema() -> dict[str, Any]:
    """FinalAnswer's JSON Schema with remediation_action_id narrowed to the real allowlist (or null).
    This only guides a model; validate_answer still enforces it."""
    schema = FinalAnswer.model_json_schema()
    schema["properties"]["remediation_action_id"] = {
        "anyOf": [{"type": "string", "enum": sorted(REMEDIATIONS)}, {"type": "null"}],
        "title": "Remediation Action Id",
    }
    return schema


class _Reject(Exception):
    def __init__(self, code: FailureCode) -> None:
        super().__init__(code)
        self.code = code


def _normalise_ids(calls: tuple[ToolCallRequest, ...], taken: int) -> tuple[ToolCallRequest, ...]:
    """Providers should send unique ids; if one does not, make them unique rather than trust them."""
    seen: set[str] = set()
    out: list[ToolCallRequest] = []
    for i, c in enumerate(calls, 1):
        cid = c.id if c.id and c.id not in seen and len(c.id) <= 80 else f"call-{taken + i}"
        seen.add(cid)
        out.append(ToolCallRequest(id=cid, name=c.name, arguments=c.arguments))
    return tuple(out)


def validate_answer(text: str, grounded: dict[str, EvidenceSource], service: str) -> Findings:
    """Turn raw provider text into Findings, or raise _Reject. Pure; no provider or tool access."""
    if len(text) > MAX_FINAL_TEXT_CHARS:
        raise _Reject("invalid_output")
    try:
        answer = FinalAnswer.model_validate_json(text)
    except ValidationError:
        raise _Reject("invalid_output") from None

    if answer.confidence != "low" and not answer.evidence:
        raise _Reject("ungrounded_evidence")
    for item in answer.evidence:
        if grounded.get(item.reference_id) != item.source:
            raise _Reject("ungrounded_evidence")

    remediation: Remediation | None = None
    if answer.remediation_action_id is not None:
        spec = REMEDIATIONS.get(answer.remediation_action_id)
        if spec is None or service not in spec.applicable_services or answer.confidence == "low":
            raise _Reject("remediation_not_allowed")
        remediation = Remediation(
            action_id=spec.action_id, description=spec.description, expected_effect=spec.expected_effect
        )

    return Findings(
        summary=answer.summary,
        root_cause_hypothesis=answer.root_cause_hypothesis,
        confidence=answer.confidence,
        confidence_rationale=answer.confidence_rationale,
        evidence=answer.evidence,
        alternative_hypotheses=answer.alternative_hypotheses,
        recommended_next_step=answer.recommended_next_step,
        remediation=remediation,
        limitations=answer.limitations,
    )


class Coordinator:
    def __init__(
        self,
        provider: LLMProvider,
        runner: ToolRunner,
        limits: Limits,
        clock: Callable[[], datetime],
    ) -> None:
        self._provider = provider
        self._runner = runner
        self._limits = limits
        self._clock = clock

    def _complete(self, request: CompletionRequest) -> CompletionResponse:
        try:
            return self._provider.complete(request)
        except TransientProviderError:
            return self._provider.complete(request)  # one retry; tools are read-only so this is safe

    def run(self, initial: Investigation, service: str, publish: Callable[[Investigation], None]) -> Investigation:
        limits = self._limits
        messages: list[Message] = [Message("user", USER_PROMPT.format(incident_id=initial.incident_id))]
        grounded: dict[str, EvidenceSource] = {}
        records: list[ToolCallRecord] = []
        rounds = tokens_in = tokens_out = 0

        def snapshot(**update: object) -> Investigation:
            usage = Usage(rounds=rounds, tool_calls=len(records), input_tokens=tokens_in, output_tokens=tokens_out)
            return initial.model_copy(update={"tool_calls": list(records), "usage": usage, **update})

        def fail(code: FailureCode) -> Investigation:
            logger.info("investigation %s failed: %s", initial.id, code)
            return snapshot(
                status="failed", failure=Failure(code=code, message=FAILURE_MESSAGES[code]), finished_at=self._clock()
            )

        for _ in range(limits.max_rounds):
            if tokens_in + tokens_out >= limits.max_total_tokens:
                return fail("budget_exceeded")
            rounds += 1
            request = CompletionRequest(
                system=system_prompt(),
                messages=tuple(messages),
                tools=tool_schemas(),
                answer_schema=answer_schema(),
                max_output_tokens=limits.max_output_tokens,
            )
            started = time.perf_counter()
            try:
                response = self._complete(request)
            except Exception as exc:
                logger.error("investigation %s provider error: %s", initial.id, type(exc).__name__)
                return fail("provider_error")
            tokens_in += response.usage.input_tokens
            tokens_out += response.usage.output_tokens
            logger.info(
                "investigation %s round=%d latency_ms=%d tokens_in=%d tokens_out=%d",
                initial.id,
                rounds,
                int((time.perf_counter() - started) * 1000),
                response.usage.input_tokens,
                response.usage.output_tokens,
            )
            if tokens_in + tokens_out > limits.max_total_tokens:
                return fail("budget_exceeded")

            if bool(response.tool_calls) == (response.final_text is not None):
                return fail("invalid_output")  # neither, or both

            if response.final_text is not None:
                try:
                    findings = validate_answer(response.final_text, grounded, service)
                except _Reject as rejected:
                    return fail(rejected.code)
                state = RemediationRecord(action_id=findings.remediation.action_id) if findings.remediation else None
                return snapshot(
                    status="completed", findings=findings, remediation_state=state, finished_at=self._clock()
                )

            calls = _normalise_ids(response.tool_calls, len(records))
            messages.append(Message("assistant", tool_calls=calls))
            for index, call in enumerate(calls):
                record_id = f"call-{len(records) + 1}"
                if index >= limits.max_calls_per_round or len(records) >= limits.max_tool_calls:
                    name = safe_tool_name(call.name)
                    message = "Too many tool calls requested; this call was not run."
                    records.append(
                        ToolCallRecord(
                            id=record_id,
                            name=name,
                            arguments={},
                            status="error",
                            error_code="too_many_tool_calls",
                            duration_ms=0,
                            result_summary=message,
                            reference_ids=[],
                            at=self._clock(),
                        )
                    )
                    payload = {"error": {"code": "too_many_tool_calls", "message": message}}
                    messages.append(Message("tool", encode_tool_result(name, payload), tool_call_id=call.id))
                    continue

                outcome = self._runner.run(initial.incident_id, call.name, call.arguments)
                logger.info(
                    "investigation %s tool=%s status=%s duration_ms=%d",
                    initial.id,
                    outcome.name,
                    "ok" if outcome.ok else outcome.error_code,
                    outcome.duration_ms,
                )
                grounded.update(outcome.reference_sources)
                records.append(
                    ToolCallRecord(
                        id=record_id,
                        name=outcome.name,
                        arguments=outcome.arguments,
                        status="ok" if outcome.ok else "error",
                        error_code=outcome.error_code,
                        duration_ms=outcome.duration_ms,
                        result_summary=outcome.summary,
                        reference_ids=list(outcome.reference_sources),
                        at=self._clock(),
                    )
                )
                content = encode_tool_result(outcome.name, outcome.payload)
                messages.append(Message("tool", content, tool_call_id=call.id))
                publish(snapshot())

        return fail("max_rounds_exceeded")
