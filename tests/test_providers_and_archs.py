from pathlib import Path

import pytest

from bench.archs import run_modular, run_single
from bench.meter import Meter
from bench.providers import CassetteMiss, get_provider, parse_json, resolve_mode
from bench.providers.cassette import Cassette
from bench.providers.mock import MockProvider
from bench.schemas import Completion, Intent, Usage
from bench.score import aggregate, score_all
from bench.synth import generate


class FakeAPI:
    """Counts real calls, so a replay can be told from a recording."""

    name = "fake"

    def __init__(self):
        self.calls = 0

    def complete(self, model, system, user, *, max_tokens=400, temperature=0.0):
        self.calls += 1
        usage = Usage(input_tokens=10, output_tokens=5)
        return Completion(text='{"intent": "quote"}', usage=usage, latency_ms=42, model=model, provider="fake")


def test_parse_json_tolerates_fences_and_prose():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! Here it is: {"a": {"b": 2}} hope it helps') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        parse_json("no json here")


def test_cassette_records_then_replays(tmp_path: Path):
    api = FakeAPI()
    recorder = Cassette(api, tmp_path, "record")
    first = recorder.complete("m", "sys", "usr")
    assert api.calls == 1 and not first.from_cassette
    replayer = Cassette(FakeAPI(), tmp_path, "offline")
    second = replayer.complete("m", "sys", "usr")
    assert second.from_cassette and second.text == first.text and second.usage == first.usage
    with pytest.raises(CassetteMiss):
        replayer.complete("m", "sys", "another prompt")


def test_mode_is_offline_without_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert resolve_mode("openai", "record") == "offline"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert resolve_mode("openai", None) in ("record", "offline", "live")
    assert resolve_mode("mock", "offline") == "live"


def test_mock_provider_is_deterministic():
    mock = MockProvider()
    cases = generate(5, seed=1)
    from bench.prompts import SINGLE_SYSTEM, user_message

    a = mock.complete("mock-small", SINGLE_SYSTEM, user_message(cases[0]))
    b = mock.complete("mock-small", SINGLE_SYSTEM, user_message(cases[0]))
    assert a.text == b.text and a.usage == b.usage


def test_both_architectures_run_and_guards_fire(tmp_path: Path):
    cases = generate(80, seed=7)
    mock = get_provider("mock")
    meter = Meter(tmp_path / "ledger.jsonl", "t")
    single = [run_single(c, mock, "mock-big", meter, label="single") for c in cases]
    modular = [run_modular(c, mock, "mock-small", mock, "mock-big", meter, label="modular") for c in cases]
    assert all(r.calls == 1 and r.retries == 0 for r in single)
    acks = [r for r, c in zip(modular, cases, strict=True) if c.truth.intent is Intent.ACKNOWLEDGEMENT]
    assert acks and all(r.calls == 0 and r.reply_text == "" for r in acks)  # rules, not a model
    assert sum(r.retries for r in modular) > 0  # the sloppy small model trips the guards
    summary = aggregate(score_all(single + modular, cases), meter.records)
    assert set(summary["arch"].to_list()) == {"single", "modular"}
    modular_row = summary.filter(summary["arch"] == "modular").row(0, named=True)
    single_row = summary.filter(summary["arch"] == "single").row(0, named=True)
    assert modular_row["cost_per_1k_usd"] < single_row["cost_per_1k_usd"]
    assert modular_row["cases_with_retry"] > 0
    assert 0 <= modular_row["correct"] <= 1
