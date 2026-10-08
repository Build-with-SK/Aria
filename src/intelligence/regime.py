"""
src/intelligence/regime.py
==========================
Which regimes ARIA can honestly evaluate in — the audit, and the fix.

THE FINDING
-----------
Every graded ledger row carries the macro label "Expansion (Goldilocks)".
That is not contamination. src/macro/regime.stamp() reads the macro snapshot
at creation (or, for backfilled rows, a world snapshot taken BEFORE the call),
so the labels are point in time; the quadrant model (CPI, 10Y-2Y, VIX,
unemployment) simply did not cross a threshold between July and October 2026.
A slow monthly classifier is constant over a ten-week record. The consequence
is the same either way: per-macro-regime performance cannot be measured, and
`audit_macro()` says so with the counts.

THE FIX
-------
A second, faster regime axis computed POINT IN TIME from market prices, with
the same definitions the feature library already uses
(src/features/indicators.add_regime_flags):

    trend   BULL      close > SMA200 and SMA50 > SMA200
            BEAR      close < SMA200 and SMA50 < SMA200
            SIDEWAYS  otherwise
    vol     HIGH_VOL  20-day realised vol above the 80th percentile of its
                      own trailing 252 values
            LOW_VOL   below the 20th
            NORMAL_VOL otherwise
    risk    RISK_ON = BULL and LOW_VOL, RISK_OFF = BEAR and HIGH_VOL, else NEUTRAL

on a benchmark (SPY for the US market), using only the bars that could have
been known at the call's creation time (src/markets/sessions.bar_known_by).
Every input is a RATIO of prices from the same series, so a dividend or split
adjustment made after the call — which multiplies every earlier price by one
constant — cannot change it. Fewer than 200 known bars gives UNKNOWN, never a
default.

Whether these slices are reportable is then the evaluation's job: it reports
a regime comparison only when at least two regimes each hold enough graded
calls on enough dates.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

TREND_WINDOW_SHORT = 50
TREND_WINDOW_LONG = 200
VOL_WINDOW = 20
VOL_LOOKBACK = 252
VOL_MIN_HISTORY = 60
HIGH_PCT, LOW_PCT = 0.80, 0.20
REGIMES_IMPLEMENTED = ("BULL", "BEAR", "SIDEWAYS", "HIGH_VOL", "NORMAL_VOL",
                       "LOW_VOL", "RISK_ON", "RISK_OFF", "NEUTRAL")


def classify_market(closes: pd.Series) -> dict:
    """Trend / volatility / risk labels from closes ALREADY cut at the instant."""
    c = pd.Series(closes, dtype=float).dropna()
    c = c[c > 0]
    out = {"trend": "UNKNOWN", "vol": "UNKNOWN", "risk": "UNKNOWN",
           "bars": int(len(c)), "last_bar": str(c.index[-1].date()) if len(c) else None}
    if len(c) >= TREND_WINDOW_LONG:
        close = float(c.iloc[-1])
        s50 = float(c.iloc[-TREND_WINDOW_SHORT:].mean())
        s200 = float(c.iloc[-TREND_WINDOW_LONG:].mean())
        out["trend"] = ("BULL" if close > s200 and s50 > s200 else
                        "BEAR" if close < s200 and s50 < s200 else "SIDEWAYS")
        out["close_vs_sma200"] = round(close / s200 - 1, 5)
    r = np.log(c).diff().dropna()
    rv = r.rolling(VOL_WINDOW).std().dropna() * math.sqrt(252)
    if len(rv) >= VOL_MIN_HISTORY:
        hist = rv.iloc[-VOL_LOOKBACK:]
        now = float(rv.iloc[-1])
        hi, lo = float(hist.quantile(HIGH_PCT)), float(hist.quantile(LOW_PCT))
        out["vol"] = "HIGH_VOL" if now > hi else "LOW_VOL" if now < lo else "NORMAL_VOL"
        out["realised_vol"] = round(now, 5)
    if out["trend"] != "UNKNOWN" and out["vol"] != "UNKNOWN":
        out["risk"] = ("RISK_ON" if out["trend"] == "BULL" and out["vol"] == "LOW_VOL" else
                       "RISK_OFF" if out["trend"] == "BEAR" and out["vol"] == "HIGH_VOL" else
                       "NEUTRAL")
    out["label"] = (f"{out['trend']}/{out['vol']}"
                    if out["trend"] != "UNKNOWN" and out["vol"] != "UNKNOWN" else None)
    return out


def _aware(at) -> datetime:
    if isinstance(at, str):
        at = datetime.fromisoformat(at)
    if at.tzinfo is None:
        at = at.astimezone()                    # the ledger stores this machine's local time
    return at


class MarketRegimeLabeler:
    """Point-in-time market regime for a call, from one benchmark series.

    `closes` is the benchmark's daily closes (DatetimeIndex). For a call made
    at T only the bars whose session had closed by T are used; the cut is found
    by binary search, so labelling a thousand calls is cheap.
    """

    def __init__(self, closes: pd.Series, *, venue: str = "XNYS", benchmark: str = "SPY",
                 settle_minutes: int = 0):
        s = pd.Series(closes, dtype=float).dropna().sort_index()
        if getattr(s.index, "tz", None) is not None:
            s.index = s.index.tz_localize(None)
        self.closes = s
        self.venue = venue
        self.benchmark = benchmark
        self.settle_minutes = settle_minutes
        self._dates = [d.date() for d in s.index]
        self._cache: dict[int, dict] = {}

    def known_count(self, at) -> int:
        """How many leading bars could have been known at `at`."""
        from src.markets import sessions
        at = _aware(at)
        lo, hi = 0, len(self._dates)
        while lo < hi:                           # availability is monotone in date
            mid = (lo + hi) // 2
            if sessions.bar_known_by(self.venue, self._dates[mid], at, self.settle_minutes):
                lo = mid + 1
            else:
                hi = mid
        return lo

    def at(self, at) -> dict:
        k = self.known_count(at)
        if k not in self._cache:
            out = classify_market(self.closes.iloc[:k])
            out["benchmark"] = self.benchmark
            self._cache[k] = out
        return dict(self._cache[k])

    def __call__(self, row: dict) -> Optional[dict]:
        created = row.get("created_at")
        if not created:
            return None
        return self.at(created)


def audit_macro(rows: list[dict]) -> dict:
    """What the stamped macro labels can and cannot support."""
    from src.intelligence.dataset import macro_regime
    labels, statuses = Counter(), Counter()
    graded_labels = Counter()
    stale = 0
    seq = []
    for r in rows:
        label, status = macro_regime(r)
        statuses[status] += 1
        if label:
            labels[label] += 1
            seq.append((r.get("created_at") or "", label))
            if r.get("resolved") and r.get("correct") is not None:
                graded_labels[label] += 1
        if "STALE" in (r.get("regime_basis") or ""):
            stale += 1
    seq.sort()
    transitions = sum(1 for a, b in zip(seq, seq[1:]) if a[1] != b[1])
    from src.macro import regime as macro
    verdict = ("per-macro-regime performance is NOT measurable: every labelled graded "
               "call carries the same label" if len(graded_labels) < 2 else
               "at least two macro regimes are present among graded calls")
    return {
        "classifier": "src.macro.regime.classify (CPI / 10Y-2Y / VIX / unemployment quadrant)",
        "thresholds": {"high_inflation_above": macro.HIGH_INFLATION_ABOVE,
                       "inverted_below": macro.INVERTED_BELOW,
                       "vix_stress_above": macro.VIX_STRESS_ABOVE,
                       "unemployment_stress_above": macro.UNEMPLOYMENT_STRESS_ABOVE},
        "labels": dict(labels), "graded_labels": dict(graded_labels),
        "validity": dict(statuses),
        "stale_input_rows": stale,
        "transitions_in_creation_order": transitions,
        "lookahead_rows": statuses.get("LOOKAHEAD", 0),
        "verdict": verdict,
        "why": ("stamped at creation from the snapshot then held (or from a world "
                "snapshot taken before the call): point in time. The quadrant is "
                "monthly-slow and did not cross a threshold in the record, so it is "
                "constant, not contaminated."),
    }


def benchmark_labeler(symbol: str = "SPY") -> Optional[MarketRegimeLabeler]:
    """A labeller on the vendor's series for `symbol` (network / cache)."""
    from src.v5 import marketdata as md
    c = md.provider_closes(symbol)
    if c is None or len(c) < TREND_WINDOW_LONG:
        return None
    return MarketRegimeLabeler(c, benchmark=symbol)
