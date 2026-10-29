"""Anthropic Messages API over plain HTTP. The system prompt is marked
cacheable; in the usage, input_tokens excludes cached tokens, so the total
input is input + cache_read + cache_creation and the cached part is cache_read.
Cache writes cost a little more than plain input; we price them as plain input,
which slightly understates the first call of each prompt."""

from __future__ import annotations

from ..schemas import Completion, Usage
from .base import api_key, client, completion, timed_post

URL = "https://api.anthropic.com/v1/messages"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self) -> None:
        self.http = client()

    def complete(self, model: str, system: str, user: str, *, max_tokens: int = 400, temperature: float | None = 0.0) -> Completion:
        body: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }
        if temperature is not None:
            body["temperature"] = temperature
        headers = {"x-api-key": api_key("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01"}
        data, latency_ms = timed_post(self.http, URL, headers=headers, body=body)
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        usage = data.get("usage") or {}
        uncached = int(usage.get("input_tokens", 0))
        cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
        cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
        return completion(
            self.name,
            model,
            text,
            Usage(
                input_tokens=uncached + cache_read + cache_creation,
                cached_tokens=cache_read,
                output_tokens=int(usage.get("output_tokens", 0)),
                exact=bool(usage),
            ),
            latency_ms,
        )
