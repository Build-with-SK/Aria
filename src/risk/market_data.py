"""
src/risk/market_data.py
=======================
THE ONLY WAY THE RISK LAYER SEES PRICES, and it sees them as of an instant.

Every portfolio-risk number — FX, volatility, correlation, factor exposure,
liquidity — is computed from daily bars fetched through `MarketData.history
(symbol, at)`, which returns only bars that could have been known at `at`.
The cut is the one ARIA's backtests already use (src/v5/marketdata.
_truncate_point_in_time → src/markets/sessions.bar_known_by): a bar exists
once its own venue's session has closed, so at 09:00 Tokyo the New York bar
for the same date does not.

Two implementations:

  MdMarketData      production: src/v5/marketdata under `as_of(at)`.
  FrameMarketData   tests and replays: frames you supply, cut by the SAME
                    function, so a test of "future data cannot leak" tests
                    the production cut and not a second one.

Nothing here caches across instants. A value cached at one `at` and served
at another is exactly how a backtest learns the future.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

import pandas as pd


class MarketData(Protocol):
    def history(self, symbol: str, at: datetime) -> Optional[pd.DataFrame]:
        """Daily bars (Open/High/Low/Close/Volume, tz-naive DatetimeIndex)
        known by `at`, or None."""
        ...


def _cut(symbol: str, frame: pd.DataFrame, at: datetime) -> Optional[pd.DataFrame]:
    from src.v5 import marketdata as md
    when = pd.Timestamp(at)
    if when.tzinfo is None:
        raise ValueError("a risk calculation needs an aware instant, not a date")
    out = md._truncate_point_in_time(f"{md.EXACT}{symbol}", frame, when, "1d")
    return out if out is not None and len(out) else None


class FrameMarketData:
    """Frames supplied by the caller, cut point-in-time exactly as production
    data is. `frames` may be mutated by a test after construction; every call
    re-reads it, so a mutation after `at` must change nothing at `at`."""

    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = frames
        self.calls = 0

    def history(self, symbol: str, at: datetime) -> Optional[pd.DataFrame]:
        self.calls += 1
        f = self.frames.get(symbol)
        if f is None or f.empty:
            return None
        return _cut(symbol, f.sort_index(), at)


class MdMarketData:
    """src/v5/marketdata, point in time. A vendor failure is None, never a
    fabricated series."""

    def __init__(self, period: str = "2y"):
        self.period = period

    def history(self, symbol: str, at: datetime) -> Optional[pd.DataFrame]:
        from src.v5 import marketdata as md
        when = pd.Timestamp(at)
        if when.tzinfo is None:
            raise ValueError("a risk calculation needs an aware instant, not a date")
        try:
            with md.as_of(when):
                return md.history(f"{md.EXACT}{symbol}", period=self.period)
        except Exception:
            return None
