import os
import re
from dataclasses import dataclass
from pathlib import Path

APP_VERSION = "0.1.0"
COGNITO_POOL_ID_PATTERN = r"[a-z]{2}(?:-[a-z]+)+-\d_[A-Za-z0-9]{6,32}"
# https anywhere, plain http only for a local fake issuer; an optional path, never a query or fragment.
_ISSUER_URL = re.compile(r"(?:https://[A-Za-z0-9.-]+|http://(?:127\.0\.0\.1|localhost))(?::\d+)?(?:/[A-Za-z0-9._-]+)*")


_ACCOUNT_ID = re.compile(r"\d{12}")
# The one place a trust policy may point: that account's root, or one named role in it (no paths, no wildcards).
_PRINCIPAL_ARN = re.compile(r"arn:aws:iam::(\d{12}):(?:root|role/[A-Za-z0-9+=,.@_-]{1,64})")


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
    # The same caps for one signed-in user (or for the shared anonymous owner when sign-in is off). Generous locally,
    # where one person runs many investigations in a row; the AWS stack sets stricter values.
    user_rate_per_minute: int = 30
    user_rate_per_hour: int = 300
    # Where records live: "local" (SQLite file), "memory" (tests), or "dynamodb" (needs dynamodb_table).
    storage_mode: str = "local"
    dynamodb_table: str = ""
    local_data_dir: str = "./.data"
    # Path to a dataset made by `python -m lab.capture`. Empty (default) serves the built-in simulated incidents.
    telemetry_dataset: str = ""
    # Amazon Cognito user pool id (e.g. us-east-1_AbCdEf123). Empty (default) means no sign-in: every caller is one
    # shared anonymous owner, exactly as before. When set, writes need a signed-in user and records are per user.
    cognito_user_pool_id: str = ""
    # Test seam: a full issuer URL to use instead of the pool's own (a local fake issuer). Not set in AWS.
    cognito_issuer_override: str = ""
    # Connecting a user's own AWS account (Phase 16). The feature is off unless the account AgentOps itself runs in is
    # named here AND sign-in is on: a connection belongs to one signed-in user and is never shared.
    aws_connect_account_id: str = ""
    # Who the user's read-only role will trust. Empty means that account's root (any principal in it that is allowed to
    # assume roles); a role ARN narrows it to exactly one role, which is what a deployed stack should set.
    aws_connect_principal_arn: str = ""
    # Which customer accounts may be registered at all. Empty means only AgentOps's own account, so the first rollout is
    # the owner's account and nobody else's; widening it is a deliberate, reviewed change.
    # The single entry "*" means any account (the usual way a monitoring service works): safe because each user's role
    # trusts only this app and only with that user's own ExternalId, which the server makes and never repeats.
    aws_connect_allowed_accounts: tuple[str, ...] = ()
    # Daily caps on investigations while a real AI model is in use (each costs money). 0 means no cap. Counted in the
    # database, so they hold across servers; the offline assistant is free and never capped.
    user_investigations_per_day: int = 0
    investigations_per_day: int = 0

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
        pool = os.environ.get("COGNITO_USER_POOL_ID", "").strip()
        if pool and not re.fullmatch(COGNITO_POOL_ID_PATTERN, pool):
            raise ConfigError("COGNITO_USER_POOL_ID must look like 'us-east-1_AbCdEf123'.")
        issuer = os.environ.get("COGNITO_ISSUER", "").strip().rstrip("/")
        if issuer and not _ISSUER_URL.fullmatch(issuer):
            raise ConfigError("COGNITO_ISSUER must be an https URL (http only for localhost).")
        if issuer and not pool:
            raise ConfigError("COGNITO_ISSUER only applies together with COGNITO_USER_POOL_ID.")
        connect_account = os.environ.get("AWS_CONNECT_ACCOUNT_ID", "").strip()
        if connect_account and not _ACCOUNT_ID.fullmatch(connect_account):
            raise ConfigError("AWS_CONNECT_ACCOUNT_ID must be a 12-digit AWS account id.")
        principal = os.environ.get("AWS_CONNECT_PRINCIPAL_ARN", "").strip()
        if principal:
            match = _PRINCIPAL_ARN.fullmatch(principal)
            if not connect_account or match is None or match.group(1) != connect_account:
                raise ConfigError("AWS_CONNECT_PRINCIPAL_ARN must be a role or root ARN in AWS_CONNECT_ACCOUNT_ID.")
        allowed = tuple(a.strip() for a in os.environ.get("AWS_CONNECT_ALLOWED_ACCOUNTS", "").split(",") if a.strip())
        if allowed and not connect_account:
            raise ConfigError("AWS_CONNECT_ALLOWED_ACCOUNTS only applies together with AWS_CONNECT_ACCOUNT_ID.")
        if allowed != ("*",) and any(not _ACCOUNT_ID.fullmatch(a) for a in allowed):
            raise ConfigError(
                "AWS_CONNECT_ALLOWED_ACCOUNTS must be a comma-separated list of 12-digit account ids, or just *."
            )
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
            user_rate_per_minute=_int_env("USER_INVESTIGATIONS_PER_MINUTE", cls.user_rate_per_minute, 1, 10_000),
            user_rate_per_hour=_int_env("USER_INVESTIGATIONS_PER_HOUR", cls.user_rate_per_hour, 1, 100_000),
            storage_mode=storage_mode,
            dynamodb_table=table,
            local_data_dir=os.environ.get("LOCAL_DATA_DIR", "").strip() or cls.local_data_dir,
            telemetry_dataset=os.environ.get("TELEMETRY_DATASET", "").strip(),
            cognito_user_pool_id=pool,
            cognito_issuer_override=issuer,
            aws_connect_account_id=connect_account,
            aws_connect_principal_arn=principal,
            aws_connect_allowed_accounts=allowed,
            user_investigations_per_day=_int_env("USER_INVESTIGATIONS_PER_DAY", 0, 0, 100_000),
            investigations_per_day=_int_env("INVESTIGATIONS_PER_DAY", 0, 0, 10_000_000),
        )
