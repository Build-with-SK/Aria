"""
scripts/fetch_universe.py
=========================
Download a price panel for the evolution lab and cache it.

    venv\\Scripts\\python.exe scripts\\fetch_universe.py sp500
    venv\\Scripts\\python.exe scripts\\fetch_universe.py nse

Downloads in batches because asking yfinance for five hundred tickers at once
gets the whole request throttled, and a partial panel that silently drops
names is worse than a slow one. Constituents come from Wikipedia through
src.research.http, which sends a real User-Agent — pandas.read_html on its own
is answered with 403.

These panels are survivorship-biased by construction; see
src/evolution/universe.py for what that does and does not invalidate.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

CACHE = ROOT / "data" / "evolution"
BATCH = 60


def sp500_symbols() -> list[str]:
    import pandas as pd

    from src.research.http import get

    html = get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    table = pd.read_html(io.StringIO(html.decode("utf-8", "replace")))[0]
    # BRK.B and BF.B are BRK-B / BF-B to yfinance.
    return [s.replace(".", "-") for s in table["Symbol"].astype(str)]


def nse_symbols() -> list[str]:
    from src.brain.quant_lab import BASKET
    return [f"{s}.NS" for s in BASKET]


def fetch(symbols: list[str], start: str):
    import pandas as pd
    import yfinance as yf

    frames = []
    for i in range(0, len(symbols), BATCH):
        chunk = symbols[i:i + BATCH]
        raw = yf.download(chunk, start=start, interval="1d", progress=False,
                          threads=True, group_by="ticker", auto_adjust=True)
        cols = {}
        for symbol in chunk:
            try:
                if symbol in raw.columns.get_level_values(0):
                    cols[symbol] = raw[symbol]["Close"]
            except (KeyError, AttributeError):
                continue
        if cols:
            frames.append(pd.DataFrame(cols))
        print(f"  batch {i // BATCH + 1}: {len(cols)}/{len(chunk)}", flush=True)

    if not frames:
        raise SystemExit("no data returned — check the network and try again")
    panel = pd.concat(frames, axis=1).sort_index().dropna(axis=1, how="all")
    return panel


def main() -> int:
    name = (sys.argv[1] if len(sys.argv) > 1 else "sp500").lower()
    CACHE.mkdir(parents=True, exist_ok=True)

    if name == "sp500":
        symbols, start, out = sp500_symbols(), "2000-01-01", "sp500_closes.pkl"
    elif name == "nse":
        symbols, start, out = nse_symbols(), "2005-01-01", "prices_long.pkl"
    else:
        raise SystemExit(f"unknown universe {name!r}; try sp500 or nse")

    print(f"fetching {len(symbols)} symbols since {start}", flush=True)
    panel = fetch(symbols, start)
    if name == "nse":
        panel.columns = [c.replace(".NS", "") for c in panel.columns]
    panel.to_pickle(CACHE / out)
    print(f"saved {panel.shape[0]} days x {panel.shape[1]} names -> {CACHE / out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
