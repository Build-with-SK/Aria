"""
src/v5/marketdata.py
====================
The only data door the V5 research modules use.

Every number a module cites must be traceable to a source string, so this layer
returns data *and* the provenance label to cite. Order of preference:

    1. data/raw/<TICKER>.csv        — the pipeline's own downloaded history
    2. data/cache/v5/               — V5's own on-disk cache (TTL'd)
    3. yfinance                      — live fetch, then cached

Failures return None rather than raising: a module that cannot get data must
abstain (contract.insufficient), never guess.
"""
from __future__ import annotations

import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


@contextmanager
def _quiet_vendor():
    """Silence yfinance's own console chatter for the duration of one fetch.

    yfinance prints "possibly delisted" and raw 404 bodies for every symbol it
    cannot resolve. This layer already converts each of those into a stated
    result — an abstention with a reason, or a clean unknown-instrument error —
    so the vendor's version is duplicate noise that reads like a crash. Scoped
    to this function only: anything else in the app using yfinance keeps its
    normal logging.
    """
    names = ("yfinance", "yfinance.data", "yfinance.utils", "peewee")
    saved = {}
    for n in names:
        lg = logging.getLogger(n)
        saved[n] = (lg.level, lg.disabled)
        lg.setLevel(logging.CRITICAL)
        lg.disabled = True
    try:
        yield
    finally:
        for n, (level, disabled) in saved.items():
            lg = logging.getLogger(n)
            lg.setLevel(level)
            lg.disabled = disabled

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"
RAW_DIR = DATA / "raw"
CACHE_DIR = DATA / "cache" / "v5"

CACHE_TTL_HOURS = 6
RAW_MAX_AGE_DAYS = 3          # data/raw is only trusted while it is fresh
FULL_PERIOD = "10y"           # one fetch per symbol; every module slices from it

# Modules ask for 3mo … 10y of the same instrument. Fetching each of those
# separately is what gets a client rate-limited, so the loader fetches the long
# series ONCE per symbol and slices it. `_FETCH_LOCKS` makes eight parallel
# modules asking for the same symbol produce one network call, not eight.
PERIOD_DAYS = {"1mo": 31, "3mo": 93, "6mo": 186, "1y": 366, "2y": 731,
               "3y": 1096, "5y": 1827, "10y": 3653}

_MEM: dict[str, tuple[datetime, pd.DataFrame]] = {}
_LOCK = threading.Lock()
_FETCH_LOCKS: dict[str, threading.Lock] = {}
_MISSES: dict[str, datetime] = {}       # symbols that just failed, to avoid hammering

# Benchmarks the cross-sectional / macro modules lean on.
BENCH = "SPY"
SECTOR_ETFS = {
    "Technology": "XLK", "Financial Services": "XLF", "Healthcare": "XLV",
    "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP", "Energy": "XLE",
    "Industrials": "XLI", "Basic Materials": "XLB", "Utilities": "XLU",
    "Real Estate": "XLRE", "Communication Services": "XLC",
}
CROSS_ASSET = {
    "equities": "SPY", "bonds": "TLT", "gold": "GC=F", "oil": "CL=F",
    "dollar": "DX-Y.NYB", "credit": "HYG", "vix": "^VIX", "bitcoin": "BTC-USD",
    "smallcap": "IWM", "nasdaq": "QQQ",
}


# ── symbol resolution ────────────────────────────────────────────────────────

def resolve(symbol: str) -> str:
    """Map a loose symbol to its Yahoo ticker via the universe index
    (SAIL → SAIL.NS). Falls back to the symbol unchanged."""
    try:
        from src.data.universe import get_universe
        info = get_universe().resolve(symbol)
        if info and info.get("yahoo"):
            return info["yahoo"]
    except Exception as e:
        logger.debug(f"v5 resolve({symbol}) fell back: {e}")
    return symbol


def _safe(name: str) -> str:
    return name.replace("=", "_").replace("^", "_").replace("/", "_").replace(":", "_")


# ── history ──────────────────────────────────────────────────────────────────

def history(symbol: str, period: str = "2y", interval: str = "1d") -> Optional[pd.DataFrame]:
    """Daily (or `interval`) OHLCV for one symbol, or None.

    Columns are normalised to Open/High/Low/Close/Volume with a tz-naive
    DatetimeIndex. Slices out of one long series per symbol.
    """
    full = _full_history(symbol, interval)
    if full is None or full.empty:
        return None
    days = PERIOD_DAYS.get(period)
    if days:
        cutoff = full.index.max() - pd.Timedelta(days=days)
        full = full[full.index >= cutoff]
    return full if len(full) >= 5 else None


def _full_history(symbol: str, interval: str) -> Optional[pd.DataFrame]:
    key = f"{symbol}|{interval}"
    with _LOCK:
        hit = _MEM.get(key)
        if hit and datetime.now() - hit[0] < timedelta(minutes=30):
            return hit[1]
        miss = _MISSES.get(key)
        if miss and datetime.now() - miss < timedelta(minutes=10):
            return None                     # recently unavailable; do not hammer
        lock = _FETCH_LOCKS.setdefault(key, threading.Lock())

    with lock:
        with _LOCK:                          # another thread may have filled it
            hit = _MEM.get(key)
            if hit and datetime.now() - hit[0] < timedelta(minutes=30):
                return hit[1]

        df = _from_raw(symbol) if interval == "1d" else None
        if df is None:
            df = _from_cache(symbol, interval)
        if df is None:
            df = _from_yfinance(symbol, interval)

        df = _normalise(df) if df is not None else None
        with _LOCK:
            if df is None or len(df) < 5:
                _MISSES[key] = datetime.now()
                return None
            _MEM[key] = (datetime.now(), df)
        return df


def source_label(symbol: str) -> str:
    """The provenance string modules cite for price-derived claims."""
    p = RAW_DIR / f"{_safe(symbol)}.csv"
    if p.exists() and _age_days(p) <= RAW_MAX_AGE_DAYS:
        return f"data/raw/{_safe(symbol)}.csv"
    return f"Yahoo Finance ({symbol}, daily close)"


def _age_days(p: Path) -> float:
    return (datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)).total_seconds() / 86400


def _from_raw(symbol: str) -> Optional[pd.DataFrame]:
    p = RAW_DIR / f"{_safe(symbol)}.csv"
    if not p.exists() or _age_days(p) > RAW_MAX_AGE_DAYS:
        return None
    try:
        return pd.read_csv(p, index_col=0, parse_dates=True)
    except Exception as e:
        logger.debug(f"v5 raw read failed for {symbol}: {e}")
        return None


def _cache_path(symbol: str, interval: str) -> Path:
    return CACHE_DIR / f"{_safe(symbol)}__{interval}.csv"


def _from_cache(symbol: str, interval: str) -> Optional[pd.DataFrame]:
    p = _cache_path(symbol, interval)
    if not p.exists() or _age_days(p) * 24 > CACHE_TTL_HOURS:
        return None
    try:
        return pd.read_csv(p, index_col=0, parse_dates=True)
    except Exception:
        return None


def _from_yfinance(symbol: str, interval: str) -> Optional[pd.DataFrame]:
    import time
    with _quiet_vendor():
        yahoo = resolve(symbol)
    for attempt in range(2):
        try:
            import yfinance as yf
            with _quiet_vendor():
                df = yf.Ticker(yahoo).history(period=FULL_PERIOD, interval=interval,
                                              auto_adjust=True)
            if df is not None and not df.empty:
                try:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    out = df.copy()
                    if getattr(out.index, "tz", None) is not None:
                        out.index = out.index.tz_localize(None)
                    out.to_csv(_cache_path(symbol, interval))
                except Exception as e:
                    logger.debug(f"v5 cache write failed for {symbol}: {e}")
                return df
        except Exception as e:
            logger.debug(f"v5 yfinance fetch failed for {symbol} "
                         f"(attempt {attempt + 1}): {e}")
        if attempt == 0:
            time.sleep(0.6)
    # Fall back to a stale cache rather than abstaining — the modules cite the
    # dates they used, so old data is usable as long as it is not passed off
    # as current.
    p = _cache_path(symbol, interval)
    if p.exists():
        try:
            logger.info(f"v5 using stale cache for {symbol} "
                        f"({_age_days(p):.1f} days old) — live fetch failed")
            return pd.read_csv(p, index_col=0, parse_dates=True)
        except Exception:
            pass
    return None


def _normalise(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    try:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        df = df.rename(columns={c: str(c).title() for c in df.columns})
        if "Close" not in df.columns:
            if "Adj Close" in df.columns:
                df["Close"] = df["Adj Close"]
            else:
                return None
        # A CSV round-trip can produce an object index or a tz-aware one; both
        # must land as a tz-naive DatetimeIndex or every downstream align fails.
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, errors="coerce", utc=True)
        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_convert(None) if str(df.index.tz) == "UTC" \
                else df.index.tz_localize(None)
        df = df[df.index.notna()]
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
        df = df.dropna(subset=["Close"])
        return df
    except Exception as e:
        logger.debug(f"v5 normalise failed: {e}")
        return None


def closes(symbol: str, period: str = "2y") -> Optional[pd.Series]:
    df = history(symbol, period=period)
    return None if df is None else df["Close"].dropna()


def returns(symbol: str, period: str = "2y") -> Optional[pd.Series]:
    c = closes(symbol, period)
    return None if c is None or len(c) < 3 else c.pct_change().dropna()


def benchmark_returns(period: str = "2y") -> Optional[pd.Series]:
    return returns(BENCH, period)


# ── fundamentals / reference ────────────────────────────────────────────────

def dossier(symbol: str) -> dict:
    """Fundamentals + 5y weekly closes from the universe manager (cached on
    disk by that layer). {} on failure."""
    try:
        from src.data.universe import get_universe
        d = get_universe().dossier(symbol, prefetch_peers=False)
        return {} if not isinstance(d, dict) or "error" in d else d
    except Exception as e:
        logger.debug(f"v5 dossier({symbol}) failed: {e}")
        return {}


def fundamentals(symbol: str) -> dict:
    return dossier(symbol).get("fundamentals") or {}


def dividend_yield_pct(symbol: str) -> Optional[float]:
    """Annual dividend yield in PERCENT (2.5 means 2.5%), or None.

    yfinance has changed the units of `dividendYield` between versions — a 0.35%
    yield has been reported as both 0.0035 and 0.35 — and guessing between them
    is a 100× error in either direction. So the yield is derived from
    dividendRate/price wherever the payout rate is available, and the ambiguous
    field is only used when its value is unambiguous on its own (≥1, which can
    only be a percentage). Otherwise this returns None and the caller abstains.
    """
    d = dossier(symbol)
    f = d.get("fundamentals") or {}
    rate, price = f.get("dividendRate"), d.get("price")
    if isinstance(rate, (int, float)) and isinstance(price, (int, float)) and price > 0:
        return round(float(rate) / float(price) * 100, 4)
    dy = f.get("dividendYield")
    if isinstance(dy, (int, float)) and float(dy) >= 1.0:
        return round(float(dy), 4)          # ≥1 cannot be a fraction
    return None


def sector_of(symbol: str) -> Optional[str]:
    f = fundamentals(symbol)
    return f.get("sector")


def peers(symbol: str, n: int = 5) -> list:
    try:
        from src.data.universe import get_universe
        return get_universe().peers(symbol, n) or []
    except Exception:
        return []


# ── ARIA's own generated data (already sourced + timestamped) ───────────────

def data_json(filename: str) -> dict:
    p = DATA / filename
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug(f"v5 could not parse data/{filename}: {e}")
        return {}


def signal(ticker: str) -> dict:
    return data_json("signals.json").get(ticker) or {}


def macro() -> dict:
    return data_json("macro_data.json")


def sentiment(ticker: str) -> dict:
    return data_json("sentiment_data.json").get(ticker) or {}


def ml_prediction(ticker: str) -> dict:
    return data_json("ml_predictions.json").get(ticker) or {}


def options_data(ticker: str) -> dict:
    d = data_json("options_data.json")
    return (d.get(ticker) or {}) if isinstance(d, dict) else {}
