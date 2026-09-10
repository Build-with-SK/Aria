"""
tests/test_ml_persistence.py
============================
The ML stage must write what it computed.

A full pipeline run trains roughly 1,500 models over several hours, logs every
prediction, and — until this was fixed — dropped all of it. `run_ml()` returned
its results into a local variable that `main()` never persisted, and the only
writer of `data/ml_predictions.json` was `main_phase4_backup.py`, a file nobody
runs. So the live predictions had not moved since 2026-05-31 while five backend
readers and the brain's perception layer treated them as current.

Nothing was WRONG in the file — it was real output, once. It was just 98 days
old and presented as today's, which is the same class of failure as a
three-month-old VIX under a heading saying LIVE.

WHAT IS PINNED
--------------
  * the contract the existing readers depend on, field by field
  * that a run producing nothing does NOT clobber the last good file
  * that one malformed horizon cannot lose the whole ticker
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import main as pipeline


@dataclass
class _Result:
    """Stands in for EnsembleResult — same attribute names."""
    ticker: str = "AAPL"
    horizon: int = 5
    prediction: int = 1
    probability_up: float = 0.62
    confidence: float = 71.0
    base_learner_probs: dict = field(default_factory=lambda: {"xgboost": 0.61})
    meta_score: float = 0.3
    regime: str = "bull"
    regime_adjusted_prob: float = 0.64
    cv_mean_score: float = 0.55
    feature_importances: dict = field(default_factory=dict)
    model_version: str = "v1"


# ── the contract five readers depend on ────────────────────────────────────

def test_the_serialised_shape_matches_what_the_readers_expect():
    out = pipeline._ml_to_json({"AAPL": {1: _Result(horizon=1), 5: _Result()}})
    assert set(out) == {"AAPL"}
    row = out["AAPL"]
    for key in ("ticker", "horizons", "overall_bullish", "overall_signal",
                "models_trained", "warning"):
        assert key in row, f"the top-level contract lost {key}"

    h = row["horizons"]["5"]
    for key in ("horizon_days", "bullish_prob", "bearish_prob", "direction",
                "confidence", "model_agreement", "per_model_probs"):
        assert key in h, f"the horizon contract lost {key}"


def test_it_round_trips_through_json():
    out = pipeline._ml_to_json({"AAPL": {5: _Result()}})
    assert json.loads(json.dumps(out, default=str)) == json.loads(
        json.dumps(out, default=str))


def test_probabilities_are_complementary_and_directional():
    out = pipeline._ml_to_json({"AAPL": {5: _Result(regime_adjusted_prob=0.64)}})
    h = out["AAPL"]["horizons"]["5"]
    assert h["bullish_prob"] == pytest.approx(0.64)
    assert h["bullish_prob"] + h["bearish_prob"] == pytest.approx(1.0)
    assert h["direction"] == "Bullish"


def test_a_bearish_prediction_reads_as_bearish():
    out = pipeline._ml_to_json({"X": {5: _Result(regime_adjusted_prob=0.31)}})
    h = out["X"]["horizons"]["5"]
    assert h["direction"] == "Bearish"
    assert h["bearish_prob"] == pytest.approx(0.69)


def test_the_regime_adjusted_probability_is_the_one_published():
    """It is the number the ensemble actually stands behind."""
    out = pipeline._ml_to_json({"X": {5: _Result(probability_up=0.50,
                                                 regime_adjusted_prob=0.70)}})
    assert out["X"]["horizons"]["5"]["bullish_prob"] == pytest.approx(0.70)


@pytest.mark.parametrize("score,word", [(80.0, "High"), (45.0, "Moderate"),
                                        (10.0, "Low")])
def test_numeric_confidence_becomes_the_word_the_readers_expect(score, word):
    """The trainer reports 0-100; the file's consumers read the old engine's
    vocabulary. Both are kept — the word for compatibility, the number because
    throwing away precision to match a legacy string would be silly."""
    out = pipeline._ml_to_json({"X": {5: _Result(confidence=score)}})
    h = out["X"]["horizons"]["5"]
    assert h["confidence"] == word
    assert h["confidence_score"] == pytest.approx(score)


def test_the_overall_signal_summarises_every_horizon():
    out = pipeline._ml_to_json({"X": {1: _Result(regime_adjusted_prob=0.8),
                                      5: _Result(regime_adjusted_prob=0.8)}})
    assert out["X"]["overall_bullish"] == pytest.approx(0.8)
    assert out["X"]["overall_signal"] == "Bullish"
    assert out["X"]["models_trained"] == 2


# ── robustness ─────────────────────────────────────────────────────────────

def test_one_bad_horizon_does_not_lose_the_whole_ticker():
    class _Broken:
        def __getattr__(self, name):
            raise RuntimeError("corrupt result")
    out = pipeline._ml_to_json({"X": {1: _Broken(), 5: _Result()}})
    assert "X" in out, "a single malformed horizon discarded a good ticker"
    assert set(out["X"]["horizons"]) == {"5"}


def test_a_ticker_with_no_usable_horizon_is_omitted_not_emptied():
    out = pipeline._ml_to_json({"X": {1: None}})
    assert "X" not in out


def test_empty_input_produces_empty_output_rather_than_raising():
    assert pipeline._ml_to_json({}) == {}
    assert pipeline._ml_to_json(None) == {}


# ── the file itself ────────────────────────────────────────────────────────

def test_a_run_that_produced_nothing_does_not_clobber_the_last_good_file(
        tmp_path, monkeypatch):
    """THE rule. An empty run must leave the previous predictions alone.

    Overwriting real output with `{}` would turn a bad run into permanent data
    loss, and every reader would show nothing rather than something stale —
    which is worse, because stale is at least detectable.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    good = tmp_path / "data" / "ml_predictions.json"
    good.write_text(json.dumps({"AAPL": {"ticker": "AAPL"}}), encoding="utf-8")

    class _Trainer:
        def __init__(self, cfg): pass
        def train_and_predict_all(self, featured): return {}

    import src.models.model_trainer as mt
    monkeypatch.setattr(mt, "ModelTrainer", _Trainer)

    pipeline.run_ml({}, {})
    assert json.loads(good.read_text(encoding="utf-8")) == {"AAPL": {"ticker": "AAPL"}}


def test_a_successful_run_writes_the_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    class _Trainer:
        def __init__(self, cfg): pass
        def train_and_predict_all(self, featured):
            return {"AAPL": {5: _Result()}}

    import src.models.model_trainer as mt
    monkeypatch.setattr(mt, "ModelTrainer", _Trainer)

    pipeline.run_ml({}, {})
    written = json.loads(
        (tmp_path / "data" / "ml_predictions.json").read_text(encoding="utf-8"))
    assert written["AAPL"]["horizons"]["5"]["bullish_prob"] == pytest.approx(0.64)


def test_the_pipeline_is_the_only_writer_of_ml_predictions():
    """`main_phase4_backup.py` also writes this file and is not run by anything.
    Two writers of one artefact is how the live one went 98 days without moving
    while looking maintained."""
    writers = []
    for path in Path(".").glob("*.py"):
        text = path.read_text(encoding="utf-8-sig")
        if "ml_predictions.json" in text and "write_text" in text:
            writers.append(path.name)
    assert "main.py" in writers, "the live pipeline no longer writes predictions"


# ── the world model must age ML predictions like everything else ───────────

def test_the_world_model_tracks_ml_freshness():
    """It tracked six artefacts and ML was not one of them, so a file frozen
    in May was served as a current view for 98 days."""
    from src.core import world
    assert "ml" in world.STALE_AFTER_HOURS, (
        "ML predictions have no staleness budget, so they can never be reported "
        "as old however old they get")


def test_a_stale_ml_file_becomes_a_stated_blind_spot(tmp_path, monkeypatch):
    import json as _json
    from datetime import datetime, timedelta
    from src.core import world

    monkeypatch.setattr(world, "DATA", tmp_path)
    old = tmp_path / "ml_predictions.json"
    old.write_text(_json.dumps({"AAPL": {"overall_signal": "Bullish"}}),
                   encoding="utf-8")
    ancient = (datetime.now() - timedelta(days=99)).timestamp()
    import os
    os.utime(old, (ancient, ancient))

    market = world._market()
    assert market["ml"]["stale"] is True
    assert "98" in market["ml"]["why"] or "99" in market["ml"]["why"]

    unknowns = world._unknowns(market, world._portfolio(), world._record(),
                               world._system())
    assert any("ML predictions are stale" in u for u in unknowns), (
        "a 99-day-old ML file produced no blind spot")


def test_an_absent_ml_file_is_also_stated(tmp_path, monkeypatch):
    """Nothing is not the same as fresh."""
    from src.core import world
    monkeypatch.setattr(world, "DATA", tmp_path)
    market = world._market()
    assert market["ml"]["tickers"] == 0
    unknowns = world._unknowns(market, world._portfolio(), world._record(),
                               world._system())
    assert any("no ML predictions" in u for u in unknowns)


def test_the_ml_block_carries_a_timestamp():
    """A probability with no timestamp is indistinguishable from a current one."""
    from src.core import world
    ml = world._market()["ml"]
    for key in ("as_of", "stale", "tickers"):
        assert key in ml, f"the ML block has no {key}"


# ── the chat context must not present stale data as live ───────────────────
#
# The file above fixed the WRITER. This fixes the READER, and it took SENTINEL
# being wired to ARIA to expose it.
#
# Asked "what is ARIA's current view on NVDA", the local model answered:
#
#     "NVDA is currently in the ML Bullish list. Its score is +59.8,
#      confidence High. My recommendation is to Buy."
#
# Every part of that was wrong. NVDA's membership came from ml_predictions.json
# — 102 days old. The +59.8 and "High" came from the ADJACENT signals table and
# belonged to ZM=F, a soybean-meal futures contract. NVDA's actual signal that
# day was Neutral, confidence Low, bull_prob 0.513, risk Very High.
#
# The model was not hallucinating. It was reading a block headed LIVE MARKET
# STATE in which nothing was dated and nothing said which table a number came
# from. Both are properties of the prompt, so both are fixed in the prompt.

import time as _time


def _ctx(tmp_path, monkeypatch, *, ml_age_days=0.0, sig_age_hours=1.0):
    import backend.main as B
    monkeypatch.setattr(B, "ROOT", tmp_path)
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)

    (data / "signals.json").write_text(json.dumps({
        "ZM=F": {"ticker": "ZM=F", "composite_score": 59.8, "action": "Buy",
                 "confidence": "High"},
        "NVDA": {"ticker": "NVDA", "composite_score": 2.9, "action": "Neutral",
                 "confidence": "Low"},
    }), encoding="utf-8")
    (data / "ml_predictions.json").write_text(json.dumps({
        "NVDA": {"overall_signal": "Bullish"},
        "AAPL": {"overall_signal": "Bearish"},
    }), encoding="utf-8")
    (data / "macro_data.json").write_text(json.dumps({"regime": "Expansion"}),
                                          encoding="utf-8")

    for name, hours in (("ml_predictions.json", ml_age_days * 24),
                        ("signals.json", sig_age_hours)):
        when = _time.time() - hours * 3600
        os.utime(data / name, (when, when))
    return B._build_market_context()


def test_a_stale_ml_view_is_labelled_and_not_offered_as_current(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch, ml_age_days=102)
    assert "103 days old" in ctx or "102 days old" in ctx, (
        "the ML section carries no age, so nothing distinguishes it from today")
    assert "DO NOT PRESENT AS CURRENT" in ctx
    assert "STALE" in ctx


def test_nothing_claims_the_whole_block_is_live(tmp_path, monkeypatch):
    """The heading asserted LIVE over every section including a three-month-old
    one. A heading that overrides the data is the interface lying for it."""
    ctx = _ctx(tmp_path, monkeypatch, ml_age_days=102)
    assert "LIVE MARKET STATE" not in ctx


def test_a_fresh_ml_view_carries_no_warning(tmp_path, monkeypatch):
    """The warning has to mean something. If it is always there it is furniture."""
    ctx = _ctx(tmp_path, monkeypatch, ml_age_days=0.5)
    assert "DO NOT PRESENT AS CURRENT" not in ctx
    assert "NVDA" in ctx


def test_every_section_names_its_own_source_and_units(tmp_path, monkeypatch):
    """So a score cannot be lifted from one table onto a ticker in another —
    which is exactly how a corn contract's +59.8 became NVDA's."""
    ctx = _ctx(tmp_path, monkeypatch, ml_age_days=1)
    assert "MACRO (" in ctx and "SIGNALS (" in ctx and "ML MODEL VIEW" in ctx
    assert "composite" in ctx, "the score column is unnamed"
    assert "never" in ctx and "another" in ctx, (
        "nothing warns against combining rows from different sections")


def test_a_stale_signals_file_is_also_dated(tmp_path, monkeypatch):
    """Signals age too, and the same rule applies to them."""
    ctx = _ctx(tmp_path, monkeypatch, ml_age_days=1, sig_age_hours=96)
    assert "SIGNALS (4 days old, STALE)" in ctx


def test_a_missing_file_says_so_rather_than_reading_as_empty(tmp_path, monkeypatch):
    import backend.main as B
    monkeypatch.setattr(B, "ROOT", tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    assert B._source_age("nope.json", "ml") == ("MISSING", True)


def test_the_freshness_budget_is_not_duplicated(tmp_path, monkeypatch):
    """It reads the world model's table. Two staleness policies drift, and the
    one nobody looks at is the one feeding the chat."""
    import backend.main as B
    from src.core import world
    monkeypatch.setattr(world, "STALE_AFTER_HOURS", {"ml": 1})
    monkeypatch.setattr(B, "ROOT", tmp_path)
    data = tmp_path / "data"; data.mkdir(exist_ok=True)
    f = data / "ml_predictions.json"; f.write_text("{}", encoding="utf-8")
    when = _time.time() - 2 * 3600
    os.utime(f, (when, when))
    assert B._source_age("ml_predictions.json", "ml")[1] is True
