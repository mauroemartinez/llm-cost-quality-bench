"""OpenAI chat completions over plain HTTP. Cached input tokens come from
usage.prompt_tokens_details.cached_tokens and are a subset of prompt_tokens."""

from __future__ import annotations

from ..schemas import Completion, Usage
from .base import api_key, client, completion, timed_post

URL = "https://api.openai.com/v1/chat/completions"


def supports_temperature(model: str) -> bool:
    # Reasoning-first families reject a custom temperature; the 4.x family accepts it.
    return model.startswith("gpt-4")


class OpenAIProvider:
    name = "openai"

    def __init__(self) -> None:
        self.http = client()

    def complete(self, model: str, system: str, user: str, *, max_tokens: int = 400, temperature: float | None = 0.0) -> Completion:
        body: dict = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_completion_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        if temperature is not None and supports_temperature(model):
            body["temperature"] = temperature
        data, latency_ms = timed_post(self.http, URL, headers={"Authorization": f"Bearer {api_key('OPENAI_API_KEY')}"}, body=body)
        text = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        return completion(
            self.name,
            model,
            text,
            Usage(
                input_tokens=int(usage.get("prompt_tokens", 0)),
                cached_tokens=int(details.get("cached_tokens", 0) or 0),
                output_tokens=int(usage.get("completion_tokens", 0)),
                exact=bool(usage),
            ),
            latency_ms,
        )
