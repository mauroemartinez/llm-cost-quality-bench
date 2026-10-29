import json
from pathlib import Path

import pytest

from bench.meter import BudgetExceeded, Meter, read_ledger
from bench.pricing import cost_usd, estimate_tokens, load_prices, price_for
from bench.schemas import Completion, Usage
from bench.stats import median, outliers, percentile, wilson


def test_every_price_has_a_source_and_a_date():
    for price in load_prices().values():
        assert price.source.startswith("http") or price.provider == "mock"
        assert price.read_on


def test_cached_tokens_are_billed_at_their_own_price():
    price = price_for("mock-big")
    plain = cost_usd("mock-big", 1000, 0, 100)
    cached = cost_usd("mock-big", 1000, 800, 100)
    assert plain == pytest.approx((1000 * price.input + 100 * price.output) / 1e6)
    assert cached == pytest.approx((200 * price.input + 800 * price.cached_input + 100 * price.output) / 1e6)
    assert cached < plain
    # Cached can never exceed input.
    assert cost_usd("mock-big", 100, 500, 0) == pytest.approx(100 * price.cached_input / 1e6)


def test_unknown_model_is_an_error():
    with pytest.raises(KeyError):
        price_for("gpt-imaginary")


def test_deepseek_offpeak_halves(monkeypatch):
    peak = cost_usd("deepseek-flash", 1000, 0, 100)
    monkeypatch.setenv("BENCH_DEEPSEEK_OFFPEAK", "1")
    assert cost_usd("deepseek-flash", 1000, 0, 100) == pytest.approx(peak / 2)


def test_meter_writes_ledger_and_enforces_budget(tmp_path: Path):
    meter = Meter(tmp_path / "ledger.jsonl", "t", max_usd=0.00001)
    replay = Completion(
        text="{}", usage=Usage(input_tokens=1000, output_tokens=50), latency_ms=10, model="mock-big", provider="mock", from_cassette=True
    )
    meter.record(arch="a", stage="s", case_id="c", attempt=1, completion=replay, prompt_text="p", validation_passed=True, violations=[])
    assert meter.real_usd == 0  # replays cost nothing today
    live = replay.model_copy(update={"from_cassette": False})
    with pytest.raises(BudgetExceeded):
        meter.record(arch="a", stage="s", case_id="c", attempt=1, completion=live, prompt_text="p", validation_passed=True, violations=[])
    rows = read_ledger(tmp_path / "ledger.jsonl")
    assert len(rows) == 2 and rows[0].cost_method == "exact"
    assert json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[0])["cost_usd"] > 0


def test_estimated_usage_is_marked(tmp_path: Path):
    meter = Meter(tmp_path / "ledger.jsonl", "t")
    no_usage = Completion(text="x" * 400, usage=Usage(exact=False), latency_ms=1, model="mock-small", provider="mock")
    record = meter.record(
        arch="a", stage="s", case_id="c", attempt=1, completion=no_usage, prompt_text="y" * 800, validation_passed=True, violations=[]
    )
    assert record.cost_method == "estimated"
    assert record.input_tokens == estimate_tokens("y" * 800) == 200
    assert record.output_tokens == 100


def test_wilson_matches_known_values():
    estimate, low, high = wilson(95, 100)
    assert estimate == pytest.approx(0.95)
    assert low == pytest.approx(0.8872, abs=0.002)
    assert high == pytest.approx(0.9789, abs=0.002)
    assert wilson(0, 0) == (0.0, 0.0, 0.0)
    _, low0, _ = wilson(0, 20)
    assert low0 == 0.0


def test_percentiles_and_outliers():
    values = [1, 2, 3, 4, 100]
    assert median(values) == 3
    assert percentile(values, 95) == 100
    assert outliers(values) == [4]
    assert outliers([]) == []
