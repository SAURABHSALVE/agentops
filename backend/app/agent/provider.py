"""The one interface between the coordinator and any model.

The coordinator never imports a vendor SDK. A provider takes the conversation so far and returns
either tool calls or a final answer, as raw text exactly as a real model would produce it. The
backend parses and validates that text; nothing a provider returns is trusted.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.agent.models import ProviderInfo, TokenUsage


@dataclass(frozen=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: str  # raw JSON text, as sent by a model


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCallRequest, ...] = ()  # assistant turns only
    tool_call_id: str | None = None  # tool turns only


@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


@dataclass(frozen=True)
class CompletionRequest:
    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSchema, ...]
    answer_schema: dict[str, Any]  # JSON Schema of the final answer
    max_output_tokens: int


@dataclass(frozen=True)
class CompletionResponse:
    """Exactly one of `tool_calls` or `final_text` is expected; anything else is rejected."""

    tool_calls: tuple[ToolCallRequest, ...] = ()
    final_text: str | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)


class LLMProvider(Protocol):
    @property
    def info(self) -> ProviderInfo: ...

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Raise TransientProviderError (retried once) or ProviderError (not retried)."""
        ...
