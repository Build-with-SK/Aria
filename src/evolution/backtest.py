"""
src/evolution/backtest.py
=========================
The same backtest quant_lab runs, fast enough to run it thousands of times.

quant_lab._positions walks `mean_reversion_z` and `rsi_reversal` day by day
in Python — fine for the handful of strategies a paper suggests, hopeless for
a population of hundreds bred over dozens of generations. Both are the same
state machine:

    flat, and the signal crosses the entry level  ->  hold
    holding, and the signal crosses the exit      ->  flat
    otherwise                                     ->  unchanged

"Otherwise unchanged" is a forward-fill. Marking entries 1, exits 0 and every
other bar NaN, then ffilling, reproduces the loop exactly — including how it
treats the NaN warm-up period, where the loop leaves the state alone. That
equivalence is not assumed; tests/test_evolution_backtest.py asserts the two
agree cell for cell, so this file can only ever be a speed-up.

Costs, weighting and the return calculation are quant_lab's, imported rather
than copied: two backtesters that disagree is worse than a slow one.
"""
from __future__ import annotations

from src.brain.quant_lab import COST_PER_SIDE


def fast_positions(template: str, params: dict, closes):
    """0/1 positions, vectorised. Identical output to quant_lab._positions."""
    import numpy as np
    import pandas as pd

    if template == "mean_reversion_z":
        w = int(params["window"])
        z = (closes - closes.rolling(w).mean()) / closes.rolling(w).std()
        entry, exit_ = float(params["entry_z"]), float(params["exit_z"])
        signal = pd.DataFrame(np.nan, index=closes.index, columns=closes.columns)
        signal[z <= entry] = 1.0
        signal[z >= exit_] = 0.0
        return signal.ffill().fillna(0.0)

    if template == "rsi_reversal":
        p = int(params["period"])
        delta = closes.diff()
        up = delta.clip(lower=0).rolling(p).mean()
        dn = (-delta.clip(upper=0)).rolling(p).mean()
        rsi = 100 - 100 / (1 + up / dn)
        buy, sell = float(params["buy_below"]), float(params["sell_above"])
        signal = pd.DataFrame(np.nan, index=closes.index, columns=closes.columns)
        signal[rsi <= buy] = 1.0
        signal[rsi >= sell] = 0.0
        return signal.ffill().fillna(0.0)

    # The remaining templates are already vectorised upstream.
    from src.brain.quant_lab import _positions
    return _positions(template, params, closes)


def net_returns(genome, closes):
    """Daily net-of-cost portfolio returns for one genome."""
    pos = fast_positions(genome.template, genome.params, closes)
    active = pos.sum(axis=1)
    weights = pos.div(active.replace(0, 1), axis=0)
    daily = closes.pct_change().fillna(0.0)
    gross = (weights.shift(1).fillna(0.0) * daily).sum(axis=1)
    turnover = (weights - weights.shift(1)).abs().sum(axis=1).fillna(0.0)
    return gross - turnover * COST_PER_SIDE


def sharpe_of(rets, periods: int = 252) -> float | None:
    """Annualised Sharpe, or None when there is not enough to say.

    None rather than 0.0 on a dead or too-short series: a strategy that never
    traded has no Sharpe, and scoring it zero would let it outrank genuine
    losers and drift through selection doing nothing.
    """
    import numpy as np

    if rets is None or len(rets) < 30:
        return None
    sd = float(rets.std())
    if not np.isfinite(sd) or sd <= 1e-12:
        return None
    value = float(rets.mean() / sd * np.sqrt(periods))
    return value if np.isfinite(value) else None


def moments(rets) -> tuple[float, float]:
    """(skew, kurtosis) of the return series, for the deflated Sharpe.

    Kurtosis is the raw fourth moment, not excess: the deflation formula
    expects 3.0 for a normal distribution.
    """
    import numpy as np

    x = np.asarray(rets, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 4:
        return 0.0, 3.0
    sd = x.std()
    if sd <= 1e-12:
        return 0.0, 3.0
    centred = (x - x.mean()) / sd
    return float((centred ** 3).mean()), float((centred ** 4).mean())
