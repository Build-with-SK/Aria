"""
src/evolution/universe.py
=========================
Price panels for the lab, and the honest label that has to travel with them.

READ THIS BEFORE BELIEVING ANY RESULT FROM A LONG BACKTEST HERE.

Both universes are built from TODAY's index membership run backwards, which
means both are survivorship-biased. The S&P 500 of 2000 contained Lehman
Brothers, Enron, Washington Mutual, General Motors and Kodak; today's list
does not. Reconstructing history from the current members quietly deletes
every company that failed, and a long-only strategy backtested on the
survivors of a 25-year selection process will look better than it was — the
universe already knows which names to hold.

Fixing this properly needs point-in-time constituents, which are a paid
dataset. Until one exists here, every campaign run on these panels carries
the caveat, and the module reports it in `describe()` so a result cannot be
quoted without it.

What survivorship bias does NOT explain away: a strategy failing. The bias
inflates results, so a candidate that cannot clear its bar on a flattered
universe would do worse on the real one, and that conclusion is safe.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
CACHE = ROOT / "data" / "evolution"

PANELS = {
    "nse": {
        "file": "prices_long.pkl",
        "market": "India — NSE large caps",
        "note": "20 NIFTY large caps, current membership run back to 2005",
    },
    "sp500": {
        "file": "sp500_closes.pkl",
        "market": "United States — S&P 500",
        "note": "current S&P 500 membership run back to 2000",
    },
}


def load(name: str = "sp500", min_history: float = 0.98):
    """Load a cached panel, keeping names with near-complete history.

    Columns are dropped rather than forward-filled from nothing: padding a
    stock backwards through years when it had not listed invents a price
    series, and a strategy will happily trade the invention.
    """
    import pandas as pd

    if name not in PANELS:
        raise ValueError(f"unknown universe {name!r}; try one of {sorted(PANELS)}")
    path = CACHE / PANELS[name]["file"]
    if not path.exists():
        raise FileNotFoundError(
            f"no cached panel at {path} — fetch it first (see scripts/fetch_universe.py)"
        )

    raw = pd.read_pickle(path)
    keep = raw.columns[raw.notna().sum() >= len(raw) * min_history]
    panel = raw[keep].ffill().dropna(how="any")
    logger.info("universe %s: %d days x %d names (%d dropped for short history)",
                name, panel.shape[0], panel.shape[1], raw.shape[1] - len(keep))
    return panel


def describe(name: str, panel=None) -> dict:
    """Everything a reader needs to judge a campaign run on this panel."""
    import numpy as np

    meta = dict(PANELS.get(name, {}))
    meta["universe"] = name
    meta["survivorship_biased"] = True
    meta["caveat"] = (
        "Built from today's index membership run backwards, so companies that "
        "failed or were delisted are absent. Long-only results are flattered; "
        "a FAILURE to clear the bar remains trustworthy."
    )
    if panel is None:
        return meta

    equal_weight = (panel.pct_change().fillna(0.0).mean(axis=1) + 1).cumprod()
    drawdown = equal_weight / equal_weight.cummax() - 1
    meta.update({
        "days": int(panel.shape[0]),
        "names": int(panel.shape[1]),
        "start": str(panel.index.min().date()),
        "end": str(panel.index.max().date()),
        "max_drawdown_pct": round(float(drawdown.min()) * 100, 1),
        "max_drawdown_on": str(drawdown.idxmin().date()),
        "years_below_20pct": sorted({int(y) for y in
                                     drawdown[drawdown < -0.20].index.year}),
    })
    return meta
