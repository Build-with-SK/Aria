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


def signed_positions(template: str, params: dict, closes):
    """-1/0/+1 positions when direction is long_short, else 0/1.

    The long_short mappings are STATELESS — the signal's own zones decide the
    position on every bar — rather than the entry/exit state machine used
    long-only. A symmetric state machine needs a third state and an exit rule
    per side, and every extra rule is another thing fitted to this sample.
    Zones are symmetric about the signal's neutral point, so a genome cannot
    quietly become a long-only strategy wearing a short's label.

    A consequence worth knowing: `exit_z` and the RSI midpoint stop mattering
    for long_short genomes, so those genomes search a smaller space than their
    long-only siblings.
    """
    import numpy as np
    import pandas as pd

    if params.get("direction", "long") != "long_short":
        return fast_positions(template, params, closes)

    if template == "mean_reversion_z":
        w = int(params["window"])
        z = (closes - closes.rolling(w).mean()) / closes.rolling(w).std()
        entry = abs(float(params["entry_z"]))
        band = abs(float(params["exit_z"]))
        pos = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
        pos[z <= -entry] = 1.0
        pos[z >= entry] = -1.0
        pos[(z > -band) & (z < band)] = 0.0
        return pos.where(z.notna(), 0.0)

    if template == "rsi_reversal":
        p = int(params["period"])
        delta = closes.diff()
        up = delta.clip(lower=0).rolling(p).mean()
        dn = (-delta.clip(upper=0)).rolling(p).mean()
        rsi = 100 - 100 / (1 + up / dn)
        buy, sell = float(params["buy_below"]), float(params["sell_above"])
        pos = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
        pos[rsi <= buy] = 1.0
        pos[rsi >= sell] = -1.0
        return pos.where(rsi.notna(), 0.0)

    if template == "ma_cross":
        fast = closes.rolling(int(params["fast"])).mean()
        slow = closes.rolling(int(params["slow"])).mean()
        pos = (fast > slow).astype(float) - (fast < slow).astype(float)
        return pos.where(slow.notna(), 0.0)

    if template == "breakout":
        w, ema = int(params["window"]), int(params["exit_ma"])
        hi = closes.rolling(w).max().shift(1)
        lo = closes.rolling(w).min().shift(1)
        ma = closes.rolling(ema).mean()
        longs = ((closes >= hi) | (closes > ma)) & (closes > ma)
        shorts = ((closes <= lo) | (closes < ma)) & (closes < ma)
        pos = longs.astype(float) - shorts.astype(float)
        return pos.where(ma.notna() & hi.notna(), 0.0)

    if template == "momentum_topn":
        # The classic cross-sectional long/short: buy the strongest names,
        # sell the weakest, which is what this template was always reaching
        # for when it could only go long.
        look, n = int(params["lookback"]), int(params["top_n"])
        mom = closes.pct_change(look)
        monthly = mom.resample("ME").last()
        best = monthly.rank(axis=1, ascending=False) <= n
        worst = monthly.rank(axis=1, ascending=True) <= n
        pos = best.astype(float) - worst.astype(float)
        return pos.reindex(closes.index, method="ffill").fillna(0.0)

    raise ValueError(f"unknown template {template}")


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
    """Daily net-of-cost portfolio returns for one genome.

    Weights are normalised by GROSS exposure — the sum of absolute positions
    — not by the signed sum. With shorts allowed the signed sum of three
    longs and three shorts is zero, and dividing by it (or by the 1 it gets
    replaced with) would silently hand the strategy six times leverage in
    exactly the market-neutral state it was built to hold. For a long-only
    genome the two are identical, so nothing changes underneath the original
    behaviour.
    """
    params = genome.params
    pos = signed_positions(genome.template, params, closes)

    gross_exposure = pos.abs().sum(axis=1)
    weights = pos.div(gross_exposure.replace(0, 1), axis=0)

    daily = closes.pct_change().fillna(0.0)
    target = float(params.get("vol_target", 0.0) or 0.0)
    if target > 0:
        weights = _vol_target(weights, daily, target,
                              lookback=int(params.get("vol_lookback", 60)))

    gross = (weights.shift(1).fillna(0.0) * daily).sum(axis=1)
    turnover = (weights - weights.shift(1)).abs().sum(axis=1).fillna(0.0)
    return gross - turnover * COST_PER_SIDE


def _vol_target(weights, daily, target: float, lookback: int = 60,
                periods: int = 252):
    """Scale the book so its realised volatility tracks `target`.

    The sizing decision at time t uses volatility measured THROUGH t, and the
    resulting weights first earn a return at t+1 (net_returns shifts weights
    by one). That ordering is the whole ballgame: sizing today from today's
    realised vol — computed with today's return in it — is a lookahead that
    makes every backtest beautiful and every live account poor.

    Leverage is capped at MAX_LEVERAGE. In the calmest stretch of any sample
    realised vol approaches zero and the uncapped ratio approaches infinity,
    and a search WILL find that corner if it is left open.
    """
    import numpy as np

    from .genome import MAX_LEVERAGE

    unlevered = (weights.shift(1).fillna(0.0) * daily).sum(axis=1)
    realised = unlevered.rolling(lookback).std() * np.sqrt(periods)

    leverage = (target / realised.replace(0.0, np.nan)).clip(upper=MAX_LEVERAGE)
    # Before enough history exists to measure vol there is no basis for a
    # size, and 1.0 would be a guess dressed as a default.
    leverage = leverage.fillna(0.0)
    return weights.mul(leverage, axis=0)


# Annualised risk-free rate charged against every strategy. A single constant
# across 2000-2026 and six currencies is a simplification — real short rates
# ranged from zero to five percent and Japan's sat near zero for the whole
# period — but the alternative that was in place, charging NOTHING, is not
# neutral. It is an assumption that cash is free, and it is the assumption a
# search will exploit.
RISK_FREE_ANNUAL = 0.02


def sharpe_of(rets, periods: int = 252, risk_free: float | None = None) -> float | None:
    """Annualised EXCESS-return Sharpe, or None when there is not enough to say.

    Excess, not raw. The distinction sounds academic and is not: with raw
    returns a book can scale itself down until it is almost entirely cash and
    keep its ratio, because Sharpe is scale-invariant. Drawdown is not
    scale-invariant, so scaling down also slips under any drawdown ceiling.
    Measured on the European panel, the search found exactly that corner — a
    genome holding 13.7% average exposure, earning 1.39% a year, posting a
    2.8% drawdown and a Sharpe that beat the fully-invested version.

    Subtracting a risk-free rate closes it. A book earning 1.39% while sitting
    in cash now scores below zero, which is what it deserves: you could have
    had the cash without the trading.

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

    rate = RISK_FREE_ANNUAL if risk_free is None else risk_free
    excess = float(rets.mean()) - rate / periods
    value = excess / sd * np.sqrt(periods)
    return float(value) if np.isfinite(value) else None


def max_drawdown(rets) -> float:
    """Worst peak-to-trough of the equity curve, as a positive fraction."""
    if rets is None or len(rets) < 2:
        return 0.0
    equity = (1 + rets).cumprod()
    return float(-(equity / equity.cummax() - 1).min())


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
