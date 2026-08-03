"""
src/v5/modules/_util.py
=======================
Shared estimator helpers for the research modules.

The important one is `conditional_hit_rate`: instead of asserting "momentum is
bullish", a module asks the instrument's own history — *when this condition held
in the past, how often was the forward return positive, and over how many
observations?* That yields a real success count, which yields a real Wilson
interval, which mechanically caps how confident the module is allowed to be.

Where an estimator genuinely has no historical base rate (most fundamental
cross-sections), the module says so in its weaknesses and its interval is built
from the number of independent inputs it actually has — few inputs, wide
interval, mostly neutral mass.
"""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd

from src.v5.contract import wilson_interval


def logistic(x: float, k: float = 1.0) -> float:
    """Squash a signed signal to a probability. k scales how hard the signal
    has to push to move the probability."""
    try:
        return 1.0 / (1.0 + math.exp(-k * float(x)))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def clamp(x: float, lo: float, hi: float) -> float:
    """Bound x to [lo, hi], propagating NaN instead of hiding it.

    `max(lo, min(hi, nan))` silently returns `hi` in CPython, because every
    comparison with NaN is False. That turned a division by zero in one module
    into a maximally bullish score. NaN in, NaN out — the contract layer then
    converts it into an honest abstention.
    """
    x = float(x)
    if x != x:
        return float("nan")
    return max(lo, min(hi, x))


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=max(2, n // 2)).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(closes: pd.Series, n: int = 14) -> pd.Series:
    delta = closes.diff()
    up = delta.clip(lower=0).rolling(n).mean()
    down = (-delta.clip(upper=0)).rolling(n).mean()
    rs = up / down.replace(0, float("nan"))
    return (100 - 100 / (1 + rs)).fillna(50)


def zscore(s: pd.Series, window: int = 60) -> pd.Series:
    mu = s.rolling(window, min_periods=window // 2).mean()
    sd = s.rolling(window, min_periods=window // 2).std()
    return ((s - mu) / sd.replace(0, float("nan"))).fillna(0)


def ann_vol(returns: pd.Series, periods: int = 252) -> float:
    if returns is None or len(returns) < 5:
        return 0.0
    return float(returns.std() * math.sqrt(periods))


def max_drawdown(closes: pd.Series) -> float:
    if closes is None or len(closes) < 2:
        return 0.0
    peak = closes.cummax()
    return float(((closes - peak) / peak).min())


def forward_return(closes: pd.Series, horizon: int) -> pd.Series:
    return closes.shift(-horizon) / closes - 1.0


def conditional_hit_rate(closes: pd.Series, mask: pd.Series, horizon: int
                         ) -> tuple[int, int, float]:
    """(successes, n, mean_forward_return) for the periods where `mask` is True.

    `n` is deliberately the raw observation count; overlapping windows make
    these observations correlated, which every caller must declare as a
    weakness — the Wilson interval built from it is therefore optimistic, and
    modules that use it say so.
    """
    fwd = forward_return(closes, horizon)
    sel = fwd[mask.reindex(fwd.index).fillna(False)].dropna()
    if sel.empty:
        return (0, 0, 0.0)
    return (int((sel > 0).sum()), int(len(sel)), float(sel.mean()))


def hit_rate_probability(successes: int, n: int, min_n: int = 20
                         ) -> Optional[tuple[float, tuple[float, float]]]:
    """(p, (lo, hi)) from an empirical hit rate, or None below `min_n` —
    the caller must then abstain rather than invent a number."""
    if n < min_n:
        return None
    p = successes / n
    return (p, wilson_interval(successes, n))


def effective_interval(p: float, n_inputs: int, floor: float = 0.12
                       ) -> tuple[float, float]:
    """Interval for an estimate built from `n_inputs` independent inputs rather
    than from a historical base rate. Deliberately conservative: the interval
    never gets narrower than `floor`, because a handful of cross-sectional
    metrics cannot support a tight claim about a forward probability."""
    n_eff = max(1, int(n_inputs))
    lo, hi = wilson_interval(p * n_eff, n_eff)
    if hi - lo < floor:
        pad = (floor - (hi - lo)) / 2
        lo, hi = max(0.0, lo - pad), min(1.0, hi + pad)
    return (round(lo, 4), round(hi, 4))


def pct(x: Optional[float], digits: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def overlap_weakness(horizon: int) -> str:
    return (f"Base rate uses overlapping {horizon}-day forward windows, so the "
            f"observations are serially correlated and the confidence interval "
            f"is narrower than a fully independent sample would justify.")


def regime_weakness() -> str:
    return ("The historical base rate assumes the current regime resembles the "
            "sample period; a structural break invalidates it without warning.")
