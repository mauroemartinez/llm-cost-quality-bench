"""Pick a provider by name and wrap it in a cassette (except the mock)."""

from __future__ import annotations

import os
from pathlib import Path

from .anthropic import AnthropicProvider
from .base import Provider, ProviderError, parse_json
from .cassette import Cassette, CassetteMiss
from .deepseek import DeepSeekProvider
from .gemini import GeminiProvider
from .mock import MockProvider
from .openai import OpenAIProvider

CASSETTES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "cassettes"
KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}
FACTORIES = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "deepseek": DeepSeekProvider,
    "mock": MockProvider,
}


def has_key(provider: str) -> bool:
    env = KEY_ENV.get(provider)
    return bool(env and os.environ.get(env, "").strip())


def resolve_mode(provider: str, requested: str | None) -> str:
    """offline when there is no key, whatever was asked; otherwise what was asked
    (default from BENCH_MODE, else record)."""
    if provider == "mock":
        return "live"
    if not has_key(provider):
        return "offline"
    return requested or os.environ.get("BENCH_MODE", "record")


def get_provider(name: str, mode: str | None = None, cassettes: Path = CASSETTES) -> Provider:
    if name not in FACTORIES:
        raise ProviderError(f"unknown provider {name!r}; choose from {sorted(FACTORIES)}")
    inner = FACTORIES[name]()
    if name == "mock":
        return inner
    return Cassette(inner, cassettes, resolve_mode(name, mode))


__all__ = ["Cassette", "CassetteMiss", "Provider", "ProviderError", "get_provider", "has_key", "parse_json", "resolve_mode"]
