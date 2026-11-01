"""Builds the configured provider. The only place that reads the OpenAI key from the environment."""

import os

from app.agent.mock_provider import MockProvider
from app.agent.openai_provider import OpenAIProvider
from app.agent.provider import LLMProvider
from app.config import ConfigError, Settings


def _read_secret(arn: str) -> str:
    """Fetched once at start-up. The value is never logged; failures do not echo AWS error text."""
    try:
        import boto3  # lazy: local runs never need AWS

        return str(boto3.client("secretsmanager").get_secret_value(SecretId=arn).get("SecretString", "")).strip()
    except Exception:
        raise ConfigError("OPENAI_SECRET_ARN could not be read.") from None


def build_provider(settings: Settings) -> LLMProvider:
    if settings.agent_provider == "mock":
        return MockProvider()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    secret_arn = os.environ.get("OPENAI_SECRET_ARN", "").strip()  # deployed: the key lives in Secrets Manager
    if not key and secret_arn:
        key = _read_secret(secret_arn)
    if not key or key.startswith("REPLACE_ME"):
        raise ConfigError("OPENAI_API_KEY is not set (put it in backend/.env, the environment or a secret).")
    return OpenAIProvider(key, settings.openai_model, timeout_seconds=settings.openai_timeout_seconds)
