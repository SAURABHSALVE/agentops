"""Test-only providers and builders. Never imported by app code (a test enforces this).

ScriptedProvider replays crafted responses, including malicious or malformed ones, and records
every request so tests can inspect exactly what the backend sent to a "model".
"""

import json
from typing import Any

from app.agent.models import ProviderInfo, TokenUsage
from app.agent.provider import CompletionRequest, CompletionResponse, ToolCallRequest


class ScriptedProvider:
    def __init__(self, *script: CompletionResponse | Exception) -> None:
        self._script = list(script)
        self.requests: list[CompletionRequest] = []

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(kind="mock", label="Scripted test provider")

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        if not self._script:
            raise AssertionError("ScriptedProvider ran out of responses")
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def call(name: str, arguments: dict[str, Any] | str | None = None, call_id: str = "c1") -> ToolCallRequest:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments or {})
    return ToolCallRequest(id=call_id, name=name, arguments=raw)


def calls(*requests: ToolCallRequest, tokens: int = 0) -> CompletionResponse:
    return CompletionResponse(tool_calls=tuple(requests), usage=TokenUsage(input_tokens=tokens))


def answer_dict(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "summary": "Scripted summary.",
        "root_cause_hypothesis": "Scripted hypothesis.",
        "confidence": "low",
        "confidence_rationale": "Scripted rationale.",
        "evidence": [],
        "alternative_hypotheses": [],
        "recommended_next_step": "Scripted next step.",
        "remediation_action_id": None,
        "limitations": [],
    }
    base.update(overrides)
    return base


def final(text: str | None = None, tokens: int = 0, **overrides: Any) -> CompletionResponse:
    return CompletionResponse(
        final_text=text if text is not None else json.dumps(answer_dict(**overrides)),
        usage=TokenUsage(output_tokens=tokens),
    )
