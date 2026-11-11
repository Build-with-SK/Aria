"""
tests/test_pit_adversarial.py
=============================
At instant T, a prediction may depend only on what was knowable at T.

For every kind of input — price, volume, FX, fundamentals, news, factor
returns, regime (market and macro), and the performance record of the models
themselves — the information AFTER T is rewritten and the prediction at T
must be byte-for-byte identical (its hash included). Then the same kind of
input BEFORE T is rewritten and the prediction must change: a test that
cannot fail proves nothing, so each future mutation is paired with a past one.

T is 15:00 New York on Friday 2026-08-14, inside the session, so that day's
bar is not yet final and must be treated as future too.
"""
import copy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("exchange_calendars")

from intel_helpers import clustered_record  # noqa: E402
from src.intelligence import pit  # noqa: E402
from src.intelligence import registry  # noqa: E402

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
T = datetime(2026, 8, 14, 15, 0, tzinfo=NY)
DATES = pd.bdate_range("2025-03-03", "2026-09-30")
TODAY = pd.Timestamp("2026-08-14")


@pytest.fixture(autouse=True)
def _status_log(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "STATUS_LOG", tmp_path / "model_status.jsonl")


def _frame(seed, drift):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(drift + 0.012 * rng.standard_normal(len(DATES))))
    v = rng.integers(1_000_000, 3_000_000, len(DATES)).astype(float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c,
                         "Volume": v}, index=DATES)


def make_data() -> pit.PointInTimeData:
    rng = np.random.default_rng(42)
    # 70 sessions from 4 May: the last calls are graded AFTER T, so "future
    # model performance" has something to rewrite.
    records = clustered_record(70, 6, lambda k: 0.55, start="2026-05-04",
                               direction_fn=lambda k, j: "bear" if j % 3 == 0 else "bull")
    return pit.PointInTimeData(
        bars={"AAPL": _frame(1, 0.0008), "SPY": _frame(2, 0.0025)},
        venues={"AAPL": "XNYS", "SPY": "XNYS"},
        fx={"EURUSD": pd.Series(1.08 + 0.002 * np.cumsum(rng.standard_normal(len(DATES))),
                                index=DATES)},
        fundamentals=[
            {"symbol": "AAPL", "field": "eps", "value": 1.20,
             "available_at": datetime(2026, 5, 1, 20, 30, tzinfo=UTC)},
            {"symbol": "AAPL", "field": "eps", "value": 1.52,
             "available_at": datetime(2026, 7, 30, 20, 30, tzinfo=UTC)},
            {"symbol": "AAPL", "field": "revenue", "value": 94.0,
             "available_at": datetime(2026, 7, 30, 20, 30, tzinfo=UTC)},
        ],
        news=[
            {"symbol": "AAPL", "headline": "a", "sentiment": 0.4,
             "published_at": datetime(2026, 8, 11, 13, 0, tzinfo=UTC)},
            {"symbol": "AAPL", "headline": "b", "sentiment": -0.2,
             "published_at": datetime(2026, 8, 13, 13, 0, tzinfo=UTC)},
        ],
        factor_returns=pd.DataFrame({"MKT": 0.01 * rng.standard_normal(len(DATES)),
                                     "SIZE": 0.005 * rng.standard_normal(len(DATES))},
                                    index=DATES),
        macro_regimes=[{"label": "Expansion (Goldilocks)",
                        "available_at": datetime(2026, 7, 15, 12, 0, tzinfo=UTC)}],
        model_records=records,
    )


BASE = None


def base():
    global BASE
    if BASE is None:
        BASE = pit.predict_at("AAPL", T, make_data(), fx_pair="EURUSD")
    return BASE


def after(index):
    return index >= TODAY                       # today's bar is not final at 15:00


def before_cut(index):
    return (index < TODAY) & (index >= TODAY - pd.Timedelta(days=4))


# ── future information: nothing may change ─────────────────────────────────

def m_future_price(d):
    for f in d.bars.values():
        f.loc[after(f.index), ["Open", "High", "Low", "Close"]] *= 3.0


def m_future_volume(d):
    f = d.bars["AAPL"]
    f.loc[after(f.index), "Volume"] *= 50


def m_today_unfinished_bar(d):
    f = d.bars["AAPL"]
    f.loc[f.index == TODAY, ["Close", "Volume"]] = [1.0, 1e12]


def m_future_fx(d):
    s = d.fx["EURUSD"]
    s[s.index >= TODAY] = 9.99                   # a fixing dated today is known tomorrow


def m_future_fundamentals(d):
    d.fundamentals.append({"symbol": "AAPL", "field": "eps", "value": 99.0,
                           "available_at": datetime(2026, 8, 14, 19, 5, tzinfo=NY)})
    d.fundamentals.append({"symbol": "AAPL", "field": "guidance", "value": -1.0,
                           "available_at": datetime(2026, 8, 20, 12, 0, tzinfo=UTC)})


def m_future_news(d):
    d.news.append({"symbol": "AAPL", "headline": "after", "sentiment": -1.0,
                   "published_at": T + timedelta(minutes=1)})


def m_future_factors(d):
    f = d.factor_returns
    f.loc[f.index >= TODAY, :] = 0.5


def m_future_regime(d):
    d.macro_regimes.append({"label": "Recession Risk",
                            "available_at": T + timedelta(hours=1)})
    f = d.bars["SPY"]
    f.loc[after(f.index), "Close"] *= 0.2


def m_future_model_performance(d):
    cut = T.astimezone().replace(tzinfo=None).isoformat()
    for r in d.model_records:
        if (r.get("resolved_at") or "") > cut:
            r["correct"] = 1 - (r["correct"] or 0)
    d.model_records.append(dict(d.model_records[0], id="late", external_ref="late",
                                created_at="2026-08-20T22:00:00"))


FUTURE = [m_future_price, m_future_volume, m_today_unfinished_bar, m_future_fx,
          m_future_fundamentals, m_future_news, m_future_factors, m_future_regime,
          m_future_model_performance]


@pytest.mark.parametrize("mutate", FUTURE, ids=lambda f: f.__name__)
def test_future_information_cannot_change_the_prediction(mutate):
    d = make_data()
    mutate(d)
    assert pit.predict_at("AAPL", T, d, fx_pair="EURUSD") == base()


# ── past information: the prediction must be able to change ────────────────

def m_past_price(d):
    f = d.bars["AAPL"]
    f.loc[before_cut(f.index), ["Open", "High", "Low", "Close"]] *= 1.25


def m_past_volume(d):
    f = d.bars["AAPL"]
    f.loc[f.index == TODAY - pd.Timedelta(days=1), "Volume"] *= 10


def m_past_fx(d):
    s = d.fx["EURUSD"]
    s[s.index == TODAY - pd.Timedelta(days=1)] = 1.5        # the latest fixing known at T


def m_past_fundamentals(d):
    d.fundamentals[1]["value"] = 0.10


def m_past_news(d):
    d.news.append({"symbol": "AAPL", "headline": "earlier", "sentiment": -0.9,
                   "published_at": T - timedelta(hours=3)})


def m_past_factors(d):
    f = d.factor_returns
    f.loc[(f.index < TODAY) & (f.index >= TODAY - pd.Timedelta(days=30)), "MKT"] *= -4


def m_past_regime(d):
    d.macro_regimes[0]["label"] = "Stagflation Risk"


def m_past_market_regime(d):
    f = d.bars["SPY"]
    window = (f.index < TODAY) & (f.index >= TODAY - pd.Timedelta(days=120))
    f.loc[window, "Close"] *= np.linspace(1.0, 0.4, int(window.sum()))


def m_past_model_performance(d):
    cut = T.astimezone().replace(tzinfo=None).isoformat()
    for r in d.model_records:
        if r.get("resolved_at") and r["resolved_at"] <= cut:
            r["correct"] = 1 - (r["correct"] or 0)


PAST = [m_past_price, m_past_volume, m_past_fx, m_past_fundamentals, m_past_news,
        m_past_factors, m_past_regime, m_past_market_regime, m_past_model_performance]


@pytest.mark.parametrize("mutate", PAST, ids=lambda f: f.__name__)
def test_past_information_can_change_the_prediction(mutate):
    d = make_data()
    mutate(d)
    out = pit.predict_at("AAPL", T, d, fx_pair="EURUSD")
    assert out["snapshot_hash"] != base()["snapshot_hash"]


# ── the reads themselves ────────────────────────────────────────────────────

def test_the_unfinished_bar_and_its_close_are_unknown_until_the_close():
    d = make_data()
    assert d.bars_at("AAPL", T).index[-1] == TODAY - pd.Timedelta(days=1)
    after_close = datetime(2026, 8, 14, 16, 1, tzinfo=NY)
    assert d.bars_at("AAPL", after_close).index[-1] == TODAY


def test_a_grade_after_t_is_pending_at_t():
    d = make_data()
    visible = d.model_records_at(T)
    cut = T.astimezone().replace(tzinfo=None).isoformat()
    late = [r for r in d.model_records if (r.get("resolved_at") or "") > cut]
    assert late, "fixture must contain grades after T"
    ids = {r["id"] for r in late}
    assert all(r["resolved"] == 0 and r["correct"] is None for r in visible if r["id"] in ids)


def test_a_naive_instant_is_refused():
    with pytest.raises(ValueError):
        pit.predict_at("AAPL", datetime(2026, 8, 14, 15), make_data())


def test_the_fixture_market_is_in_an_uptrend_at_t():
    """So the past-regime mutation (a crash) has a label to flip."""
    assert base()["snapshot"]["market_regime"]["trend"] == "BULL"


def test_the_prediction_is_the_technical_composite_with_context():
    out = base()
    assert out["model"] == "technical" and out["direction"] in ("bull", "bear", "neutral")
    snap = out["snapshot"]
    assert snap["last_bar"] == "2026-08-13"
    assert snap["fundamentals"] == {"eps": 1.52, "revenue": 94.0}
    assert snap["news"]["count"] == 2
    assert snap["macro_regime"] == "Expansion (Goldilocks)"
    assert out["evidence"]["routing"] == "ROUTING_INSUFFICIENT_EVIDENCE"
