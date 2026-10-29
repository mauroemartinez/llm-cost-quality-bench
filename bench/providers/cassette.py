"""Recorded responses, so anyone can run the whole bench with no API key.

A cassette is one JSON file per distinct request (provider, model, prompts,
max_tokens, temperature). `record` calls the API and saves the answer with
its usage and latency; `offline` only replays; `live` calls without saving.
Replays keep the recorded usage, so costs are the real ones, and they cost
nothing today, which the meter knows (from_cassette)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from ..schemas import Completion, Usage
from .base import Provider

MODES = ("offline", "record", "live")


class CassetteMiss(LookupError):
    pass


class Cassette:
    name: str

    def __init__(self, inner: Provider, directory: Path, mode: str = "offline"):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.inner = inner
        self.name = inner.name
        self.directory = directory
        self.mode = mode
        self.hits = 0
        self.misses = 0

    def _path(self, model: str, request: dict) -> Path:
        digest = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]
        return self.directory / f"{self.name}__{model}" / f"{digest}.json"

    def complete(self, model: str, system: str, user: str, *, max_tokens: int = 400, temperature: float | None = 0.0) -> Completion:
        request = {
            "provider": self.name,
            "model": model,
            "system": system,
            "user": user,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        path = self._path(model, request)
        if self.mode != "live" and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.hits += 1
            return Completion(
                text=data["text"],
                usage=Usage.model_validate(data["usage"]),
                latency_ms=float(data["latency_ms"]),
                model=model,
                provider=self.name,
                from_cassette=True,
            )
        if self.mode == "offline":
            self.misses += 1
            raise CassetteMiss(f"no recording for {self.name}/{model} ({path.name}); record it with BENCH_MODE=record")
        result = self.inner.complete(model, system, user, max_tokens=max_tokens, temperature=temperature)
        if self.mode == "record":
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "request": request,
                "text": result.text,
                "usage": result.usage.model_dump(),
                "latency_ms": result.latency_ms,
                "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        return result
