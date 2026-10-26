"""Independent review, 2026-10-26 (Claude, reviewer seat) — backtest chronology.

ARIA-D4: in src/backtesting/backtester.run_backtest a stop is evaluated on
bar i's CLOSE, and in the same iteration the entry block may open a new
position at bar i's OPEN — a price from earlier in the same day than the
information (the stop-out at the close) that freed the capital. That is a
fill the strategy could not have had. Strict xfail until fixed.
"""
from __future__ import annotations

import pandas as pd
import pytest


@pytest.mark.xfail(strict=True, reason="ARIA-D4: re-entry at the open of the bar that stopped out at its close")
def test_no_entry_before_the_exit_that_freed_the_capital(monkeypatch):
    from src.backtesting import backtester as bt

    n = 120
    idx = pd.bdate_range("2024-01-01", periods=n)
    opens = [100.0] * n
    closes = [100.0] * n
    closes[5] = 94.0                       # bar 5 closes 6% down: the 5% stop fires at its close
    df = pd.DataFrame({"Open": opens, "Close": closes}, index=idx)
    # Hold the signal permanently bullish so only the chronology is tested.
    monkeypatch.setattr(bt, "_compute_historical_scores",
                        lambda d: pd.Series(50.0, index=d.index))

    res = bt.run_backtest("TEST", df, stop_loss_pct=0.05, max_drawdown_limit=0.99)
    trades = res.trades
    stop = next(t for t in trades if t.exit_reason == "stop_loss")
    after = [t for t in trades if t.entry_date >= stop.exit_date and t is not stop]
    assert after, "expected a later entry"
    assert after[0].entry_date > stop.exit_date, (
        f"re-entered on {after[0].entry_date} at that day's open after stopping out at its close")
