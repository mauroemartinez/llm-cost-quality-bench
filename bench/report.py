"""The Markdown report with its charts: cost per 1,000 messages, accuracy,
latency p50/p95, retries and fallbacks, violations by type, outliers, and
the cost-against-quality chart with its Pareto frontier."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import polars as pl  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

from .meter import read_ledger  # noqa: E402
from .pricing import load_prices  # noqa: E402
from .review import compute as compute_review  # noqa: E402
from .score import aggregate, by_intent, load_results, score_all, violations_by_type  # noqa: E402
from .stats import median, outliers  # noqa: E402
from .synth import load as load_cases  # noqa: E402

NAVY, CYAN, GREY, SAND = "#051e45", "#1fb5e3", "#8a94a6", "#d9d2c5"
PALETTE = [NAVY, CYAN, "#4f6d8f", "#9bd7ef", GREY, SAND]
# One colour per provider on the cost-against-quality chart.
PROVIDER_COLOURS = {"openai": CYAN, "anthropic": "#e8742c", "deepseek": "#1a1a1a", "gemini": "#2f9e6b", "mock": GREY}
PROVIDER_NAMES = {"openai": "OpenAI", "anthropic": "Anthropic", "deepseek": "DeepSeek", "gemini": "Gemini", "mock": "simulated"}


def _pct(value: float) -> str:
    return f"{100 * value:.1f} %"


def _usd(value: float, digits: int = 2) -> str:
    return f"USD {value:,.{digits}f}"


def _style(ax) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="y", color="#e6e6e6", linewidth=0.8)
    ax.set_axisbelow(True)


def chart_bars(frame: pl.DataFrame, column: str, title: str, ylabel: str, path: Path, percent: bool = False) -> None:
    labels, values = frame["arch"].to_list(), frame[column].to_list()
    fig, ax = plt.subplots(figsize=(7, 3.6), dpi=150)
    bars = ax.bar(labels, [100 * v if percent else v for v in values], color=PALETTE[: len(labels)])
    for bar, value in zip(bars, values, strict=True):
        ax.annotate(
            _pct(value) if percent else f"{value:,.2f}",
            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            ha="center",
            va="bottom",
            fontsize=9,
            xytext=(0, 3),
            textcoords="offset points",
        )
    ax.set_title(title, loc="left", fontsize=11, color=NAVY)
    ax.set_ylabel(ylabel)
    if percent:
        ax.set_ylim(0, 105)
    _style(ax)
    plt.xticks(fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def chart_latency(frame: pl.DataFrame, path: Path) -> None:
    labels = frame["arch"].to_list()
    p50, p95 = frame["latency_p50_ms"].to_list(), frame["latency_p95_ms"].to_list()
    fig, ax = plt.subplots(figsize=(7, 3.6), dpi=150)
    x = range(len(labels))
    ax.bar([i - 0.2 for i in x], p50, width=0.4, label="p50", color=NAVY)
    ax.bar([i + 0.2 for i in x], p95, width=0.4, label="p95", color=CYAN)
    ax.set_xticks(list(x), labels, fontsize=8)
    ax.set_ylabel("ms per message")
    ax.set_title("Latency per message, p50 and p95", loc="left", fontsize=11, color=NAVY)
    ax.legend(frameon=False)
    _style(ax)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _short(label: str) -> str:
    """The architecture's name as it fits on a chart: 'single (m)' reads
    'm, one prompt'; 'modular (s + guards, b fallback)' reads 's + guards'."""
    if label.startswith("single (") and label.endswith(")"):
        return f"{label[8:-1]}, one prompt"
    if label.startswith("modular (") and " fallback)" in label:
        return label[9 : label.rfind(", ")]
    return label


def pareto_frontier(points: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Points no other point beats on both cost (lower) and quality (higher)."""
    frontier = []
    for cost, quality, label in points:
        dominated = any(c <= cost and q >= quality and (c < cost or q > quality) for c, q, _ in points)
        if not dominated:
            frontier.append((cost, quality, label))
    return sorted(frontier)


def _big_model(label: str) -> str | None:
    """The big model behind an architecture's name, or None."""
    if label.startswith("single (") and label.endswith(")"):
        return label[8:-1]
    if label.startswith("modular (") and " fallback)" in label:
        return label[label.rfind(", ") + 2 : label.rfind(" fallback)")]
    return None


def chart_pareto(frame: pl.DataFrame, path: Path) -> list[str]:
    """One colour per provider; a hollow point is the big model on one
    prompt, a filled one the modular design with guards. Under each
    modular point, its distance to the one-prompt design on the same big
    model. Lower left is better; the frontier is the grey line."""
    points = list(zip(frame["cost_per_1k_usd"].to_list(), frame["correct"].to_list(), frame["arch"].to_list(), strict=True))
    frontier = pareto_frontier(points)
    prices = load_prices()
    by_label = {label: (cost, quality) for cost, quality, label in points}

    def provider_of(label: str) -> str:
        big = _big_model(label)
        return prices[big].provider if big in prices else "mock"

    def colour_of(label: str) -> str:
        return PROVIDER_COLOURS.get(provider_of(label), GREY)

    fig, ax = plt.subplots(figsize=(7, 4.4), dpi=150)
    widest = max(cost for cost, _, _ in points) or 1
    if len(frontier) > 1:
        ax.plot([c for c, _, _ in frontier], [100 * q for _, q, _ in frontier], color="#c9cfd8", linewidth=1.1, zorder=1)
    # Names by the points, pulled inwards on the right half. Along the top
    # band, where the good designs crowd, names alternate above and below
    # from the left so neighbours never run into each other.
    top = [label for cost, quality, label in sorted(points) if quality >= 0.9]
    for cost, quality, label in points:
        modular = label.startswith("modular (")
        colour = colour_of(label)
        if modular:
            ax.scatter(cost, 100 * quality, s=90, color=colour, zorder=3)
        else:
            ax.scatter(cost, 100 * quality, s=90, facecolors="white", edgecolors=colour, linewidths=1.8, zorder=3)
        text = _short(label)
        big = _big_model(label)
        base = by_label.get(f"single ({big})") if modular and big else None
        if base and base[0] > 0:
            text += f"\n{100 * (cost / base[0] - 1):+.1f} % cost, {100 * (quality - base[1]):+.1f} points"
        right = cost > 0.6 * widest
        above = label in top and top.index(label) % 2 == 0
        ax.annotate(
            text,
            (cost, 100 * quality),
            fontsize=8,
            color=NAVY,
            ha="right" if right else "left",
            va="bottom" if above else "top",
            xytext=(-6 if right else 6, 8 if above else -8),
            textcoords="offset points",
            linespacing=1.3,
        )
    providers = sorted(
        {provider_of(label) for _, _, label in points}, key=lambda p: list(PROVIDER_COLOURS).index(p) if p in PROVIDER_COLOURS else 99
    )
    handles = [
        Line2D([], [], marker="o", linestyle="none", markersize=7, color=PROVIDER_COLOURS.get(p, GREY), label=PROVIDER_NAMES.get(p, p))
        for p in providers
    ]
    handles += [
        Line2D(
            [],
            [],
            marker="o",
            linestyle="none",
            markersize=7,
            markerfacecolor="white",
            markeredgecolor=GREY,
            markeredgewidth=1.6,
            label="one prompt",
        ),
        Line2D([], [], marker="o", linestyle="none", markersize=7, color=GREY, label="modular with guards"),
    ]
    ax.legend(handles=handles, frameon=False, fontsize=8, loc="lower right", ncol=2)
    ax.set_xlabel("cost per 1,000 messages (USD)")
    ax.set_ylabel("fully correct messages (%)")
    ax.set_title("Cost against quality: up and to the left is better", loc="left", fontsize=11, color=NAVY)
    ax.set_ylim(0, 112)
    ax.set_xlim(left=0)
    _style(ax)
    ax.grid(axis="x", color="#e6e6e6", linewidth=0.8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return [label for _, _, label in frontier]


def _table(frame: pl.DataFrame, columns: list[tuple[str, str, str]]) -> str:
    """columns: (column, header, kind) with kind in pct|usd|usd4|int|num|str."""
    header = "| " + " | ".join(h for _, h, _ in columns) + " |"
    rule = "|" + "|".join("---" for _ in columns) + "|"
    lines = [header, rule]
    for row in frame.iter_rows(named=True):
        cells = []
        for column, _, kind in columns:
            value = row[column]
            if kind == "pct":
                cells.append(_pct(value))
            elif kind == "usd":
                cells.append(_usd(value))
            elif kind == "usd4":
                cells.append(_usd(value, 4))
            elif kind == "int":
                cells.append(f"{int(value):,}")
            elif kind == "num":
                cells.append(f"{value:,.2f}")
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build(run_dir: Path, cases_path: Path, review_sheet: Path | None = None) -> Path:
    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    cases = load_cases(cases_path)
    results = load_results(run_dir / "results.jsonl")
    ledger = read_ledger(run_dir / "ledger.jsonl")
    scores = score_all(results, cases)
    summary = aggregate(scores, ledger)
    per_intent = by_intent(scores)
    violations = violations_by_type(ledger)
    img = run_dir / "img"
    img.mkdir(exist_ok=True)

    chart_bars(summary, "cost_per_1k_usd", "Cost per 1,000 supplier messages", "USD", img / "cost_per_1k.png")
    chart_bars(summary, "correct", "Fully correct messages (intent, extraction and reply)", "%", img / "accuracy.png", percent=True)
    chart_latency(summary, img / "latency.png")
    frontier = chart_pareto(summary, img / "pareto.png")

    simulated = any(r.provider == "mock" for r in ledger)
    prices = load_prices()
    as_of = sorted({prices[m].read_on for m in {r.model for r in ledger} if m in prices})
    case_ids = {r.case_id for r in results}

    lines = []
    lines.append(f"# Cost and quality report: {config.get('run_id', run_dir.name)}")
    lines.append("")
    if simulated:
        lines.append(
            "> **SIMULATED RESULTS.** At least one architecture ran on the mock provider, which has no model behind it. "
            "These numbers only show that the pipeline works. Do not quote them as model results."
        )
        lines.append("")
    lines.append(
        f"Synthetic data: {len(case_ids)} supplier messages generated with seed {config.get('seed')}; nothing here comes from a real "
        f"supplier, customer or production system. Prices as read on {', '.join(as_of) or 'n/a'} (see prices.yaml for the sources)."
    )
    lines.append("")
    lines.append("## Architectures")
    lines.append("")
    for arch in config.get("archs", []):
        lines.append(f"- **{arch['label']}**: {arch['description']}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(
        _table(
            summary,
            [
                ("arch", "Architecture", "str"),
                ("cases", "Messages", "int"),
                ("correct", "Fully correct", "pct"),
                ("intent_accuracy", "Intent", "pct"),
                ("extraction_accuracy", "Extraction (all 7 fields)", "pct"),
                ("field_accuracy", "Fields", "pct"),
                ("reply_ok", "Reply does its job", "pct"),
                ("cost_per_1k_usd", "Cost per 1,000", "usd"),
                ("cost_median_usd", "Median per message", "usd4"),
                ("latency_p50_ms", "Latency p50 (ms)", "int"),
                ("latency_p95_ms", "Latency p95 (ms)", "int"),
            ],
        )
    )
    lines.append("")
    lines.append("![Cost per 1,000 messages](img/cost_per_1k.png)")
    lines.append("")
    lines.append("![Fully correct messages](img/accuracy.png)")
    lines.append("")
    lines.append("![Latency](img/latency.png)")
    lines.append("")
    lines.append("## Cost against quality")
    lines.append("")
    lines.append("![Pareto](img/pareto.png)")
    lines.append("")
    lines.append("On the frontier (nothing is both cheaper and better): " + ", ".join(f"**{f}**" for f in frontier) + ".")
    lines.append("")
    lines.append("## Guards: validation, retries and fallbacks")
    lines.append("")
    lines.append(
        _table(
            summary,
            [
                ("arch", "Architecture", "str"),
                ("calls_per_case", "Calls per message", "num"),
                ("first_attempt_failures", "First attempts that failed validation", "int"),
                ("cases_with_retry", "Messages retried", "int"),
                ("cases_with_fallback", "Messages sent to the big model", "int"),
                ("parse_failed", "Unparsable answers", "int"),
                ("cached_share", "Input served from cache", "pct"),
                ("estimated_share", "Calls priced by estimate", "pct"),
            ],
        )
    )
    lines.append("")
    lines.append("Violations caught by the rules, by type (every attempt counted):")
    lines.append("")
    for arch, counter in violations.items():
        top = ", ".join(f"{name} ({count})" for name, count in counter.most_common(8)) or "none"
        lines.append(f"- **{arch}**: {top}")
    lines.append("")
    lines.append("## By intent")
    lines.append("")
    lines.append(
        _table(
            per_intent,
            [
                ("arch", "Architecture", "str"),
                ("intent_truth", "Intent", "str"),
                ("cases", "Messages", "int"),
                ("correct", "Fully correct", "pct"),
                ("cost_mean_usd", "Mean cost", "usd4"),
            ],
        )
    )
    lines.append("")
    lines.append("## Outliers")
    lines.append("")
    for arch in summary["arch"].to_list():
        costs = [s["cost_usd"] for s in scores if s["arch"] == arch]
        ids = [s["case_id"] for s in scores if s["arch"] == arch]
        heavy = outliers(costs)
        lines.append(
            f"- **{arch}**: median {_usd(median(costs), 4)} per message; {len(heavy)} messages above twice the median"
            + (": " + ", ".join(f"{ids[i]} ({_usd(costs[i], 4)})" for i in heavy[:6]) if heavy else "")
            + "."
        )
    lines.append("")
    lines.append("## Projection")
    lines.append("")
    volume = int(config.get("monthly_volume", 5000))
    lines.append(f"At {volume:,} supplier messages per month:")
    lines.append("")
    for row in summary.iter_rows(named=True):
        lines.append(f"- **{row['arch']}**: {_usd(row['cost_per_1k_usd'] / 1000 * volume)} per month")
    lines.append("")
    if review_sheet and review_sheet.exists():
        lines.append("## Manual review")
        lines.append("")
        try:
            reviewed = compute_review(review_sheet)
        except ValueError as error:
            reviewed, note = [], str(error)
            lines.append(f"Sheet could not be scored: {note}")
        if reviewed:
            lines.append(
                "| Architecture | Reviewed | Ok | Doubtful (counted as error) | Accuracy | 95 % Wilson interval "
                "| Auto vs reviewer disagreements |"
            )
            lines.append("|---|---|---|---|---|---|---|")
            for row in reviewed:
                lines.append(
                    f"| {row['arch']} | {row['reviewed']} | {row['ok']} | {row['doubtful']} | {_pct(row['accuracy'])} | "
                    f"{_pct(row['ci_low'])} to {_pct(row['ci_high'])} | {row['auto_vs_reviewer_disagreements']} |"
                )
        lines.append("")
    lines.append("## How to read this")
    lines.append("")
    lines.append("- *Fully correct* needs the intent, all seven quote fields and a reply that does its job, all at once.")
    lines.append("- Costs use the list price of each model on the date in prices.yaml, with cached input at its own price.")
    lines.append("- *Calls priced by estimate* are calls whose API gave no usage; their tokens were estimated from text length.")
    lines.append("- Every call is one line in `ledger.jsonl`; every message, one line in `results.jsonl`. Regroup them as you need.")
    lines.append("")
    report_path = run_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    (run_dir / "summary.json").write_text(
        json.dumps({"simulated": simulated, "frontier": frontier, "summary": summary.to_dicts()}, indent=1), encoding="utf-8"
    )
    return report_path
