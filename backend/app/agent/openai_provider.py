"""OpenAI Chat Completions provider (Phase 3B), over the standard library only.

No SDK: the endpoint is fixed, the request/response shapes below were checked against the live API
(tool_calls with JSON-string arguments, `response_format` json_schema in strict mode, `usage`
prompt/completion tokens), and one less dependency means a smaller supply-chain and Lambda surface.

Trust rules: the API key lives only in this object, is never logged, returned or put in an error
message (provider error bodies can echo parts of it, so they are never surfaced), and the
endpoint cannot be redirected by configuration. Everything returned is still untrusted: the
coordinator parses and validates it exactly as for any other provider.
"""

import json
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from app.agent.models import ProviderInfo, TokenUsage
from app.agent.provider import CompletionRequest, CompletionResponse, Message, ToolCallRequest
from app.errors import ProviderError, TransientProviderError

logger = logging.getLogger("agentops.agent")

ENDPOINT = "https://api.openai.com/v1/chat/completions"
MODEL_NAME = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
MAX_RESPONSE_BYTES = 1_000_000
TRANSIENT_STATUS = {408, 409, 429, 500, 502, 503, 504}

# (url, headers, body, timeout_seconds) -> parsed JSON response. Replaceable in tests.
Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


def _urllib_transport(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310  # fixed https endpoint
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        # Deliberately do not read or surface the body: it can echo parts of the credential.
        kind = TransientProviderError if exc.code in TRANSIENT_STATUS else ProviderError
        raise kind(f"OpenAI returned HTTP {exc.code}") from None
    except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as exc:
        raise TransientProviderError(f"OpenAI request failed: {type(exc).__name__}") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProviderError("OpenAI response too large")
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise ProviderError("OpenAI response was not JSON") from None
    if not isinstance(parsed, dict):
        raise ProviderError("OpenAI response had an unexpected shape")
    return parsed


def _to_wire(message: Message) -> dict[str, Any]:
    if message.role == "assistant":
        wire: dict[str, Any] = {"role": "assistant", "content": message.content or None}
        if message.tool_calls:
            wire["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in message.tool_calls
            ]
        return wire
    if message.role == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    return {"role": "user", "content": message.content}


class OpenAIProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 30,
        transport: Transport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key is required")
        if not MODEL_NAME.fullmatch(model):
            raise ValueError("model name is invalid")
        self._key = api_key.strip()
        self._model = model
        self._timeout = timeout_seconds
        self._transport = transport or _urllib_transport

    def __repr__(self) -> str:  # never leak the key through repr/logging of the object
        return f"OpenAIProvider(model={self._model!r})"

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(kind="openai", label=f"OpenAI {self._model} (live language model)")

    def _body(self, request: CompletionRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "system", "content": request.system}, *(_to_wire(m) for m in request.messages)],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in request.tools
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "final_answer", "strict": True, "schema": request.answer_schema},
            },
            "max_completion_tokens": request.max_output_tokens,
        }
        if not self._model.startswith(("gpt-5", "o1", "o3", "o4")):  # reasoning models reject temperature
            body["temperature"] = 0
        return body

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        data = self._transport(ENDPOINT, headers, self._body(request), self._timeout)
        try:
            choice = data["choices"][0]
            message = choice["message"]
            usage = data.get("usage") or {}
            tokens = TokenUsage(
                input_tokens=int(usage.get("prompt_tokens", 0)), output_tokens=int(usage.get("completion_tokens", 0))
            )
            if message.get("refusal"):
                raise ProviderError("OpenAI model refused the request")
            raw_calls = message.get("tool_calls") or []
            calls = tuple(
                ToolCallRequest(
                    id=str(c.get("id", "")),
                    name=str(c["function"]["name"]),
                    arguments=str(c["function"].get("arguments", "")),
                )
                for c in raw_calls
                if c.get("type", "function") == "function"
            )
            content = message.get("content")
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise ProviderError("OpenAI response had an unexpected shape") from None
        if calls:
            return CompletionResponse(tool_calls=calls, usage=tokens)
        return CompletionResponse(final_text=content if isinstance(content, str) else None, usage=tokens)
