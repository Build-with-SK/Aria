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


# ── the rest of the world ────────────────────────────────────────────────
#
# Each index page needs its own ticker treatment. Wikipedia lists FTSE and SMI
# constituents bare, Hong Kong as "SEHK: 5", Singapore as "SGX: A17U", and the
# Nikkei not in a table at all — its components appear only as page links. The
# normalisers below are per-index because there is no general rule; guessing
# one produces a panel full of symbols that silently return no data.

TICKER_HINTS = ("ticker", "symbol", "code", "epic", "ric")

# yfinance exchange suffixes seen across these indices. Used to tell a real
# suffix from a share class — see _clean_ticker.
EXCHANGE_SUFFIXES = {
    "L", "PA", "DE", "F", "AS", "BR", "MC", "MI", "SW", "ST", "HE", "CO",
    "OL", "VI", "LS", "IR", "WA", "PR", "T", "HK", "AX", "NZ", "SI", "KS",
    "KQ", "TW", "TO", "V", "SA", "MX", "NS", "BO",
}

WORLD_INDICES = {
    # name:        (wikipedia page,                                   suffix, region)
    "ftse100":   ("https://en.wikipedia.org/wiki/FTSE_100_Index",     ".L",  "europe"),
    "cac40":     ("https://en.wikipedia.org/wiki/CAC_40",             "",    "europe"),
    "dax":       ("https://en.wikipedia.org/wiki/DAX",                "",    "europe"),
    "aex":       ("https://en.wikipedia.org/wiki/AEX_index",          "",    "europe"),
    "ibex35":    ("https://en.wikipedia.org/wiki/IBEX_35",            "",    "europe"),
    "ftsemib":   ("https://en.wikipedia.org/wiki/FTSE_MIB",           "",    "europe"),
    "smi":       ("https://en.wikipedia.org/wiki/Swiss_Market_Index", ".SW", "europe"),
    "omxs30":    ("https://en.wikipedia.org/wiki/OMX_Stockholm_30",   "",    "europe"),
    "hangseng":  ("https://en.wikipedia.org/wiki/Hang_Seng_Index",    ".HK", "asiapac"),
    "asx200":    ("https://en.wikipedia.org/wiki/S%26P/ASX_200",      ".AX", "asiapac"),
    "sti":       ("https://en.wikipedia.org/wiki/Straits_Times_Index", ".SI", "asiapac"),
    "tsx60":     ("https://en.wikipedia.org/wiki/S%26P/TSX_60",       ".TO", "americas"),
}


def _clean_ticker(raw: str, suffix: str) -> str | None:
    """Normalise one Wikipedia cell into a yfinance symbol."""
    import re

    value = str(raw).replace("\xa0", " ").strip()
    if not value or value.lower() == "nan":
        return None

    # "SEHK: 5" -> 0005.HK ; Hong Kong codes are zero-padded to four digits.
    if "sehk" in value.lower():
        digits = re.sub(r"\D", "", value)
        return f"{digits.zfill(4)}.HK" if digits else None
    # "SGX: A17U" -> A17U.SI
    if "sgx" in value.lower():
        code = value.split(":")[-1].strip()
        return f"{code}.SI" if code else None

    value = value.split(":")[-1].strip()

    # Only a REAL exchange suffix means "already qualified". Testing merely for
    # a short alphabetic tail treats the share class in "BT.A" as an exchange
    # and yields a symbol yfinance cannot resolve — which is how BT went
    # missing from the first European panel.
    if "." in value and value.rsplit(".", 1)[1].upper() in EXCHANGE_SUFFIXES:
        return value
    # Share classes are hyphens to yfinance: BT.A -> BT-A.L, BRK.B -> BRK-B.
    value = value.replace(".", "-")
    return f"{value}{suffix}" if suffix else value


def index_symbols(name: str) -> list[str]:
    import re

    import pandas as pd

    from src.research.http import get

    url, suffix, _region = WORLD_INDICES[name]
    html = get(url).decode("utf-8", "replace")

    tables = pd.read_html(io.StringIO(html))
    for table in tables:
        cols = [str(c).lower() for c in table.columns]
        hits = [c for c in cols if any(h in c for h in TICKER_HINTS)]
        if hits and len(table) >= 15:
            column = table.columns[cols.index(hits[0])]
            out = [_clean_ticker(v, suffix) for v in table[column]]
            return [s for s in out if s]

    raise SystemExit(f"no constituent table found for {name}")


def nikkei_symbols() -> list[str]:
    """The Nikkei page has no constituent table — codes appear only as links."""
    import re

    from src.research.http import get

    html = get("https://en.wikipedia.org/wiki/Nikkei_225").decode("utf-8", "replace")
    codes = sorted(set(re.findall(r">(\d{4})</a>", html)))
    if len(codes) < 100:
        raise SystemExit(f"only {len(codes)} Nikkei codes found — page layout changed")
    return [f"{c}.T" for c in codes]


def region_symbols(region: str) -> list[str]:
    if region == "japan":
        return nikkei_symbols()
    names = [n for n, (_u, _s, r) in WORLD_INDICES.items() if r == region]
    if not names:
        raise SystemExit(f"unknown region {region!r}")
    symbols: list[str] = []
    for name in names:
        try:
            found = index_symbols(name)
            symbols += found
            print(f"  {name}: {len(found)} symbols", flush=True)
        except Exception as exc:                      # noqa: BLE001
            print(f"  {name}: FAILED ({exc}) — continuing", flush=True)
    return sorted(set(symbols))


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
    elif name in ("europe", "japan", "asiapac", "americas"):
        symbols, start, out = region_symbols(name), "2000-01-01", f"{name}_closes.pkl"
    else:
        raise SystemExit(
            f"unknown universe {name!r}; try sp500, nse, europe, japan, "
            "asiapac or americas"
        )

    print(f"fetching {len(symbols)} symbols since {start}", flush=True)
    panel = fetch(symbols, start)
    if name == "nse":
        panel.columns = [c.replace(".NS", "") for c in panel.columns]
    panel.to_pickle(CACHE / out)
    print(f"saved {panel.shape[0]} days x {panel.shape[1]} names -> {CACHE / out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
