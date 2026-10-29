"""One interface for every provider, and the JSON parsing they all share."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Protocol

import httpx

from ..schemas import Completion, Usage

FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class Provider(Protocol):
    name: str

    def complete(self, model: str, system: str, user: str, *, max_tokens: int = 400, temperature: float | None = 0.0) -> Completion: ...


class ProviderError(RuntimeError):
    pass


def parse_json(text: str) -> dict:
    """Models wrap JSON in fences or prose; take the first object that parses."""
    candidate = text.strip()
    fenced = FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        value = json.loads(candidate)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the answer")
    value = json.loads(candidate[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("JSON is not an object")
    return value


def api_key(env_name: str) -> str:
    key = os.environ.get(env_name, "").strip()
    if not key:
        raise ProviderError(f"{env_name} is not set; run offline (BENCH_MODE=offline) or add the key to .env")
    return key


def client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(90.0, connect=15.0))


def timed_post(http: httpx.Client, url: str, *, headers: dict, body: dict) -> tuple[dict, float]:
    started = time.perf_counter()
    response = http.post(url, headers=headers, json=body)
    latency_ms = (time.perf_counter() - started) * 1000
    if response.status_code >= 400:
        raise ProviderError(f"{url} returned {response.status_code}: {response.text[:300]}")
    return response.json(), latency_ms


def completion(provider: str, model: str, text: str, usage: Usage, latency_ms: float) -> Completion:
    return Completion(text=text, usage=usage, latency_ms=latency_ms, model=model, provider=provider)
