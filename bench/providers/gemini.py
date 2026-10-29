"""Gemini API over plain HTTP. promptTokenCount already includes cached
tokens; thinking tokens are billed as output, so they are added to it."""

from __future__ import annotations

from ..schemas import Completion, Usage
from .base import api_key, client, completion, timed_post

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiProvider:
    name = "gemini"

    def __init__(self) -> None:
        self.http = client()

    def complete(self, model: str, system: str, user: str, *, max_tokens: int = 400, temperature: float | None = 0.0) -> Completion:
        generation: dict = {"maxOutputTokens": max_tokens, "responseMimeType": "application/json"}
        if temperature is not None:
            generation["temperature"] = temperature
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": generation,
        }
        headers = {"x-goog-api-key": api_key("GEMINI_API_KEY")}
        data, latency_ms = timed_post(self.http, URL.format(model=model), headers=headers, body=body)
        candidates = data.get("candidates") or []
        parts = (candidates[0].get("content") or {}).get("parts", []) if candidates else []
        text = "".join(part.get("text", "") for part in parts)
        usage = data.get("usageMetadata") or {}
        return completion(
            self.name,
            model,
            text,
            Usage(
                input_tokens=int(usage.get("promptTokenCount", 0)),
                cached_tokens=int(usage.get("cachedContentTokenCount", 0) or 0),
                output_tokens=int(usage.get("candidatesTokenCount", 0)) + int(usage.get("thoughtsTokenCount", 0) or 0),
                exact=bool(usage),
            ),
            latency_ms,
        )
