"""Command line: generate, run, score, review, report, calibrate, all."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from .archs import run_modular, run_single
from .meter import BudgetExceeded, Meter, read_ledger
from .providers import CassetteMiss, ProviderError, get_provider, has_key, resolve_mode
from .report import build
from .review import compute, make_sheet, write_sheet
from .schemas import CaseResult
from .score import aggregate, load_results, save_results, score_all
from .synth import generate, load, save

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "fixtures" / "cases.jsonl"
RESULTS = ROOT / "results"
REVIEW = ROOT / "review"

# Default big/small pair per provider. Any model in prices.yaml can be used with --big/--small.
PAIRS = {
    "openai": ("gpt-5.4", "gpt-5.4-mini"),
    "anthropic": ("claude-sonnet-5-5", "claude-haiku-5-5"),
    "gemini": ("gemini-3.1-pro-preview", "gemini-3.5-flash-lite"),
    "deepseek": ("deepseek-v4-pro", "deepseek-flash"),
    "mock": ("mock-big", "mock-small"),
}


def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader: KEY=value lines, no quotes, never printed."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def cmd_generate(args: argparse.Namespace) -> None:
    cases = generate(args.n, args.seed)
    save(cases, args.out)
    print(f"{len(cases)} synthetic cases written to {args.out}")


def _archs(args: argparse.Namespace) -> list[dict]:
    archs = []
    for pair in args.pair:
        big, small = PAIRS[pair]
        big, small = args.big or big, args.small or small
        if args.arch in ("single", "both"):
            archs.append(
                {
                    "kind": "single",
                    "provider": pair,
                    "big": big,
                    "small": small,
                    "label": f"single ({big})",
                    "description": f"one agent, one big prompt, {big}, no retries",
                }
            )
        if args.arch in ("modular", "both"):
            archs.append(
                {
                    "kind": "modular",
                    "provider": pair,
                    "big": big,
                    "small": small,
                    "label": f"modular ({small} + guards, {big} fallback)",
                    "description": f"rules first, then classify, extract and reply as separate stages on {small}; "
                    f"validation guards retry once with a correction and fall back to {big}",
                }
            )
    return archs


def cmd_run(args: argparse.Namespace) -> Path:
    cases = load(args.cases)[: args.n] if args.n else load(args.cases)
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + "-".join(args.pair)
    run_dir = RESULTS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    archs = _archs(args)
    meter = Meter(run_dir / "ledger.jsonl", run_id, max_usd=args.max_usd)
    results: list[CaseResult] = []
    providers = {}
    for arch in archs:
        name = arch["provider"]
        if name not in providers:
            providers[name] = get_provider(name, args.mode)
            keyless = "" if has_key(name) or name == "mock" else " (no API key: cassettes only)"
            print(f"{name}: mode {resolve_mode(name, args.mode)}{keyless}")
    (run_dir / "config.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "seed": args.seed,
                "n": len(cases),
                "archs": archs,
                "monthly_volume": args.monthly_volume,
                "mode": args.mode,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    try:
        for arch in archs:
            provider = providers[arch["provider"]]
            for index, case in enumerate(cases, start=1):
                try:
                    if arch["kind"] == "single":
                        result = run_single(case, provider, arch["big"], meter, label=arch["label"], temperature=args.temperature)
                    else:
                        result = run_modular(
                            case, provider, arch["small"], provider, arch["big"], meter, label=arch["label"], temperature=args.temperature
                        )
                except CassetteMiss as miss:
                    print(f"stopped: {miss}", file=sys.stderr)
                    raise SystemExit(2) from miss
                results.append(result)
                if index % 25 == 0 or index == len(cases):
                    spent = f"USD {meter.total_usd:.4f} so far (live USD {meter.real_usd:.4f})"
                    print(f"  {arch['label']}: {index}/{len(cases)} messages, {spent}")
    except BudgetExceeded as stop:
        print(f"stopped: {stop}", file=sys.stderr)
    finally:
        save_results(results, run_dir / "results.jsonl")
    print(f"run {run_id}: {len(results)} results, total USD {meter.total_usd:.4f} (live USD {meter.real_usd:.4f}) in {run_dir}")
    return run_dir


def cmd_score(args: argparse.Namespace) -> None:
    cases = load(args.cases)
    results = load_results(args.run / "results.jsonl")
    ledger = read_ledger(args.run / "ledger.jsonl")
    summary = aggregate(score_all(results, cases), ledger)
    with __import__("polars").Config(tbl_cols=20, tbl_width_chars=200, fmt_str_lengths=60, set_ascii_tables=True):
        print(summary)


def cmd_review(args: argparse.Namespace) -> None:
    if args.compute:
        for row in compute(args.compute):
            print(
                f"{row['arch']}: {row['ok']}/{row['reviewed']} ok ({100 * row['accuracy']:.1f} %, "
                f"95 % Wilson {100 * row['ci_low']:.1f} to {100 * row['ci_high']:.1f} %), "
                f"{row['doubtful']} doubtful counted as error, "
                f"{row['auto_vs_reviewer_disagreements']} disagreements with the automatic check"
            )
        return
    cases = load(args.cases)
    results = load_results(args.run / "results.jsonl")
    rows = make_sheet(cases, results, score_all(results, cases), args.per_stratum, args.seed)
    out = args.out or REVIEW / f"{args.run.name}.csv"
    write_sheet(rows, out)
    print(f"{len(rows)} rows to review in {out}; fill reviewer_verdict with ok, error or doubtful, then: bench review --compute {out}")


def cmd_report(args: argparse.Namespace) -> None:
    path = build(args.run, args.cases, args.review_sheet)
    print(f"report written to {path}")


def cmd_calibrate(args: argparse.Namespace) -> None:
    """The noise floor: the same model, twice, on the same messages. Agreement
    below 100 % is what any comparison has to be read against."""
    cases = load(args.cases)[: args.n]
    provider = get_provider(args.provider, args.mode)
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-calibrate"
    run_dir = RESULTS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meter = Meter(run_dir / "ledger.jsonl", run_id, max_usd=args.max_usd)
    first = [run_single(c, provider, args.model, meter, label="pass 1", temperature=args.temperature) for c in cases]
    second = [run_single(c, provider, args.model, meter, label="pass 2", temperature=args.temperature) for c in cases]
    same = 0
    for a, b in zip(first, second, strict=True):
        same += int(a.extraction == b.extraction and (a.reply_text.strip() == "") == (b.reply_text.strip() == ""))
    share = 100 * same / len(cases)
    print(f"{args.model} against itself on {len(cases)} messages: {same}/{len(cases)} same answers ({share:.1f} %)")
    print(f"Spent USD {meter.total_usd:.4f}.")
    print("Any difference between two configurations smaller than this gap is noise, not signal.")


def cmd_all(args: argparse.Namespace) -> None:
    if not args.cases.exists():
        save(generate(args.n_generate, args.seed), args.cases)
        print(f"generated {args.n_generate} cases in {args.cases}")
    run_dir = cmd_run(args)
    args.run = run_dir
    cmd_score(args)
    cases, results = load(args.cases), load_results(run_dir / "results.jsonl")
    rows = make_sheet(cases, results, score_all(results, cases), args.per_stratum, args.seed)
    sheet = REVIEW / f"{run_dir.name}.csv"
    write_sheet(rows, sheet)
    args.review_sheet = None
    cmd_report(args)
    print(f"review sheet: {sheet}")


def main(argv: list[str] | None = None) -> None:
    load_env()
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="bench", description="Cost and quality of LLMs on a B2B procurement task, with synthetic data.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--cases", type=Path, default=CASES)
        p.add_argument("--seed", type=int, default=7)

    def add_run(p: argparse.ArgumentParser) -> None:
        add_common(p)
        p.add_argument("--pair", action="append", choices=sorted(PAIRS), default=None, help="provider pair(s); repeatable")
        p.add_argument("--big", help="override the big model")
        p.add_argument("--small", help="override the small model")
        p.add_argument("--arch", choices=("single", "modular", "both"), default="both")
        p.add_argument("--n", type=int, default=None, help="use only the first N cases (default: all; with live APIs, 50)")
        p.add_argument(
            "--mode", choices=("offline", "record", "live"), default=None, help="default: BENCH_MODE, else record when a key exists"
        )
        p.add_argument("--max-usd", type=float, default=float(os.environ.get("BENCH_MAX_USD", 2)), help="hard stop for live spend")
        p.add_argument("--temperature", type=float, default=0.0)
        p.add_argument("--monthly-volume", type=int, default=5000, help="messages per month for the projection")
        p.add_argument("--run-id")
        p.add_argument("--per-stratum", type=int, default=5, help="rows per (architecture, intent) in the review sheet")

    g = sub.add_parser("generate", help="write the synthetic cases")
    add_common(g)
    g.add_argument("--n", type=int, default=300)
    g.add_argument("--out", type=Path, default=CASES)
    g.set_defaults(func=cmd_generate)

    r = sub.add_parser("run", help="run the architectures on the cases")
    add_run(r)
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("score", help="automatic scoring of a run")
    add_common(s)
    s.add_argument("--run", type=Path, required=True)
    s.set_defaults(func=cmd_score)

    v = sub.add_parser("review", help="manual review sheet, or its accuracy with Wilson interval")
    add_common(v)
    v.add_argument("--run", type=Path)
    v.add_argument("--per-stratum", type=int, default=5)
    v.add_argument("--out", type=Path)
    v.add_argument("--compute", type=Path, help="a filled sheet to score")
    v.set_defaults(func=cmd_review)

    p = sub.add_parser("report", help="Markdown report with charts")
    add_common(p)
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--review-sheet", type=Path, default=None)
    p.set_defaults(func=cmd_report)

    c = sub.add_parser("calibrate", help="the same model twice: the noise floor")
    add_common(c)
    c.add_argument("--provider", choices=sorted(PAIRS), default="mock")
    c.add_argument("--model", default=None)
    c.add_argument("--n", type=int, default=30)
    c.add_argument("--mode", choices=("offline", "record", "live"), default=None)
    c.add_argument("--max-usd", type=float, default=float(os.environ.get("BENCH_MAX_USD", 2)))
    c.add_argument("--temperature", type=float, default=None, help="default: the provider's own default, to expose its variance")
    c.add_argument("--run-id")
    c.set_defaults(func=cmd_calibrate)

    a = sub.add_parser("all", help="generate (if needed), run, score, review sheet and report")
    add_run(a)
    a.add_argument("--n-generate", type=int, default=300)
    a.set_defaults(func=cmd_all)

    args = parser.parse_args(argv)
    if getattr(args, "pair", None) is None and args.command in ("run", "all"):
        args.pair = ["mock"]
    if args.command == "calibrate" and args.model is None:
        args.model = PAIRS[args.provider][0]
    if args.command in ("run", "all") and args.n is None and any(has_key(p) for p in args.pair):
        args.n = 50
    try:
        args.func(args)
    except ProviderError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
