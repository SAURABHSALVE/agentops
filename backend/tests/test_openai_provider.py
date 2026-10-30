"""The OpenAI provider, tested offline against a fake transport. No network, no key."""

import json
import urllib.error
from typing import Any

import pytest

from app.agent.coordinator import answer_schema
from app.agent.factory import build_provider
from app.agent.openai_provider import ENDPOINT, OpenAIProvider
from app.agent.provider import CompletionRequest, Message, ToolCallRequest, ToolSchema
from app.config import ConfigError, Settings, load_env_file
from app.errors import ProviderError, TransientProviderError
from app.simulator.service import Simulator
from tests.harness import run_investigation

KEY = "sk-test-SECRET-KEY-123"


class FakeTransport:
    def __init__(self, *responses: dict[str, Any] | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str], dict[str, Any], float]] = []

    def __call__(self, url: str, headers: dict[str, str], body: dict[str, Any], timeout: float) -> dict[str, Any]:
        self.calls.append((url, headers, body, timeout))
        step = self.responses.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def reply(message: dict[str, Any], prompt: int = 11, completion: int = 7) -> dict[str, Any]:
    return {
        "choices": [{"index": 0, "message": {"role": "assistant", **message}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }


def request() -> CompletionRequest:
    return CompletionRequest(
        system="SYS",
        messages=(
            Message("user", "go"),
            Message("assistant", tool_calls=(ToolCallRequest("c1", "get_incident", "{}"),)),
            Message("tool", "<tool_result/>", tool_call_id="c1"),
        ),
        tools=(ToolSchema("get_incident", "d", {"type": "object", "properties": {}}),),
        answer_schema=answer_schema(),
        max_output_tokens=321,
    )


def provider(
    *responses: dict[str, Any] | Exception, model: str = "gpt-4.1-mini"
) -> tuple[OpenAIProvider, FakeTransport]:
    t = FakeTransport(*responses)
    return OpenAIProvider(KEY, model, timeout_seconds=9, transport=t), t


def test_request_is_shaped_for_chat_completions_with_tools_strict_schema_and_bounds() -> None:
    p, t = provider(reply({"content": "{}"}))
    p.complete(request())
    url, headers, body, timeout = t.calls[0]
    assert url == ENDPOINT and url.startswith("https://api.openai.com/")
    assert headers["Authorization"] == f"Bearer {KEY}" and timeout == 9
    assert body["model"] == "gpt-4.1-mini" and body["max_completion_tokens"] == 321 and body["temperature"] == 0
    assert body["messages"][0] == {"role": "system", "content": "SYS"}
    assert body["messages"][2]["tool_calls"] == [
        {"id": "c1", "type": "function", "function": {"name": "get_incident", "arguments": "{}"}}
    ]
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": "<tool_result/>"}
    assert body["tools"][0]["function"]["name"] == "get_incident"
    rf = body["response_format"]["json_schema"]
    assert rf["strict"] is True and rf["schema"]["additionalProperties"] is False


def test_the_answer_schema_offers_only_allowlisted_remediations_or_null() -> None:
    options = answer_schema()["properties"]["remediation_action_id"]["anyOf"]
    assert options[0]["enum"] == [
        "simulate_clear_dependency_fault",
        "simulate_kill_slow_query",
        "simulate_restart_worker",
    ]
    assert options[1] == {"type": "null"}


def test_reasoning_models_get_no_temperature() -> None:
    p, t = provider(reply({"content": "{}"}), model="gpt-5-mini")
    p.complete(request())
    assert "temperature" not in t.calls[0][2]


def test_tool_calls_and_usage_are_parsed() -> None:
    wire = {
        "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "search_logs", "arguments": '{"query":"x"}'}}
        ]
    }
    p, _ = provider(reply({"content": None, **wire}, prompt=100, completion=5))
    out = p.complete(request())
    assert out.final_text is None
    assert [(c.id, c.name, c.arguments) for c in out.tool_calls] == [("call_1", "search_logs", '{"query":"x"}')]
    assert (out.usage.input_tokens, out.usage.output_tokens) == (100, 5)


def test_final_text_is_returned_raw_for_the_backend_to_validate() -> None:
    p, _ = provider(reply({"content": "not even json"}))
    assert p.complete(request()).final_text == "not even json"


@pytest.mark.parametrize(
    "bad", [{}, {"choices": []}, {"choices": [{}]}, {"choices": [{"message": None}]}, {"choices": "x"}]
)
def test_unexpected_response_shapes_are_provider_errors(bad: dict[str, Any]) -> None:
    p, _ = provider(bad)
    with pytest.raises(ProviderError):
        p.complete(request())


def test_a_refusal_is_a_provider_error() -> None:
    p, _ = provider(reply({"content": None, "refusal": "I cannot help with that."}))
    with pytest.raises(ProviderError):
        p.complete(request())


def test_the_key_never_appears_in_repr_info_or_errors() -> None:
    p, _ = provider(reply({"content": None, "refusal": f"{KEY} leaked"}))
    assert KEY not in repr(p) and KEY not in p.info.label
    with pytest.raises(ProviderError) as err:
        p.complete(request())
    assert KEY not in str(err.value)


def test_model_name_and_key_are_validated() -> None:
    for bad in ("", "gpt 4", "x" * 65, "../../etc", "a;b"):
        with pytest.raises(ValueError):
            OpenAIProvider(KEY, bad)
    with pytest.raises(ValueError):
        OpenAIProvider("  ", "gpt-4.1-mini")


# ---- real transport error mapping (urllib is faked underneath) -----------------------------------


def _http_error(code: int) -> urllib.error.HTTPError:
    import io

    return urllib.error.HTTPError(ENDPOINT, code, "x", {}, io.BytesIO(f'{{"error":"{KEY}"}}'.encode()))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("status", "transient"), [(429, True), (500, True), (503, True), (400, False), (401, False), (404, False)]
)
def test_http_errors_map_to_transient_or_permanent_without_leaking_the_body(
    monkeypatch: pytest.MonkeyPatch, status: int, transient: bool
) -> None:
    def boom(*_: Any, **__: Any) -> Any:
        raise _http_error(status)

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(ProviderError) as err:
        OpenAIProvider(KEY, "gpt-4.1-mini").complete(request())
    assert isinstance(err.value, TransientProviderError) is transient
    assert KEY not in str(err.value) and str(status) in str(err.value)


def test_timeouts_and_connection_failures_are_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    for exc in (TimeoutError("t"), urllib.error.URLError("dns"), ConnectionResetError()):

        def boom(*_: Any, _e: Exception = exc, **__: Any) -> Any:
            raise _e

        monkeypatch.setattr("urllib.request.urlopen", boom)
        with pytest.raises(TransientProviderError):
            OpenAIProvider(KEY, "gpt-4.1-mini").complete(request())


# ---- end to end through the real coordinator ---------------------------------------------------


def test_a_transient_failure_then_success_completes_through_the_coordinator(sim: Simulator) -> None:
    answer: dict[str, Any] = {
        "summary": "s",
        "root_cause_hypothesis": "h",
        "confidence": "low",
        "confidence_rationale": "r",
        "evidence": [],
        "alternative_hypotheses": [],
        "recommended_next_step": "n",
        "remediation_action_id": None,
        "limitations": [],
    }
    p, t = provider(TransientProviderError("HTTP 503"), reply({"content": json.dumps(answer)}))
    done, _ = run_investigation(sim, p, "INC-1004")
    assert done.status == "completed" and len(t.calls) == 2


def test_a_live_style_model_that_obeys_the_injection_is_still_contained(sim: Simulator) -> None:
    calls = [
        {"id": "a", "type": "function", "function": {"name": "search_logs", "arguments": '{"query":"ignore all"}'}}
    ]
    answer: dict[str, Any] = {
        "summary": "s",
        "root_cause_hypothesis": "bad deploy",
        "confidence": "high",
        "confidence_rationale": "r",
        "evidence": [{"source": "logs", "reference_id": "log-514", "observation": "x"}],
        "alternative_hypotheses": [],
        "recommended_next_step": "n",
        "remediation_action_id": "simulate_restart_worker",
        "limitations": [],
    }
    p, _ = provider(reply({"content": None, "tool_calls": calls}), reply({"content": json.dumps(answer)}))
    done, _ = run_investigation(sim, p, "INC-1005")
    assert done.status == "failed" and done.findings is None
    assert done.failure is not None and done.failure.code == "remediation_not_allowed"


# ---- configuration ---------------------------------------------------------------------------


def test_openai_mode_requires_a_model_and_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    with pytest.raises(ConfigError, match="OPENAI_MODEL"):
        Settings.from_env()
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    settings = Settings.from_env()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        build_provider(settings)
    monkeypatch.setenv("OPENAI_API_KEY", "REPLACE_ME_sk-your-key-here")
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        build_provider(settings)
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    assert build_provider(settings).info.kind == "openai"


def test_default_provider_is_the_offline_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_PROVIDER", raising=False)
    assert build_provider(Settings.from_env()).info.kind == "mock"


def test_env_file_loader_does_not_override_real_environment_and_ignores_junk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("ALREADY_SET", "real")
    monkeypatch.delenv("FROM_FILE", raising=False)
    monkeypatch.delenv("QUOTED", raising=False)
    f = tmp_path / ".env"
    f.write_text('# c\nALREADY_SET=file\nFROM_FILE = abc\nQUOTED="q v"\nlower=no\nnonsense\n', encoding="utf-8")
    load_env_file(f)
    import os

    assert (os.environ["ALREADY_SET"], os.environ["FROM_FILE"], os.environ["QUOTED"]) == ("real", "abc", "q v")
    assert "lower" not in os.environ
    load_env_file(tmp_path / "missing.env")  # a missing file is fine
    monkeypatch.delenv("FROM_FILE")
    monkeypatch.delenv("QUOTED")
