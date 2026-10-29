"""Prices per model, read from prices.yaml, and the cost of one call.

Costs are computed and stored when the call is recorded, with the price of
that day: if the price list changes later, old rows keep the price that was
actually paid and month-to-month comparisons stay honest."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

PRICES_PATH = Path(__file__).resolve().parent.parent / "prices.yaml"


@dataclass(frozen=True)
class Price:
    provider: str
    model: str
    input: float  # USD per 1M input tokens not served from cache
    cached_input: float  # USD per 1M input tokens served from the prompt cache
    output: float  # USD per 1M output tokens
    source: str
    read_on: str


@lru_cache(maxsize=1)
def load_prices(path: Path = PRICES_PATH) -> dict[str, Price]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    prices: dict[str, Price] = {}
    for provider, block in data["providers"].items():
        for model, p in block["models"].items():
            prices[model] = Price(
                provider=provider,
                model=model,
                input=float(p["input"]),
                cached_input=float(p.get("cached_input", p["input"])),
                output=float(p["output"]),
                source=block["source"],
                read_on=str(block.get("read_on", data.get("as_of", ""))),
            )
    return prices


def price_for(model: str) -> Price:
    try:
        return load_prices()[model]
    except KeyError as error:
        raise KeyError(f"{model!r} is not in prices.yaml; add it with its date and source") from error


def provider_of(model: str) -> str:
    return price_for(model).provider


def cost_usd(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    """Cached tokens are a subset of input tokens and are billed at their own price.
    Forgetting that was our most expensive accounting lesson: a run looked far
    costlier than the invoice because every input token was priced as uncached."""
    price = price_for(model)
    cached = min(max(cached_tokens, 0), max(input_tokens, 0))
    uncached = max(input_tokens, 0) - cached
    scale = 0.5 if price.provider == "deepseek" and os.environ.get("BENCH_DEEPSEEK_OFFPEAK") == "1" else 1.0
    total = (uncached * price.input + cached * price.cached_input + max(output_tokens, 0) * price.output) / 1e6
    return round(total * scale, 8)


def estimate_tokens(text: str) -> int:
    """Rough token count when the API gave no usage (about 4 characters per token).
    Rows priced this way are marked `estimated`, never passed off as exact."""
    return int(math.ceil(len(text) / 4))
