"""
tests/test_perception_ml_age.py
===============================
The brain's perception layer must not see an expired ML call as current.

The pipeline merges ML rows into data/ml_predictions.json a batch at a time
(src/models/ml_store), so the file's mtime only says when the last batch
landed. Perception loaded every row with no age at all: once the first fresh
batch touched the file, the brain daemon read a 130-day-old 20-day call as
today's view — in the reasoner's ANALYSE prompt and in novelty's ml_split.

WHAT IS PINNED
--------------
  * a row is aged by its own `generated_at`; a file with no stamped row by
    its mtime; an unstamped row among stamped ones has no age and is not live
  * a horizon whose window has elapsed (7/5 calendar days per trading day)
    is not perceived; a row with none left goes to `ml_expired`
  * a part-elapsed row's overall is the writer's summary of what is left
  * `snapshot.ml` keeps its shape for the reasoner and novelty
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.brain.cognitive import novelty
from src.brain.cognitive import perception as P

NOW = datetime(2026, 10, 20, 12, 0, 0)

HORIZONS = {
    "1": {"horizon_days": 1, "bullish_prob": 0.9, "direction": "Bullish"},
    "5": {"horizon_days": 5, "bullish_prob": 0.64, "direction": "Bullish"},
    "20": {"horizon_days": 20, "bullish_prob": 0.31, "direction": "Bearish"},
}


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "DATA", tmp_path)
    monkeypatch.setattr(P, "_LAST_REGIME_FILE", tmp_path / "brain_memory" / "last_regime.json")
    return tmp_path


def _row(ticker: str, *, age: timedelta | None, horizons=HORIZONS,
         overall=0.6167, signal="Bullish") -> dict:
    row = {"ticker": ticker, "horizons": horizons, "overall_bullish": overall,
           "overall_signal": signal, "models_trained": len(horizons), "warning": None}
    if age is not None:
        row["generated_at"] = (NOW - age).isoformat(timespec="seconds")
    return row


def _write(data, rows: dict, mtime: datetime | None = None):
    path = data / "ml_predictions.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime.timestamp(), mtime.timestamp()))
    return path


def test_a_130_day_old_stamped_row_is_not_perceived_as_live(data):
    # A fresh batch just touched the file; OLD is from the previous pass.
    _write(data, {"OLD": _row("OLD", age=timedelta(days=130)),
                  "NEW": _row("NEW", age=timedelta(hours=2))}, mtime=NOW)
    snap = P.MarketPerception().perceive(now=NOW)

    assert "OLD" not in snap.ml, "a 130-day-old row was perceived because the file was touched"
    old = snap.ml_expired["OLD"]
    assert old.horizons == {} and set(old.expired_horizons) == {"1", "5", "20"}
    assert old.made_at == NOW - timedelta(days=130)


def test_a_fresh_row_is_perceived_whole_with_the_files_own_numbers(data):
    _write(data, {"NEW": _row("NEW", age=timedelta(hours=2))})
    snap = P.MarketPerception().perceive(now=NOW)

    new = snap.ml["NEW"]
    assert set(new.horizons) == {"1", "5", "20"} and new.expired_horizons == {}
    assert (new.overall_bullish, new.overall_signal) == (0.6167, "Bullish")
    assert new.made_at == NOW - timedelta(hours=2)
    assert snap.ml_expired == {}


def test_a_legacy_unstamped_file_is_aged_by_its_mtime(data):
    _write(data, {"AAPL": _row("AAPL", age=None)}, mtime=NOW - timedelta(days=130))
    snap = P.MarketPerception().perceive(now=NOW)
    assert "AAPL" not in snap.ml and "AAPL" in snap.ml_expired
    assert snap.ml_expired["AAPL"].made_at == NOW - timedelta(days=130)

    _write(data, {"AAPL": _row("AAPL", age=None)}, mtime=NOW - timedelta(hours=3))
    snap = P.MarketPerception().perceive(now=NOW)
    assert set(snap.ml["AAPL"].horizons) == {"1", "5", "20"}


def test_an_unstamped_row_beside_stamped_ones_is_never_live(data):
    _write(data, {"OLD": _row("OLD", age=None), "NEW": _row("NEW", age=timedelta(hours=1))},
           mtime=NOW)
    snap = P.MarketPerception().perceive(now=NOW)
    assert "OLD" not in snap.ml and snap.ml_expired["OLD"].made_at is None
    assert "NEW" in snap.ml


def test_each_horizon_expires_on_its_own_window(data):
    # 3 calendar days: the 1-day call (1.4 days) has elapsed, 5d (7) and 20d (28) have not.
    _write(data, {"X": _row("X", age=timedelta(days=3))})
    x = P.MarketPerception().perceive(now=NOW).ml["X"]
    assert set(x.horizons) == {"5", "20"} and set(x.expired_horizons) == {"1"}

    # The window boundary: 5 trading days = 7 calendar days, inclusive.
    _write(data, {"X": _row("X", age=timedelta(days=7))})
    assert set(P.MarketPerception().perceive(now=NOW).ml["X"].horizons) == {"5", "20"}
    _write(data, {"X": _row("X", age=timedelta(days=7, seconds=1))})
    assert set(P.MarketPerception().perceive(now=NOW).ml["X"].horizons) == {"20"}


def test_a_part_elapsed_rows_overall_is_the_writers_summary_of_what_is_left(data):
    import main as pipeline
    r = lambda h, p: SimpleNamespace(horizon=h, regime_adjusted_prob=p)  # noqa: E731
    full = pipeline._ml_to_json({"X": {1: r(1, 0.9), 5: r(5, 0.64), 20: r(20, 0.31)}})["X"]
    left = pipeline._ml_to_json({"X": {5: r(5, 0.64), 20: r(20, 0.31)}})["X"]
    assert full["overall_signal"] == "Bullish" and left["overall_signal"] == "Neutral"

    full["generated_at"] = (NOW - timedelta(days=3)).isoformat(timespec="seconds")
    _write(data, {"X": full})
    x = P.MarketPerception().perceive(now=NOW).ml["X"]
    assert (x.overall_bullish, x.overall_signal) == (left["overall_bullish"],
                                                    left["overall_signal"]), (
        "the overall still blended the expired 1-day call")


def test_downstream_readers_see_only_what_is_live(data):
    # Novelty's ml_split is fed by snapshot.ml; an expired 0.9 must not move it.
    _write(data, {"OLD": _row("OLD", age=timedelta(days=130), overall=0.9)}, mtime=NOW)
    snap = P.MarketPerception().perceive(now=NOW)
    assert novelty.features_from(snap)["ml_split"] == 0.5
    assert snap.ml.get("OLD") is None, "the reasoner reads perception.ml.get(ticker)"

    _write(data, {"NEW": _row("NEW", age=timedelta(hours=1), overall=0.9)})
    snap = P.MarketPerception().perceive(now=NOW)
    assert novelty.features_from(snap)["ml_split"] == pytest.approx(0.1)
    assert isinstance(snap.ml["NEW"], P.MLPrediction)


def test_no_ml_file_perceives_no_ml(data):
    snap = P.MarketPerception().perceive(now=NOW)
    assert snap.ml == {} and snap.ml_expired == {}
    assert snap.timestamp == NOW
