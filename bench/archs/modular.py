"""Architecture B: modular agents with validation guards.

Stage 0, rules only: a plain acknowledgement never reaches a model.
Stage 1, classify (small model, tiny prompt).
Stage 2, extract the quote (small model), only when there is one to read.
Stage 3, reply (small model) with only the module for that situation.
Every model stage is guarded: one retry with the broken rules spelled out,
then the big model as fallback."""

from __future__ import annotations

from ..meter import Meter
from ..prompts import CLASSIFY_SYSTEM, EXTRACT_SYSTEM, reply_system, user_message
from ..providers import Provider
from ..rules import looks_like_acknowledgement, validate_extraction, validate_reply
from ..schemas import Case, CaseResult, Extraction, Intent, Quote
from .common import StageOutcome, call_stage


def run_modular(
    case: Case,
    small: Provider,
    small_model: str,
    big: Provider,
    big_model: str,
    meter: Meter,
    *,
    label: str = "modular",
    temperature: float | None = 0.0,
) -> CaseResult:
    outcomes: list[StageOutcome] = []
    violations: list[str] = []

    def stage(name: str, system: str, user: str, validate, max_tokens: int = 400) -> StageOutcome:
        outcome = call_stage(
            meter=meter,
            arch=label,
            stage=name,
            case_id=case.id,
            system=system,
            user=user,
            validate=validate,
            provider=small,
            model=small_model,
            guarded=True,
            fallback_provider=big,
            fallback_model=big_model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        outcomes.append(outcome)
        violations.extend(outcome.violations)
        return outcome

    if looks_like_acknowledgement(case.message):
        return CaseResult(
            arch=label,
            case_id=case.id,
            extraction=Extraction(intent=Intent.ACKNOWLEDGEMENT),
            reply_text="",
            calls=0,
            retries=0,
            fallbacks=0,
            violations=[],
            cost_usd=0.0,
            latency_ms=0.0,
        )

    classified = stage(
        "classify",
        CLASSIFY_SYSTEM,
        user_message(case),
        lambda payload: (Intent(payload.get("intent")), []),
        max_tokens=60,
    )
    intent = classified.value if isinstance(classified.value, Intent) else Intent.QUOTE

    quote = Quote()
    if intent in (Intent.QUOTE, Intent.COUNTER_REPLY):

        def validate_quote(payload: dict) -> tuple[object, list[str]]:
            extraction = Extraction(intent=intent, quote=Quote.model_validate(payload.get("quote") or {}))
            return extraction.quote, validate_extraction(extraction, case.message)

        extracted = stage("extract", EXTRACT_SYSTEM, user_message(case), validate_quote, max_tokens=300)
        if isinstance(extracted.value, Quote):
            quote = extracted.value
    extraction = Extraction(intent=intent, quote=quote)

    reply = ""
    if intent is not Intent.ACKNOWLEDGEMENT:

        def validate_reply_payload(payload: dict) -> tuple[object, list[str]]:
            text = str(payload.get("reply") or "")
            return text, validate_reply(text, extraction, case.ours, case.message)

        replied = stage("reply", reply_system(intent), user_message(case, extraction), validate_reply_payload, max_tokens=400)
        reply = replied.value if isinstance(replied.value, str) else ""

    return CaseResult(
        arch=label,
        case_id=case.id,
        extraction=extraction,
        reply_text=reply,
        calls=sum(len(o.records) for o in outcomes),
        retries=sum(o.retries for o in outcomes),
        fallbacks=sum(1 for o in outcomes if o.fallback),
        violations=violations,
        cost_usd=sum(o.cost_usd for o in outcomes),
        latency_ms=sum(o.latency_ms for o in outcomes),
        parse_failed=any(o.parse_failed for o in outcomes),
    )
