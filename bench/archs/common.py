"""One stage = one prompt, parsed and validated, with the guard policy:

- unguarded (the single big prompt): one call, whatever comes out is the answer;
- guarded (the modular pipeline): if validation fails, repeat the call ONCE
  with the broken rules appended as a correction; if it fails again, the big
  model answers that stage. Every attempt goes to the ledger."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import ValidationError

from ..meter import Meter
from ..prompts import correction
from ..providers import Provider, parse_json
from ..schemas import CallRecord

Validator = Callable[[dict], tuple[object, list[str]]]


@dataclass
class StageOutcome:
    value: object | None
    violations: list[str]
    records: list[CallRecord] = field(default_factory=list)
    fallback: bool = False
    parse_failed: bool = False

    @property
    def cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.records)

    @property
    def latency_ms(self) -> float:
        return sum(r.latency_ms for r in self.records)

    @property
    def retries(self) -> int:
        return max(0, len(self.records) - 1)


def _parse_and_validate(text: str, validate: Validator) -> tuple[object | None, list[str], bool]:
    try:
        payload = parse_json(text)
    except (ValueError, TypeError):
        return None, ["unparsable_json"], True
    try:
        return (*validate(payload), False)
    except ValidationError as error:
        first = error.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ())) or "root"
        return None, [f"schema_{location}"], False


def call_stage(
    *,
    meter: Meter,
    arch: str,
    stage: str,
    case_id: str,
    system: str,
    user: str,
    validate: Validator,
    provider: Provider,
    model: str,
    guarded: bool,
    fallback_provider: Provider | None = None,
    fallback_model: str | None = None,
    max_tokens: int = 400,
    temperature: float | None = 0.0,
) -> StageOutcome:
    outcome = StageOutcome(value=None, violations=[])
    plan: list[tuple[Provider, str, str, bool]] = [(provider, model, user, False)]
    for attempt, (prov, mdl, prompt, is_fallback) in enumerate(plan, start=1):
        completion = prov.complete(mdl, system, prompt, max_tokens=max_tokens, temperature=temperature)
        value, violations, parse_failed = _parse_and_validate(completion.text, validate)
        record = meter.record(
            arch=arch,
            stage=stage,
            case_id=case_id,
            attempt=attempt,
            completion=completion,
            prompt_text=system + prompt,
            validation_passed=not violations,
            violations=violations,
            fallback=is_fallback,
        )
        outcome.records.append(record)
        outcome.value, outcome.violations, outcome.parse_failed = value, violations, parse_failed
        outcome.fallback = outcome.fallback or is_fallback
        if not violations or not guarded:
            break
        if attempt == 1:
            plan.append((provider, model, user + "\n\n" + correction(violations), False))
        elif attempt == 2 and fallback_provider is not None and fallback_model:
            plan.append((fallback_provider, fallback_model, user, True))
    return outcome
