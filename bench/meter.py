"""The ledger: one JSONL line per model call, plus a hard budget.

Every call is written as it happens, with its tokens, its cost at today's
price, whether the usage was exact or estimated, whether validation passed
and how many retries it took. Nothing is aggregated here; reports read the
ledger and group it however the question needs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .pricing import cost_usd, estimate_tokens
from .schemas import CallRecord, Completion


class BudgetExceeded(RuntimeError):
    pass


class Meter:
    def __init__(self, path: Path, run_id: str, max_usd: float | None = None):
        self.path = path
        self.run_id = run_id
        self.max_usd = max_usd
        self.total_usd = 0.0
        self.real_usd = 0.0  # spent on live calls only (cassette replays cost nothing today)
        self.records: list[CallRecord] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        arch: str,
        stage: str,
        case_id: str,
        attempt: int,
        completion: Completion,
        prompt_text: str,
        validation_passed: bool,
        violations: list[str],
        fallback: bool = False,
    ) -> CallRecord:
        usage = completion.usage
        if usage.exact:
            input_tokens, cached, output_tokens, method = (
                usage.input_tokens,
                usage.cached_tokens,
                usage.output_tokens,
                "exact",
            )
        else:
            input_tokens, cached, output_tokens, method = (
                estimate_tokens(prompt_text),
                0,
                estimate_tokens(completion.text),
                "estimated",
            )
        cost = cost_usd(completion.model, input_tokens, cached, output_tokens)
        record = CallRecord(
            run_id=self.run_id,
            ts=datetime.now(UTC).isoformat(timespec="seconds"),
            arch=arch,
            stage=stage,
            provider=completion.provider,
            model=completion.model,
            case_id=case_id,
            attempt=attempt,
            input_tokens=input_tokens,
            cached_tokens=cached,
            output_tokens=output_tokens,
            cost_usd=cost,
            cost_method=method,
            latency_ms=completion.latency_ms,
            validation_passed=validation_passed,
            violations=violations,
            fallback=fallback,
            from_cassette=completion.from_cassette,
        )
        self.records.append(record)
        self.total_usd += cost
        if not completion.from_cassette:
            self.real_usd += cost
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
        if self.max_usd is not None and self.real_usd > self.max_usd:
            raise BudgetExceeded(
                f"spent USD {self.real_usd:.4f} on live calls, over the cap of USD {self.max_usd:.2f}; raise --max-usd or run offline"
            )
        return record


def read_ledger(path: Path) -> list[CallRecord]:
    records = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(CallRecord.model_validate(json.loads(line)))
    return records
