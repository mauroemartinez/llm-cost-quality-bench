import csv
from pathlib import Path

from bench import cli
from bench.review import compute, write_sheet


def test_all_offline_with_mock(tmp_path: Path, monkeypatch, capsys):
    cases = tmp_path / "cases.jsonl"
    monkeypatch.setattr(cli, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(cli, "REVIEW", tmp_path / "review")
    cli.main(["all", "--pair", "mock", "--cases", str(cases), "--n-generate", "40", "--n", "40", "--run-id", "t", "--per-stratum", "2"])
    run = tmp_path / "results" / "t"
    assert (run / "ledger.jsonl").exists() and (run / "results.jsonl").exists()
    report = (run / "report.md").read_text(encoding="utf-8")
    assert "SIMULATED RESULTS" in report
    assert "Cost per 1,000" in report and (run / "img" / "pareto.png").exists()
    sheet = tmp_path / "review" / "t.csv"
    rows = list(csv.DictReader(sheet.open(encoding="utf-8", newline="")))
    assert rows and {r["arch"] for r in rows} and all(r["reviewer_verdict"] == "" for r in rows)
    out = capsys.readouterr().out
    assert "review sheet" in out


def test_review_compute_counts_doubtful_as_error(tmp_path: Path):
    rows = [
        {"case_id": f"c{i}", "arch": "a", "intent_truth": "quote", "supplier_message": "", "our_facts": "", "truth_quote": "",
         "model_intent": "", "model_quote": "", "model_reply": "", "auto_correct": "yes", "reviewer_verdict": v, "reviewer_note": ""}
        for i, v in enumerate(["ok"] * 18 + ["doubtful", "error"])
    ]
    sheet = tmp_path / "s.csv"
    write_sheet(rows, sheet)
    (result,) = compute(sheet)
    assert result["reviewed"] == 20 and result["ok"] == 18 and result["doubtful"] == 1
    assert result["accuracy"] == 0.9 and result["ci_low"] < 0.9 < result["ci_high"]
    assert result["auto_vs_reviewer_disagreements"] == 2


def test_calibrate_runs_on_mock(tmp_path: Path, monkeypatch, capsys):
    cases = tmp_path / "cases.jsonl"
    cli.main(["generate", "--n", "12", "--out", str(cases)])
    monkeypatch.setattr(cli, "RESULTS", tmp_path / "results")
    cli.main(["calibrate", "--provider", "mock", "--n", "12", "--cases", str(cases), "--run-id", "cal"])
    assert "against itself" in capsys.readouterr().out
