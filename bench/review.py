"""Manual review: a stratified random sample, a CSV to fill in by hand, and
the accuracy with its Wilson interval once it is filled.

Automatic checks are cheap and tireless, but they are also wrong in ways a
person spots at once. A sample reviewed by hand is what turns "the judge says
87%" into a number you can defend."""

from __future__ import annotations

import csv
import random
from collections import defaultdict
from pathlib import Path

from .schemas import Case, CaseResult
from .stats import wilson

COLUMNS = [
    "case_id",
    "arch",
    "intent_truth",
    "supplier_message",
    "our_facts",
    "truth_quote",
    "model_intent",
    "model_quote",
    "model_reply",
    "auto_correct",
    "reviewer_verdict",
    "reviewer_note",
]
VERDICTS = ("ok", "error", "doubtful")


def make_sheet(cases: list[Case], results: list[CaseResult], scores: list[dict], per_stratum: int = 5, seed: int = 7) -> list[dict]:
    """Stratified by architecture and by the true intent, so rare intents
    (declines, questions) are reviewed as carefully as the common quote."""
    rng = random.Random(seed)
    by_case = {case.id: case for case in cases}
    auto = {(s["arch"], s["case_id"]): s["correct"] for s in scores}
    strata: dict[tuple[str, str], list[CaseResult]] = defaultdict(list)
    for result in results:
        strata[(result.arch, by_case[result.case_id].truth.intent.value)].append(result)
    rows = []
    for (arch, intent), members in sorted(strata.items()):
        for result in rng.sample(members, min(per_stratum, len(members))):
            case = by_case[result.case_id]
            rows.append(
                {
                    "case_id": case.id,
                    "arch": arch,
                    "intent_truth": intent,
                    "supplier_message": case.message,
                    "our_facts": case.ours.model_dump_json(),
                    "truth_quote": case.truth.quote.model_dump_json(exclude_none=True),
                    "model_intent": result.extraction.intent.value if result.extraction else "",
                    "model_quote": result.extraction.quote.model_dump_json(exclude_none=True) if result.extraction else "",
                    "model_reply": result.reply_text,
                    "auto_correct": "yes" if auto.get((arch, case.id)) else "no",
                    "reviewer_verdict": "",
                    "reviewer_note": "",
                }
            )
    return rows


def write_sheet(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def compute(path: Path) -> list[dict]:
    """Accuracy per architecture from a filled sheet. A doubtful verdict counts
    as an error, on purpose. Also reports how often the automatic check and the
    reviewer disagreed: that gap is the reason the review exists."""
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row.get("reviewer_verdict", "").strip()]
    out = []
    for arch in sorted({row["arch"] for row in rows}):
        mine = [row for row in rows if row["arch"] == arch]
        for row in mine:
            verdict = row["reviewer_verdict"].strip().lower()
            if verdict not in VERDICTS:
                raise ValueError(f"{row['case_id']}: reviewer_verdict must be one of {VERDICTS}, got {verdict!r}")
        ok = sum(1 for row in mine if row["reviewer_verdict"].strip().lower() == "ok")
        doubtful = sum(1 for row in mine if row["reviewer_verdict"].strip().lower() == "doubtful")
        n = len(mine)
        estimate, low, high = wilson(ok, n)
        disagreements = sum(1 for row in mine if (row["auto_correct"] == "yes") != (row["reviewer_verdict"].strip().lower() == "ok"))
        out.append(
            {
                "arch": arch,
                "reviewed": n,
                "ok": ok,
                "doubtful": doubtful,
                "accuracy": estimate,
                "ci_low": low,
                "ci_high": high,
                "auto_vs_reviewer_disagreements": disagreements,
            }
        )
    return out
