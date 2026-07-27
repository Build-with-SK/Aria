"""Technical summary — pure scoring/tally/consensus (no network)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.technical_summary import (label_from_counts, tally, _consensus,
                                        score_frame)


def test_label_buckets():
    assert label_from_counts(9, 0) == "Strong Buy"
    assert label_from_counts(6, 4) == "Buy"          # 0.6
    assert label_from_counts(5, 5) == "Neutral"      # 0.5
    assert label_from_counts(4, 6) == "Sell"         # 0.4
    assert label_from_counts(0, 9) == "Strong Sell"
    assert label_from_counts(0, 0) == "Neutral"


def test_tally_counts_and_label():
    sig = {"a": "buy", "b": "buy", "c": "sell", "d": "neutral"}
    t = tally(sig)
    assert t == {"buy": 2, "sell": 1, "neutral": 1, "label": "Buy"}


def test_consensus_across_timeframes():
    assert _consensus(["Strong Buy", "Buy", "Buy"]) in ("Buy", "Strong Buy")
    assert _consensus(["Strong Sell", "Sell"]) in ("Sell", "Strong Sell")
    assert _consensus(["Strong Buy", "Strong Sell"]) == "Neutral"
    assert _consensus([]) == "Neutral"


def test_score_frame_needs_data():
    import pandas as pd
    assert score_frame(pd.DataFrame({"Close": [1, 2, 3]})) is None
    assert score_frame(None) is None


def test_score_frame_on_synthetic_uptrend():
    import numpy as np, pandas as pd
    n = 260
    close = pd.Series(np.linspace(100, 200, n))     # steady uptrend
    df = pd.DataFrame({"Open": close, "High": close * 1.01,
                       "Low": close * 0.99, "Close": close})
    s = score_frame(df)
    assert s is not None
    # price above every MA in a clean uptrend → MA side strongly bullish
    assert s["moving_averages"]["label"] in ("Strong Buy", "Buy")
    assert s["summary"] in ("Strong Buy", "Buy", "Neutral")
