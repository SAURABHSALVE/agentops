import os
import re
from dataclasses import dataclass
from pathlib import Path

APP_VERSION = "0.1.0"


class ConfigError(Exception):
    """Invalid or unsupported configuration. Raised at startup, never while serving a request."""


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines from a git-ignored .env into os.environ without overriding real
    environment variables. Values are never logged or returned. Missing file is fine."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", name) and name not in os.environ:
            os.environ[name] = value


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
    # Which LLMProvider drives investigations: "mock" (default, offline) or "openai" (needs key + model).
    agent_provider: str = "mock"
    openai_model: str = ""
    openai_timeout_seconds: int = 30
    # Bounds on every model-driven run (see PLAN.md, "Bounds on every model-driven run").
    agent_max_rounds: int = 6
    agent_max_total_tokens: int = 20_000
    max_output_tokens: int = 1_500
    tool_timeout_seconds: int = 5
    tool_result_max_bytes: int = 16_384
    # Public-demo backstops (per process). Generous locally; the AWS stack sets lower values.
    rate_per_minute: int = 30
    rate_per_hour: int = 300
    # Where records live: "local" (SQLite file), "memory" (tests), or "dynamodb" (needs dynamodb_table).
    storage_mode: str = "local"
    dynamodb_table: str = ""
    local_data_dir: str = "./.data"
    # Path to a dataset made by `python -m lab.capture`. Empty (default) serves the built-in simulated incidents.
    telemetry_dataset: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        raw = os.environ.get("CORS_ALLOW_ORIGINS", "")
        origins = tuple(o.strip() for o in raw.split(",") if o.strip()) or cls.cors_allow_origins
        provider = os.environ.get("AGENT_PROVIDER", "").strip().lower() or cls.agent_provider
        if provider not in ("mock", "openai"):
            raise ConfigError("AGENT_PROVIDER must be 'mock' or 'openai'.")
        model = os.environ.get("OPENAI_MODEL", "").strip()
        if provider == "openai" and not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", model):
            raise ConfigError("OPENAI_MODEL must be set to a model name when AGENT_PROVIDER=openai.")
        storage_mode = os.environ.get("STORAGE_MODE", "").strip().lower() or cls.storage_mode
        if storage_mode not in ("local", "memory", "dynamodb"):
            raise ConfigError("STORAGE_MODE must be 'local', 'memory' or 'dynamodb'.")
        table = os.environ.get("DYNAMODB_TABLE", "").strip()
        if storage_mode == "dynamodb" and not table:
            raise ConfigError("DYNAMODB_TABLE is required when STORAGE_MODE=dynamodb.")
        return cls(
            cors_allow_origins=origins,
            agent_provider=provider,
            openai_model=model,
            openai_timeout_seconds=_int_env("OPENAI_TIMEOUT_SECONDS", cls.openai_timeout_seconds, 1, 300),
            agent_max_rounds=_int_env("AGENT_MAX_ROUNDS", cls.agent_max_rounds, 1, 20),
            agent_max_total_tokens=_int_env("AGENT_MAX_TOTAL_TOKENS", cls.agent_max_total_tokens, 1, 500_000),
            max_output_tokens=_int_env("OPENAI_MAX_OUTPUT_TOKENS", cls.max_output_tokens, 1, 16_000),
            tool_timeout_seconds=_int_env("TOOL_TIMEOUT_SECONDS", cls.tool_timeout_seconds, 1, 60),
            tool_result_max_bytes=_int_env("TOOL_RESULT_MAX_BYTES", cls.tool_result_max_bytes, 1_024, 1_048_576),
            rate_per_minute=_int_env("INVESTIGATIONS_PER_MINUTE", cls.rate_per_minute, 1, 10_000),
            rate_per_hour=_int_env("INVESTIGATIONS_PER_HOUR", cls.rate_per_hour, 1, 100_000),
            storage_mode=storage_mode,
            dynamodb_table=table,
            local_data_dir=os.environ.get("LOCAL_DATA_DIR", "").strip() or cls.local_data_dir,
            telemetry_dataset=os.environ.get("TELEMETRY_DATASET", "").strip(),
        )
