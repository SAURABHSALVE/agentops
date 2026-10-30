"""Builds the configured provider. The only place that reads the OpenAI key from the environment."""

import os

from app.agent.mock_provider import MockProvider
from app.agent.openai_provider import OpenAIProvider
from app.agent.provider import LLMProvider
from app.config import ConfigError, Settings


def build_provider(settings: Settings) -> LLMProvider:
    if settings.agent_provider == "mock":
        return MockProvider()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key or key.startswith("REPLACE_ME"):
        raise ConfigError("OPENAI_API_KEY is not set (put it in backend/.env or the environment).")
    return OpenAIProvider(key, settings.openai_model, timeout_seconds=settings.openai_timeout_seconds)
