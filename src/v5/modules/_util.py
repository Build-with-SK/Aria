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
import os
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
                         ) -> tuple[int, int, float, int]:
    """(successes, n_effective, mean_forward_return, n_raw) where `mask` is True.

    `n_effective` is the INDEPENDENCE-DISCOUNTED count, and returning it in the
    position callers already read is the whole point of this function's shape.

    A 21-day forward return starting today and one starting tomorrow share
    twenty of their twenty-one days. Counting both as independent evidence is
    the classic way to manufacture statistical power: daily sampling of a
    21-day horizon inflates the sample by ~21x, and Wilson — which is correct
    arithmetic on a wrong n — dutifully returns an interval ~4.6x too narrow.

    That did not merely look precise. `from_probability` maps interval width to
    neutral mass with a floor of 0.10, so a fake-narrow interval hit the floor
    and was granted the MAXIMUM permitted conviction. `seasonality` shipped
    +20.9 net from ~10 real Junes dressed up as 210 daily observations, while
    its own declared weakness said it "overstates the evidence badly". It did,
    and the number that reached the user was the overstated one.

    Non-overlapping windows are what independence actually looks like here, so
    n_effective = n_raw / horizon, and successes are scaled with it to preserve
    the hit rate. `quant.bayesian` already did exactly this; the rest of the
    engine now does too.

    n_raw is returned fourth for evidence text — "212 trading days" is a fair
    description of the data, as long as it is not what the statistics are
    built on.
    """
    fwd = forward_return(closes, horizon)
    sel = fwd[mask.reindex(fwd.index).fillna(False)].dropna()
    if sel.empty:
        return (0, 0, 0.0, 0)

    n_raw = int(len(sel))
    succ_raw = int((sel > 0).sum())
    p = succ_raw / n_raw

    n_eff = max(1, int(round(n_raw / max(1, int(horizon)))))
    succ_eff = int(round(p * n_eff))
    return (succ_eff, n_eff, float(sel.mean()), n_raw)


# The history a CONDITIONAL base-rate module needs, and the arithmetic behind
# the number.
#
# These modules ask "what happened after this state historically", so their
# sample is (bars x how often the state occurs) / horizon, because
# conditional_hit_rate discounts overlapping forward windows down to
# independent ones. For a tercile state at the house 21-day horizon:
#
#     5 years  ~ 1256 bars x 0.33 / 21  ~  20 effective observations
#    10 years  ~ 2513 bars x 0.33 / 21  ~  39 effective observations
#
# min_n is 20. Five years therefore lands exactly ON the threshold and falls
# under it whenever the state is slightly rarer than a third — which is why
# momentum, clustering, market_regime and volatility abstained on every single
# one of 240 walk-forward evaluations while looking, from the outside, like
# modules that simply had no opinion.
#
# Ten years is not a tuned number and was not chosen by trying values until the
# modules spoke: it is the history marketdata already fetches and caches
# (FULL_PERIOD), so these modules were discarding half of what was in memory.
# Whether the extra history HELPS is a separate question from whether it clears
# the threshold, and it is answered by walk-forward, not by this comment.
# Overridable so the walk-forward harness can run BOTH arms under identical
# harness code — otherwise "the new lookback is better" is a comparison between
# two different measuring instruments as well as two different settings.
BASE_RATE_LOOKBACK = os.environ.get("ARIA_BASE_RATE_LOOKBACK", "10y").strip() or "10y"


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
