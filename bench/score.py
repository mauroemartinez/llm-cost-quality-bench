"""Automatic scoring against the known answer, and the aggregates per
architecture. The rules in rules.py never see the truth; this module does."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import polars as pl

from .rules import LEAKS, numbers_in
from .schemas import CallRecord, Case, CaseResult
from .stats import percentile

FIELDS = ("price", "currency", "moq", "moq_unit", "lead_time_days", "incoterm", "payment_terms")
ASK_KEYWORDS = {
    "moq": ("moq", "minimum"),
    "lead_time": ("lead time", "delivery", "production time"),
    "payment_terms": ("payment",),
    "proforma invoice": ("proforma", " pi"),
}
ACCEPTANCE = ("we accept", "deal", "agreed", "go ahead", "confirm the price", "sounds good")


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9/%]+", text.lower()))


def field_ok(name: str, got, truth) -> bool:
    if got is None or truth is None:
        return got is None and truth is None
    if name == "payment_terms":
        a, b = _tokens(str(got)), _tokens(str(truth))
        return len(a & b) / len(a | b) >= 0.6 if a | b else True
    if isinstance(truth, float | int):
        try:
            return abs(float(got) - float(truth)) < 1e-6
        except (TypeError, ValueError):
            return False
    return " ".join(str(got).lower().split()) == " ".join(str(truth).lower().split())


def reply_ok(reply: str, case: Case) -> bool:
    """Does the reply do what the generated requirements say, judged by code."""
    req = case.reply
    text = reply.strip()
    low = text.lower()
    if req.no_reply:
        return text == ""
    if not text:
        return False
    if any(leak in low for leak in LEAKS) or ("[" in text and "]" in text):
        return False
    price = case.truth.quote.price
    if req.must_counter and price is not None:
        counters = [n for n in numbers_in(text) if 0.6 * price <= n < price]
        if not counters or any(phrase in low for phrase in ACCEPTANCE):
            return False
    for field in req.must_ask:
        if not any(keyword in low for keyword in ASK_KEYWORDS.get(field, (field,))):
            return False
    if req.must_answer and req.must_answer.lower() not in low:
        return False
    if req.must_close_politely and ("thank" not in low or any(k in low for k in ("your price", "best price", "quote", "quotation"))):
        return False
    return True


def score_case(result: CaseResult, case: Case) -> dict:
    truth = case.truth
    got = result.extraction
    intent_ok = got is not None and got.intent == truth.intent
    fields = {name: (got is not None and field_ok(name, getattr(got.quote, name), getattr(truth.quote, name))) for name in FIELDS}
    extraction_ok = all(fields.values())
    replied_ok = reply_ok(result.reply_text, case)
    return {
        "case_id": case.id,
        "arch": result.arch,
        "intent_truth": truth.intent.value,
        "intent_ok": intent_ok,
        "extraction_ok": extraction_ok,
        "fields_ok": sum(fields.values()),
        "reply_ok": replied_ok,
        "correct": intent_ok and extraction_ok and replied_ok,
        "parse_failed": result.parse_failed,
        "violations": len(result.violations),
        "calls": result.calls,
        "retries": result.retries,
        "fallbacks": result.fallbacks,
        "cost_usd": result.cost_usd,
        "latency_ms": result.latency_ms,
    }


def score_all(results: list[CaseResult], cases: list[Case]) -> list[dict]:
    by_id = {case.id: case for case in cases}
    return [score_case(result, by_id[result.case_id]) for result in results]


def aggregate(scores: list[dict], ledger: list[CallRecord]) -> pl.DataFrame:
    frame = pl.DataFrame(scores)
    rows = []
    for arch, group in frame.group_by("arch", maintain_order=True):
        label = arch[0]
        calls = [r for r in ledger if r.arch == label]
        first_attempt_failures = sum(1 for r in calls if r.attempt == 1 and not r.validation_passed)
        input_tokens = sum(r.input_tokens for r in calls)
        cached = sum(r.cached_tokens for r in calls)
        n = group.height
        latencies = group["latency_ms"].to_list()
        rows.append(
            {
                "arch": label,
                "cases": n,
                "intent_accuracy": group["intent_ok"].mean(),
                "extraction_accuracy": group["extraction_ok"].mean(),
                "field_accuracy": group["fields_ok"].sum() / (n * len(FIELDS)),
                "reply_ok": group["reply_ok"].mean(),
                "correct": group["correct"].mean(),
                "parse_failed": group["parse_failed"].sum(),
                "cost_total_usd": group["cost_usd"].sum(),
                "cost_per_1k_usd": group["cost_usd"].sum() / n * 1000,
                "cost_median_usd": group["cost_usd"].median(),
                "calls_per_case": group["calls"].mean(),
                "first_attempt_failures": first_attempt_failures,
                "cases_with_retry": int((group["retries"] > 0).sum()),
                "cases_with_fallback": int((group["fallbacks"] > 0).sum()),
                "latency_p50_ms": percentile(latencies, 50),
                "latency_p95_ms": percentile(latencies, 95),
                "cached_share": cached / input_tokens if input_tokens else 0.0,
                "estimated_share": sum(1 for r in calls if r.cost_method == "estimated") / len(calls) if calls else 0.0,
                "from_cassette": all(r.from_cassette for r in calls) if calls else False,
            }
        )
    return pl.DataFrame(rows)


def violations_by_type(ledger: list[CallRecord]) -> dict[str, Counter]:
    counts: dict[str, Counter] = {}
    for record in ledger:
        counts.setdefault(record.arch, Counter()).update(record.violations)
    return counts


def by_intent(scores: list[dict]) -> pl.DataFrame:
    frame = pl.DataFrame(scores)
    return (
        frame.group_by(["arch", "intent_truth"], maintain_order=True)
        .agg(pl.len().alias("cases"), pl.col("correct").mean().alias("correct"), pl.col("cost_usd").mean().alias("cost_mean_usd"))
        .sort(["arch", "intent_truth"])
    )


def save_results(results: list[CaseResult], path: Path) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(result.model_dump_json() + "\n")


def load_results(path: Path) -> list[CaseResult]:
    with path.open(encoding="utf-8") as handle:
        return [CaseResult.model_validate(json.loads(line)) for line in handle if line.strip()]
