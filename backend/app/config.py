import os
from dataclasses import dataclass

APP_VERSION = "0.1.0"


class ConfigError(Exception):
    """Invalid or unsupported configuration. Raised at startup, never while serving a request."""


def _int_env(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer.") from None
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}.")
    return value


@dataclass(frozen=True)
class Settings:
    cors_allow_origins: tuple[str, ...] = ("http://localhost:3000",)
    # Which LLMProvider drives investigations. Only "mock" exists until Phase 3B.
    agent_provider: str = "mock"
    # Bounds on every model-driven run (see PLAN.md, "Bounds on every model-driven run").
    agent_max_rounds: int = 6
    agent_max_total_tokens: int = 20_000
    max_output_tokens: int = 1_500
    tool_timeout_seconds: int = 5
    tool_result_max_bytes: int = 16_384

    @classmethod
    def from_env(cls) -> "Settings":
        raw = os.environ.get("CORS_ALLOW_ORIGINS", "")
        origins = tuple(o.strip() for o in raw.split(",") if o.strip()) or cls.cors_allow_origins
        provider = os.environ.get("AGENT_PROVIDER", "").strip().lower() or cls.agent_provider
        if provider == "openai":
            raise ConfigError("AGENT_PROVIDER=openai is not available yet (Phase 3B). Use 'mock'.")
        if provider != "mock":
            raise ConfigError("AGENT_PROVIDER must be 'mock'.")
        return cls(
            cors_allow_origins=origins,
            agent_provider=provider,
            agent_max_rounds=_int_env("AGENT_MAX_ROUNDS", cls.agent_max_rounds, 1, 20),
            agent_max_total_tokens=_int_env("AGENT_MAX_TOTAL_TOKENS", cls.agent_max_total_tokens, 1, 500_000),
            max_output_tokens=_int_env("OPENAI_MAX_OUTPUT_TOKENS", cls.max_output_tokens, 1, 16_000),
            tool_timeout_seconds=_int_env("TOOL_TIMEOUT_SECONDS", cls.tool_timeout_seconds, 1, 60),
            tool_result_max_bytes=_int_env("TOOL_RESULT_MAX_BYTES", cls.tool_result_max_bytes, 1_024, 1_048_576),
        )
