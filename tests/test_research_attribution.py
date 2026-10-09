"""
tests/test_research_attribution.py
==================================
Relative-return attribution: betas from before the window, the window's move
split into market / sector / factor contributions and an UNEXPLAINED_RESIDUAL
that is never called alpha — on synthetic data whose true loadings and true
idiosyncratic jump are known.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("exchange_calendars")

import research_fixtures as F  # noqa: E402
from src.intelligence.research import attribution as A  # noqa: E402
from src.risk.market_data import FrameMarketData  # noqa: E402


@pytest.fixture(scope="module")
def frames():
    return F.build_frames()


def run(frames, symbol="AAPL", at=F.T):
    return A.attribute(symbol, at, FrameMarketData(frames))


def test_the_plan_uses_spy_and_the_sector_etf_for_a_us_stock():
    p = A.plan("AAPL")
    assert p["legs"]["market"] == ("SPY", None)
    assert p["legs"]["sector"] == ("XLK", "SPY")
    assert p["frequency"] == "daily"


def test_the_injected_residual_is_recovered_and_labelled(frames):
    w = run(frames)["windows"]["21d"]
    assert w["status"] == "MEASURED"
    injected = 21 * F.JUMP
    assert abs(w["residual"]["log"] - injected) < 0.035
    assert w["residual"]["label"] == "UNEXPLAINED_RESIDUAL"
    assert w["residual"]["unusual"] is True
    assert "alpha" not in w["sentence"].lower()
    assert "UNEXPLAINED_RESIDUAL" in w["sentence"]


def test_betas_are_estimated_strictly_before_the_window(frames):
    w = run(frames)["windows"]["21d"]
    assert w["estimation_window"][1] < w["window"][0]
    assert w["window"][1] == "2026-09-15"
    assert w["contributions"]["market"]["beta"] == pytest.approx(1.2, abs=0.15)


def test_contributions_add_up(frames):
    w = run(frames)["windows"]["21d"]
    total = sum(c["contribution"] for c in w["contributions"].values())
    assert total == pytest.approx(w["explained"], abs=1e-5)
    assert w["explained"] + w["residual"]["log"] == pytest.approx(w["log_return"], abs=1e-5)


def test_a_non_us_stock_is_weekly_with_a_currency_term_and_no_one_day_window(frames):
    out = run(frames, symbol="7203.T")
    assert out["frequency"] == "weekly"
    assert out["windows"]["1d"]["status"] == "NOT_APPLICABLE"
    w = out["windows"]["21d"]
    assert w["status"] == "MEASURED"
    assert {"market", "country", "currency"} <= set(w["contributions"])


def test_too_little_history_is_insufficient_not_a_guess(frames):
    short = {k: v[v.index >= "2026-06-01"] for k, v in frames.items()}
    out = run(short)
    assert out["windows"]["21d"]["status"] == "INSUFFICIENT_DATA"


def test_a_missing_proxy_is_reported_and_the_rest_still_attributes(frames):
    f = {k: v for k, v in frames.items() if k != "XLK"}
    out = run(f)
    assert "sector" in out["missing_factors"]
    assert out["windows"]["21d"]["status"] == "MEASURED"


def test_future_bars_cannot_change_the_attribution(frames):
    base = run(frames)
    fut = {k: v.copy() for k, v in frames.items()}
    for v in fut.values():
        v.loc[v.index > "2026-09-15", ["Close"]] *= 5.0
    assert run(fut) == base


def test_a_change_inside_the_estimation_window_changes_the_betas(frames):
    base = run(frames)["windows"]["21d"]["contributions"]["market"]["beta"]
    past = {k: v.copy() for k, v in frames.items()}
    est = (past["AAPL"].index > "2025-12-01") & (past["AAPL"].index < "2026-08-01")
    past["AAPL"].loc[est, "Close"] = past["AAPL"].loc[est, "Close"] * np.linspace(
        1, 1.6, int(est.sum()))
    assert run(past)["windows"]["21d"]["contributions"]["market"]["beta"] != base


def test_a_naive_instant_is_refused(frames):
    with pytest.raises(ValueError):
        A.attribute("AAPL", datetime(2026, 9, 15), FrameMarketData(frames))
