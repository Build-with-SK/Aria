"""
src/v5/dataquality.py
=====================
Checks on a daily OHLCV series before anything is computed from it.

Pure functions of a DataFrame. The market-data layer runs `assess()` on every
series it serves and attaches the result to the series' provenance, so a
module, the meta-review and the API can all see the same verdict.

What is checked, and how a legitimate market event is kept apart from bad data:

  non-positive close     always bad — no listed instrument trades at <= 0
  future-dated bars      always bad — a timestamp error or a broken feed
  OHLC inconsistency     High < Low, or Close outside [Low, High]: a warning
                         (vendors round; one bar is not a broken series)
  gaps                   more than GAP_BUSINESS_DAYS business days without a
                         bar: a warning (a suspension is a real event)
  jumps                  |log return| > JUMP_LOG on one bar. On an ADJUSTED
                         series this is a warning — crashes happen. On an
                         UNADJUSTED series a jump whose ratio sits on a common
                         split factor is a PROBABLE UNADJUSTED SPLIT, and is
                         bad: every return, ATR and momentum figure across it
                         would be wrong by the split ratio.

`status` is "ok", "warn" or "bad". "bad" means the series must not be served
as if it were sound; "warn" is carried as information.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

JUMP_LOG = 0.35                 # ~ +42% / -30% in one bar
GAP_BUSINESS_DAYS = 5
SPLIT_FACTORS = (2, 3, 4, 5, 8, 10, 15, 20, 1.5, 4 / 3, 2.5)
SPLIT_TOLERANCE = 0.03          # ratio within 3% of a factor

#: How each vendor adjusts the closes it returns. "unknown" is never assumed
#: to be adjusted.
ADJUSTMENT = {
    "yfinance": "split+dividend",          # history(auto_adjust=True)
    "alphavantage": "none",                # TIME_SERIES_DAILY: as traded
    "stooq": "split",                      # split-adjusted; dividend treatment unverified
    "raw_csv": "split+dividend",           # data_downloader: yfinance auto_adjust=True
}


def _split_factor(ratio: float) -> Optional[float]:
    """The split factor a price ratio sits on, or None. Both directions:
    a 4:1 split shows as 0.25, a 1:10 reverse split as 10."""
    if not (ratio > 0 and math.isfinite(ratio)):
        return None
    for f in SPLIT_FACTORS:
        for target in (1 / f, f):
            if abs(ratio / target - 1) <= SPLIT_TOLERANCE:
                return target
    return None


def jumps(df: pd.DataFrame) -> list[dict]:
    close = pd.to_numeric(df["Close"], errors="coerce")
    close = close[close > 0]
    if len(close) < 2:
        return []
    lr = np.log(close / close.shift(1)).dropna()
    out = []
    for ts, v in lr[lr.abs() > JUMP_LOG].items():
        ratio = float(math.exp(v))
        out.append({"date": pd.Timestamp(ts).date().isoformat(),
                    "ratio": round(ratio, 4),
                    "split_factor": _split_factor(ratio)})
    return out


def probable_splits(df: pd.DataFrame) -> list[dict]:
    return [j for j in jumps(df) if j["split_factor"] is not None]


def assess(df: Optional[pd.DataFrame], *, adjustment: str = "unknown",
           now: Optional[datetime] = None) -> dict:
    now = now or datetime.now()
    if df is None or len(df) == 0 or "Close" not in df.columns:
        return {"status": "bad", "issues": ["no data"], "adjustment": adjustment}
    issues, warnings = [], []
    close = pd.to_numeric(df["Close"], errors="coerce")

    nonpos = int((close <= 0).sum())
    if nonpos:
        issues.append(f"{nonpos} non-positive close(s)")

    idx = pd.DatetimeIndex(df.index)
    future = int((idx > pd.Timestamp(now + timedelta(days=1))).sum())
    if future:
        issues.append(f"{future} bar(s) dated in the future")

    if {"High", "Low"} <= set(df.columns):
        hi = pd.to_numeric(df["High"], errors="coerce")
        lo = pd.to_numeric(df["Low"], errors="coerce")
        bad_hl = int((hi < lo).sum())
        outside = int(((close > hi * 1.001) | (close < lo * 0.999)).sum())
        if bad_hl:
            warnings.append(f"{bad_hl} bar(s) with High below Low")
        if outside:
            warnings.append(f"{outside} close(s) outside the bar's High-Low range")

    if len(idx) > 1:
        gaps = np.busday_count(idx[:-1].values.astype("datetime64[D]"),
                               idx[1:].values.astype("datetime64[D]"))
        big = int((gaps > GAP_BUSINESS_DAYS).sum())
        if big:
            warnings.append(f"{big} gap(s) longer than {GAP_BUSINESS_DAYS} business days "
                            f"(longest {int(gaps.max())})")

    js = jumps(df)
    splits = [j for j in js if j["split_factor"] is not None]
    if adjustment == "none" and splits:
        issues.append("probable unadjusted split(s) on "
                      + ", ".join(f"{j['date']} (x{j['ratio']})" for j in splits[:5]))
    elif js:
        warnings.append(f"{len(js)} single-bar move(s) beyond {math.exp(JUMP_LOG) - 1:.0%}: "
                        + ", ".join(j["date"] for j in js[:5]))

    if adjustment == "unknown":
        warnings.append("adjustment convention unknown")
    elif adjustment == "none":
        warnings.append("unadjusted closes: dividends are not in returns")

    return {"status": "bad" if issues else "warn" if warnings else "ok",
            "issues": issues, "warnings": warnings, "adjustment": adjustment,
            "jumps": js[:10], "rows": int(len(df))}
