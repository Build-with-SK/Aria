"""
src/intelligence/research/analogues.py
======================================
"When this instrument looked like this before, what happened next?" — asked
point in time, answered with its sample, and never dressed up as a forecast.

STATE
-----
At every past date d, from bars up to d ONLY (rolling windows are causal):

    trend         close / SMA200 - 1
    mom21, mom63  21- and 63-day returns
    vol_pct       20-day realised vol's percentile within its trailing year
    rel21         21-day return minus the benchmark's
    bench_trend   benchmark close / its SMA200 - 1 (the market regime)
    volume        log of volume over its trailing 20-day mean

SEARCH
------
A date d is a candidate only if its forward return over the horizon was
already KNOWN at T (d + horizon <= the last bar known at T). Features are
standardised with means and deviations computed over the candidates only.
The k nearest by Euclidean distance are taken greedily with no two closer
than `horizon` bars, so their forward windows do not overlap and are not the
same observation counted twice.

REPORT
------
analogue_count, similarity_method, time_window, forward returns (mean,
median), dispersion (std, quartiles), win_rate, the unconditional base rate
over non-overlapping windows of the same history, and an exact binomial test
of the analogues' win rate against that base rate. Even a significant
difference is labelled DIFFERS_FROM_BASE_RATE (in-sample, not validated out
of sample) — never "predictive". Below MIN_ANALOGUES or MIN_HISTORY the
answer is ANALOGUE_INSUFFICIENT_DATA.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

INSUFFICIENT = "ANALOGUE_INSUFFICIENT_DATA"
MIN_HISTORY = 504
MIN_ANALOGUES = 10
DEFAULT_K = 20
FEATURES = ("trend", "mom21", "mom63", "vol_pct", "rel21", "bench_trend", "volume")


def state_frame(close: pd.Series, bench: Optional[pd.Series],
                volume: Optional[pd.Series]) -> pd.DataFrame:
    c = pd.Series(close, dtype=float)
    f = pd.DataFrame(index=c.index)
    f["trend"] = c / c.rolling(200).mean() - 1
    f["mom21"] = c / c.shift(21) - 1
    f["mom63"] = c / c.shift(63) - 1
    vol = np.log(c).diff().rolling(20).std() * math.sqrt(252)
    # Percentile of today's vol within its trailing year (ties count as <=).
    f["vol_pct"] = vol.rolling(252, min_periods=120).rank(method="max", pct=True)
    if bench is not None and len(bench):
        b = pd.Series(bench, dtype=float).reindex(c.index).ffill(limit=3)
        f["rel21"] = f["mom21"] - (b / b.shift(21) - 1)
        f["bench_trend"] = b / b.rolling(200).mean() - 1
    else:
        f["rel21"] = np.nan
        f["bench_trend"] = np.nan
    if volume is not None and len(volume):
        v = pd.Series(volume, dtype=float).reindex(c.index)
        f["volume"] = np.log(v / v.rolling(20).mean().shift(1))
    else:
        f["volume"] = np.nan
    return f


def _binom_p(k: int, n: int, p0: float) -> float:
    try:
        from scipy import stats
        return float(stats.binomtest(k, n, p0).pvalue)
    except Exception:                            # pragma: no cover - scipy present
        z = (k / n - p0) / math.sqrt(p0 * (1 - p0) / n)
        return float(math.erfc(abs(z) / math.sqrt(2)))


def find(close: pd.Series, bench: Optional[pd.Series], volume: Optional[pd.Series], *,
         horizon: int = 21, k: int = DEFAULT_K) -> dict:
    """Analogues of the LAST state in `close` (already cut at T)."""
    c = pd.Series(close, dtype=float).dropna()
    if len(c) < MIN_HISTORY:
        return {"status": INSUFFICIENT,
                "why": f"{len(c)} bars known at T; {MIN_HISTORY} needed",
                "similarity_method": None}
    f = state_frame(c, bench, volume)
    feats = [x for x in FEATURES if f[x].notna().sum() > MIN_HISTORY // 2]
    cur = f[feats].iloc[-1]
    if cur.isna().any():
        return {"status": INSUFFICIENT, "why": "the current state is incomplete: "
                + ", ".join(cur.index[cur.isna()]), "similarity_method": None}
    n = len(c)
    fwd = c.shift(-horizon) / c - 1
    pos = np.arange(n)
    cand_mask = (pos + horizon <= n - 1) & f[feats].notna().all(axis=1).to_numpy()
    cand = f[feats][cand_mask]
    if len(cand) < MIN_ANALOGUES * horizon // 2:
        return {"status": INSUFFICIENT, "why": f"{len(cand)} candidate dates with a known "
                f"{horizon}-day outcome", "similarity_method": None}
    mu, sd = cand.mean(), cand.std(ddof=1).replace(0, np.nan)
    z = ((cand - mu) / sd).fillna(0.0)
    zc = ((cur - mu) / sd).fillna(0.0)
    dist = np.sqrt(((z - zc) ** 2).sum(axis=1))
    order = dist.sort_values()
    chosen: list = []
    idx_pos = {ts: i for i, ts in enumerate(c.index)}
    for ts, d in order.items():
        p = idx_pos[ts]
        if all(abs(p - idx_pos[q]) >= horizon for q, _ in chosen):
            chosen.append((ts, float(d)))
        if len(chosen) >= k:
            break
    if len(chosen) < MIN_ANALOGUES:
        return {"status": INSUFFICIENT, "why": f"only {len(chosen)} non-overlapping "
                f"analogues", "similarity_method": "standardised Euclidean"}
    r = np.array([float(fwd.loc[ts]) for ts, _ in chosen])
    # Unconditional base rate: non-overlapping windows over the same history.
    base_idx = [ts for i, ts in enumerate(cand.index) if i % horizon == 0]
    base = np.array([float(fwd.loc[ts]) for ts in base_idx])
    wins = int((r > 0).sum())
    base_up = float((base > 0).mean()) if len(base) else None
    p = _binom_p(wins, len(r), base_up) if base_up not in (None, 0.0, 1.0) else None
    verdict = ("DIFFERS_FROM_BASE_RATE (in-sample, not validated out of sample)"
               if p is not None and p < 0.05 else "NOT_DEMONSTRATED")
    return {
        "status": "MEASURED",
        "analogue_count": len(r),
        "similarity_method": f"standardised Euclidean distance on {', '.join(feats)}; "
                             f"greedy non-overlapping ({horizon} bars apart)",
        "time_window": [str(c.index[0].date()), str(c.index[-1].date())],
        "history_years": round(len(c) / 252, 1),
        "horizon_bars": horizon,
        "forward_returns": {"mean": round(float(r.mean()), 5),
                            "median": round(float(np.median(r)), 5)},
        "dispersion": {"std": round(float(r.std(ddof=1)), 5),
                       "q25": round(float(np.quantile(r, 0.25)), 5),
                       "q75": round(float(np.quantile(r, 0.75)), 5)},
        "win_rate": round(wins / len(r), 4),
        "base_rate_up": None if base_up is None else round(base_up, 4),
        "base_rate_n": len(base),
        "p_value_vs_base_rate": None if p is None else round(p, 4),
        "predictive_value": verdict,
        "sample_quality": {"n": len(r), "non_overlapping": True,
                           "median_distance": round(float(np.median([d for _, d in chosen])), 3),
                           "max_distance": round(float(max(d for _, d in chosen)), 3),
                           "dates": [str(ts.date()) for ts, _ in chosen]},
    }
