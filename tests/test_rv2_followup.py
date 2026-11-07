"""Regression tests for Codex review findings RV2-01..RV2-04 (review of 268af1d).

RV2-01  a bar with a missing value skipped its gap stop, intrabar stop and
        pending breaker exit, and marked the position at its entry value;
RV2-02  a weekly or monthly series with a missing period was still annualised;
RV2-03  a replay could jump over whole rule days with no reset observation;
RV2-04  a replay accepted conflicting observations at one instant.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import numpy as np
import pandas as pd
import pytest

from test_multiasset_risk import case, NOW  # noqa: F401  (fixture)
from test_review_findings import probe_frame, put, day

from src.backtesting.backtester import run_backtest
from src.backtesting.challenge_replay import EquityObservation, replay_path
from src.backtesting.metrics import periods_per_year

UTC = timezone.utc
NAN = float("nan")

# All-in, cost-free, so prices read straight off the equity curve: the probe
# frame enters long at bar 1's open of 100 and holds a flat 100 market.
ALL_IN = dict(commission_pct=0, slippage_pct=0, risk_per_trade=1, stop_loss_pct=.05,
              max_drawdown_limit=.9)


def without_signal(df, row):
    """Make bar `row + 1`'s decision signal unavailable (score at `row` is NaN)."""
    return put(df, row, dist_sma_20=NAN)


# --- RV2-01: a missing value never erases a risk exit ------------------------

@pytest.mark.parametrize("row", [1, 120, 149])
def test_missing_close_is_refused(row):
    """The reviewer's case is row 120 with a gap open of 70. A missing close
    leaves the bar's mark, the next signal and the benchmark undefined, so the
    run is refused, as a missing open already is."""
    df = put(probe_frame(), row, Open=70., High=71., Low=69., Close=NAN)
    with pytest.raises(ValueError, match="Close"):
        run_backtest("FIXTURE", df, **ALL_IN)


def test_missing_signal_does_not_skip_a_gap_stop():
    df = put(without_signal(probe_frame(), 119), 120, Open=70., High=71., Low=69., Close=70.)
    result = run_backtest("FIXTURE", df, **ALL_IN)
    stops = [t for t in result.trades if t.exit_reason == "stop_loss"]
    assert [(t.exit_date, t.exit_price) for t in stops] == [(day(df, 120), 70.0)]
    assert result.equity_curve.iloc[120] == pytest.approx(70_000)


def test_missing_signal_does_not_skip_an_intrabar_stop():
    df = put(without_signal(probe_frame(), 119), 120, Open=100., High=100., Low=90., Close=99.)
    result = run_backtest("FIXTURE", df, **ALL_IN)
    stops = [t for t in result.trades if t.exit_reason == "stop_loss"]
    assert [(t.exit_date, t.exit_price) for t in stops] == [(day(df, 120), 95.0)]
    assert result.equity_curve.iloc[120] == pytest.approx(95_000)


def test_missing_signal_bar_is_marked_at_its_close():
    """Before RV2-01 the bar was marked at the entry value (100,000)."""
    df = put(without_signal(probe_frame(), 119), 120, Open=100., High=100., Low=96.5, Close=97.)
    result = run_backtest("FIXTURE", df, **ALL_IN)
    assert result.equity_curve.iloc[120] == pytest.approx(97_000)
    assert not [t for t in result.trades if t.exit_reason == "stop_loss"]


def test_missing_signal_does_not_delay_a_pending_breaker_exit():
    """The breaker trips at bar 120's close; the exit is due at bar 121's open
    (96), whether or not bar 121 has a signal."""
    df = put(probe_frame(), 120, Open=100., High=100., Low=96.5, Close=97.)
    df = put(without_signal(df, 120), 121, Open=96., High=97., Low=95.5, Close=96.)
    result = run_backtest("FIXTURE", df, **{**ALL_IN, "stop_loss_pct": .5, "max_drawdown_limit": .02})
    exits = [(t.exit_date, t.exit_reason, t.exit_price) for t in result.trades]
    assert exits == [(day(df, 121), "max_drawdown", 96.0)]
    assert result.equity_curve.iloc[121] == pytest.approx(96_000)


def test_missing_signal_still_blocks_entries():
    """Unchanged: no signal, no new position (the warm-up behaviour)."""
    df = probe_frame()
    df.iloc[:50, df.columns.get_loc("dist_sma_20")] = NAN
    result = run_backtest("FIXTURE", df, **ALL_IN)
    assert [t.entry_date for t in result.trades] == [day(df, 51)]
    assert (result.equity_curve.iloc[:51] == 100_000).all()


# --- RV2-02: an incomplete weekly or monthly grid is not annualised ----------

@pytest.mark.parametrize("freq", ["W", "W-MON", "W-FRI", "MS", "ME", "BME"])
def test_weekly_or_monthly_grid_missing_a_period_is_not_annualised(freq):
    """The reviewer's cases are W and MS with the 21st bar removed."""
    idx = pd.date_range("2020-01-01", periods=50, freq=freq)
    assert periods_per_year(idx, "CRYPTO") in (52.0, 12.0)
    assert periods_per_year(idx.delete(20), "CRYPTO") is None


@pytest.mark.parametrize("freq,expected", [("W-FRI", 52.0), ("BME", 12.0)])
def test_two_bars_in_one_period_are_not_annualised(freq, expected):
    idx = pd.date_range("2020-01-01", periods=50, freq=freq)
    assert periods_per_year(idx, "EXCHANGE") == expected
    extra = idx.insert(21, idx[20] + pd.Timedelta(days=1))
    assert periods_per_year(extra, "EXCHANGE") is None


@pytest.mark.parametrize("freq,expected", [
    ("W", 52.0), ("W-MON", 52.0), ("W-FRI", 52.0), ("MS", 12.0), ("ME", 12.0), ("BME", 12.0)])
def test_complete_weekly_and_monthly_grids_still_annualise(freq, expected):
    idx = pd.date_range("2020-01-01", periods=50, freq=freq)
    assert periods_per_year(idx, "EXCHANGE") == expected
    assert periods_per_year(idx.tz_localize("UTC"), "EXCHANGE") == expected


def test_weekly_bar_labelled_on_a_short_week_still_counts():
    """A week ending on a Thursday holiday is labelled Thursday: still one bar a week."""
    idx = pd.date_range("2020-01-01", periods=50, freq="W-FRI")
    idx = idx.delete(20).insert(20, idx[20] - pd.Timedelta(days=1))
    assert periods_per_year(idx, "EXCHANGE") == 52.0


# --- RV2-03 / RV2-04: a replay observes every rule day exactly once ----------

def prague_midnight(y, m, d):
    """00:00 Europe/Prague in November (UTC+1), as UTC."""
    return datetime(y, m, d, tzinfo=UTC) - timedelta(hours=1)


def observe(at, balance, equity=None):
    return EquityObservation(at=at, balance=D(balance), equity=D(equity if equity is not None else balance))


def test_replay_refuses_skipped_rule_days(case):
    """The reviewer's case: Friday, then Monday's reset, with no Saturday or Sunday."""
    path = (observe(NOW, "10000"), observe(prague_midnight(2026, 11, 9), "10000"))
    with pytest.raises(ValueError, match="2026-11-07"):
        replay_path(case[1].profile, path)


def test_replay_refuses_one_skipped_day_in_the_middle(case):
    path = (observe(NOW, "10000"), observe(prague_midnight(2026, 11, 7), "10000"),
            observe(prague_midnight(2026, 11, 9), "10000"))
    with pytest.raises(ValueError, match="2026-11-08"):
        replay_path(case[1].profile, path)


def test_replay_accepts_every_rule_day_including_the_weekend(case):
    path = (observe(NOW, "10000"), observe(prague_midnight(2026, 11, 7), "10000"),
            observe(prague_midnight(2026, 11, 8), "10000"),
            observe(prague_midnight(2026, 11, 9), "10000"),
            observe(prague_midnight(2026, 11, 9) + timedelta(hours=9), "10000", "9600"))
    result = replay_path(case[1].profile, path)
    assert result.outcome == "OPEN" and result.final_balance == D("10000")


def test_replay_refuses_conflicting_reset_observations(case):
    """The reviewer's case: two different balances at the same reset instant."""
    at = prague_midnight(2026, 11, 7)
    path = (observe(NOW, "10000"), observe(at, "10000"), observe(at, "10400"),
            observe(at + timedelta(minutes=1), "10400", "9890"))
    with pytest.raises(ValueError, match="time order"):
        replay_path(case[1].profile, path)


@pytest.mark.parametrize("second", [("10000", "9400"), ("10000", "10000")])
def test_replay_refuses_any_repeated_instant(case, second):
    """Within a day too, and even an identical repeat: which came first is unknowable."""
    at = NOW + timedelta(hours=2)
    path = (observe(NOW, "10000"), observe(at, "10000"), observe(at, *second))
    with pytest.raises(ValueError, match="time order"):
        replay_path(case[1].profile, path)
