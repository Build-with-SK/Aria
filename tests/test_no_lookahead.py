"""
tests/test_no_lookahead.py
==========================
Adversarial look-ahead checks: change the FUTURE, and nothing computed for the
past may move.

Each test builds a price history, computes something, then rewrites every bar
after a cutoff (a crash, a moonshot) and recomputes. Any value at or before
the cutoff that changes was computed with information from after it. This is
the only kind of leakage test that does not depend on reading the code
correctly, which is why it is written this way rather than as assertions about
shift() calls.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

N, CUT = 400, 300


def _ohlcv(seed=0, n=N, cols=None):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, n)))
    df = pd.DataFrame({"Open": close * (1 + rng.normal(0, 0.002, n)),
                       "High": close * 1.01, "Low": close * 0.99,
                       "Close": close, "Volume": rng.integers(1e5, 1e6, n).astype(float)},
                      index=idx)
    return df


def _rewrite_future(df, cut=CUT, factor=0.5):
    """Crash everything after the cutoff — a future that never happened."""
    out = df.copy()
    for c in ("Open", "High", "Low", "Close"):
        if c in out.columns:
            out.iloc[cut + 1:, out.columns.get_loc(c)] *= factor
    return out


def _closes_panel(seed=0, cols=6):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=N)
    data = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, (N, cols)), axis=0))
    return pd.DataFrame(data, index=idx, columns=[f"S{i}" for i in range(cols)])


# ── features ────────────────────────────────────────────────────────────────

def test_indicator_features_do_not_see_the_future():
    from src.features import indicators as ind
    base = _ohlcv()
    cfg = {}

    def feats(df):
        out = df.copy()
        out = ind.add_moving_averages(out, cfg) if cfg is not None else out
        out = ind.add_rsi(out); out = ind.add_macd(out)
        out = ind.add_bollinger_bands(out); out = ind.add_atr(out)
        out = ind.add_momentum(out); out = ind.add_regime_flags(out)
        return out

    a, b = feats(base), feats(_rewrite_future(base))
    cols = [c for c in a.columns if c not in ("Open", "High", "Low", "Close", "Volume")]
    pd.testing.assert_frame_equal(a.loc[:a.index[CUT], cols], b.loc[:b.index[CUT], cols])


def test_training_labels_are_the_only_forward_looking_columns():
    """The label is SUPPOSED to see the future — that is what a label is. It
    must see exactly `horizon` bars and no more."""
    from src.models.targets import future_return
    base = _ohlcv()
    a = future_return(base["Close"], 5)
    b = future_return(_rewrite_future(base)["Close"], 5)
    pd.testing.assert_series_equal(a.iloc[:CUT - 5 + 1], b.iloc[:CUT - 5 + 1])
    assert not np.isclose(a.iloc[CUT - 4], b.iloc[CUT - 4])     # sees bar CUT+1


# ── backtests ───────────────────────────────────────────────────────────────

def test_signal_backtester_decisions_use_only_the_past():
    from src.backtesting import backtester as bt
    from src.features import indicators as ind

    def run(df):
        f = ind.add_regime_flags(ind.add_macd(ind.add_rsi(
            ind.add_moving_averages(df.copy(), {}))))
        return bt.run_backtest("X", f, signal_threshold=5.0)

    base = _ohlcv(seed=3)
    a, b = run(base), run(_rewrite_future(base, factor=3.0))
    # Equity is marked on the close, so the bar AFTER the cutoff may differ.
    pd.testing.assert_series_equal(a.equity_curve.iloc[:CUT - 1],
                                   b.equity_curve.iloc[:CUT - 1])
    entered_before = [t for t in a.trades if t.entry_date <= str(base.index[CUT - 1].date())
                      and t.exit_date <= str(base.index[CUT - 1].date())]
    assert entered_before == [t for t in b.trades if t in entered_before]


@pytest.mark.parametrize("template", ["momentum_topn", "ma_cross", "mean_reversion_z",
                                      "rsi_reversal", "breakout"])
def test_quant_lab_positions_do_not_see_the_future(template):
    from src.brain import quant_lab as ql
    params = ql.TEMPLATES[template]
    base = _closes_panel()
    future = base.copy()
    future.iloc[CUT + 1:] *= 0.4
    a = ql._positions(template, params, base)
    b = ql._positions(template, params, future)
    pd.testing.assert_frame_equal(a.iloc[:CUT + 1], b.iloc[:CUT + 1])


@pytest.mark.parametrize("template", ["momentum_topn", "ma_cross", "mean_reversion_z",
                                      "rsi_reversal", "breakout"])
def test_evolution_positions_do_not_see_the_future(template):
    from src.brain import quant_lab as ql
    from src.evolution.backtest import fast_positions
    params = ql.TEMPLATES[template]
    base = _closes_panel(seed=5)
    future = base.copy()
    future.iloc[CUT + 1:] *= 2.5
    a = fast_positions(template, params, base)
    b = fast_positions(template, params, future)
    pd.testing.assert_frame_equal(a.iloc[:CUT + 1], b.iloc[:CUT + 1])


# ── validation splits ───────────────────────────────────────────────────────

@pytest.mark.parametrize("horizon", [1, 5, 20])
def test_walk_forward_training_labels_never_reach_the_test_window(horizon):
    from src.models.walk_forward import PurgedWalkForwardCV
    X = pd.DataFrame({"f": np.arange(1000)},
                     index=pd.bdate_range("2020-01-01", periods=1000))
    folds = list(PurgedWalkForwardCV(n_splits=5).split(X, horizon=horizon))
    assert folds, "no folds produced"
    for train, test in folds:
        # a training row i is labelled with close[i + horizon]
        assert train.max() + horizon < test.min()


# ── point-in-time replay ────────────────────────────────────────────────────

def test_market_data_as_of_hides_later_bars(monkeypatch):
    from src.v5 import marketdata as md
    full = _ohlcv()
    monkeypatch.setattr(md, "_full_history", lambda symbol, interval: full)
    cutoff = full.index[CUT]
    with md.as_of(cutoff):
        seen = md.history("X", period="10y")
    assert seen.index.max() == cutoff
    assert md.history("X", period="10y").index.max() == full.index[-1]
