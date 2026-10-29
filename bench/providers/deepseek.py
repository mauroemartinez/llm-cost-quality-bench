"""DeepSeek, OpenAI-compatible endpoint. Cache hits and misses are reported
separately (prompt_cache_hit_tokens / prompt_cache_miss_tokens). Thinking
mode is on by default on their side; this benchmark turns it off unless
BENCH_DEEPSEEK_THINKING=1, because the stages here are short structured
tasks and reasoning tokens are billed as output."""

from __future__ import annotations

import os

from ..schemas import Completion, Usage
from .base import api_key, client, completion, timed_post

URL = "https://api.deepseek.com/chat/completions"


class DeepSeekProvider:
    name = "deepseek"

    def __init__(self) -> None:
        self.http = client()

    def complete(self, model: str, system: str, user: str, *, max_tokens: int = 400, temperature: float | None = 0.0) -> Completion:
        thinking = os.environ.get("BENCH_DEEPSEEK_THINKING") == "1"
        body: dict = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens if not thinking else max(max_tokens, 4000),
            "response_format": {"type": "json_object"},
            "thinking": {"type": "enabled" if thinking else "disabled"},
        }
        if temperature is not None:
            body["temperature"] = temperature
        data, latency_ms = timed_post(self.http, URL, headers={"Authorization": f"Bearer {api_key('DEEPSEEK_API_KEY')}"}, body=body)
        text = data["choices"][0]["message"].get("content") or ""
        usage = data.get("usage") or {}
        return completion(
            self.name,
            model,
            text,
            Usage(
                input_tokens=int(usage.get("prompt_tokens", 0)),
                cached_tokens=int(usage.get("prompt_cache_hit_tokens", 0) or 0),
                output_tokens=int(usage.get("completion_tokens", 0)),
                exact=bool(usage),
            ),
            latency_ms,
        )
