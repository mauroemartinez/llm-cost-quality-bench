"""Architecture A: one agent, one big prompt. Classifies, extracts and
replies in a single call. Validation runs afterwards only to measure; there
is no retry, which is the point of the comparison."""

from __future__ import annotations

from ..meter import Meter
from ..prompts import SINGLE_SYSTEM, user_message
from ..providers import Provider
from ..rules import validate_extraction, validate_reply
from ..schemas import Case, CaseResult, Extraction
from .common import call_stage


def run_single(
    case: Case, provider: Provider, model: str, meter: Meter, *, label: str = "single", temperature: float | None = 0.0
) -> CaseResult:
    def validate(payload: dict) -> tuple[object, list[str]]:
        extraction = Extraction.model_validate({"intent": payload.get("intent"), "quote": payload.get("quote") or {}})
        reply = str(payload.get("reply") or "")
        violations = validate_extraction(extraction, case.message) + validate_reply(reply, extraction, case.ours, case.message)
        return (extraction, reply), violations

    outcome = call_stage(
        meter=meter,
        arch=label,
        stage="single",
        case_id=case.id,
        system=SINGLE_SYSTEM,
        user=user_message(case),
        validate=validate,
        provider=provider,
        model=model,
        guarded=False,
        max_tokens=600,
        temperature=temperature,
    )
    extraction, reply = outcome.value if outcome.value is not None else (None, "")
    return CaseResult(
        arch=label,
        case_id=case.id,
        extraction=extraction,
        reply_text=reply,
        calls=len(outcome.records),
        retries=0,
        fallbacks=0,
        violations=outcome.violations,
        cost_usd=outcome.cost_usd,
        latency_ms=outcome.latency_ms,
        parse_failed=outcome.parse_failed,
    )
