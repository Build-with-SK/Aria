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

# How every series this layer served was actually obtained. Written on every
# load, read by provenance() — see the PROVENANCE section below.
_PROVENANCE: dict[str, dict] = {}

# A daily series whose newest bar is older than this is stale. Five days clears
# a Friday close read on a Monday, and a long weekend, without clearing a feed
# that has genuinely stopped updating.
STALE_AFTER_DAYS = 5

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

# ── point-in-time replay ─────────────────────────────────────────────────────
#
# Historical validation needs every module to see the world as it was on some
# past date, and there are 41 modules. Rewriting each one to accept an as-of
# date would be 41 chances to leak the future; putting the clock HERE, in the
# one function they all read data through, is one chance.
#
# Thread-local because the registry runs modules in a thread pool: a global
# would leak one evaluation's date into another's, which is the same class of
# bug the mechanism exists to prevent.
_AS_OF = threading.local()


@contextmanager
def as_of(when):
    """Inside this block, every series is truncated to `when`.

    A module run under it cannot see a bar it could not have seen on the day —
    which is what makes a walk-forward result mean anything.
    """
    previous = getattr(_AS_OF, "when", None)
    _AS_OF.when = pd.Timestamp(when) if when is not None else None
    try:
        yield
    finally:
        _AS_OF.when = previous


def current_as_of():
    return getattr(_AS_OF, "when", None)


def history(symbol: str, period: str = "2y", interval: str = "1d") -> Optional[pd.DataFrame]:
    """Daily (or `interval`) OHLCV for one symbol, or None.

    Columns are normalised to Open/High/Low/Close/Volume with a tz-naive
    DatetimeIndex. Slices out of one long series per symbol.
    """
    full = _full_history(symbol, interval)
    if full is None or full.empty:
        return None
    cutoff_date = current_as_of()
    if cutoff_date is not None:
        full = full[full.index <= cutoff_date]
        if full.empty:
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

        prov = {"symbol": symbol, "interval": interval}
        df = _from_raw(symbol) if interval == "1d" else None
        if df is not None:
            prov.update(served_from="raw_csv", vendor=None, degraded=False,
                        detail=f"data/raw/{_safe(symbol)}.csv")
        if df is None:
            df = _from_cache(symbol, interval)
            if df is not None:
                prov.update(served_from="disk_cache", vendor=None, degraded=False,
                            detail=f"cache within {CACHE_TTL_HOURS}h TTL")
        if df is None:
            df, prov = _from_vendors(symbol, interval, prov)

        df = _normalise(df) if df is not None else None
        with _LOCK:
            if df is None or len(df) < 5:
                _MISSES[key] = datetime.now()
                _PROVENANCE[key] = {**prov, "ok": False,
                                    "stale": True, "last_bar": None,
                                    "last_bar_age_days": None,
                                    "at": datetime.now().isoformat(timespec="seconds")}
                return None
            _PROVENANCE[key] = _finish_provenance(prov, df)
            _MEM[key] = (datetime.now(), df)
        return df


# ── PROVENANCE ───────────────────────────────────────────────────────────────
#
# A number is not a fact without its source and its date. This section answers,
# for any series this layer has served: which vendor produced it, when it was
# fetched, how old the newest bar is, and whether that makes it stale.
#
# It is deliberately a side channel rather than a change to history()'s return
# type — 41 modules call these functions, and a signature change would be a
# migration where what is needed is a fact they can ask for.


def _finish_provenance(prov: dict, df: pd.DataFrame) -> dict:
    """Add the facts that can only be known once the data is in hand."""
    last_bar = None
    age_days = None
    try:
        last = df.index.max()
        if last is not None and not pd.isna(last):
            last_bar = pd.Timestamp(last).to_pydatetime()
            age_days = round((datetime.now() - last_bar.replace(tzinfo=None))
                             .total_seconds() / 86400, 2)
    except Exception:
        pass

    too_old = age_days is not None and age_days > STALE_AFTER_DAYS
    stale = bool(prov.get("degraded")) or too_old
    reasons = []
    if prov.get("degraded"):
        reasons.append(prov.get("detail") or "degraded source")
    if too_old:
        reasons.append(f"newest bar is {age_days:.1f} days old "
                       f"(stale after {STALE_AFTER_DAYS})")

    return {**prov,
            "ok": True,
            "last_bar": last_bar.date().isoformat() if last_bar else None,
            "last_bar_age_days": age_days,
            "stale": stale,
            "stale_reason": "; ".join(reasons),
            "rows": int(len(df)),
            "at": datetime.now().isoformat(timespec="seconds")}


def provenance(symbol: str, interval: str = "1d") -> dict:
    """How the series for `symbol` was obtained, or {} if it was never loaded.

    Ask AFTER loading (history/closes/returns); this reports, it does not fetch.
    """
    with _LOCK:
        return dict(_PROVENANCE.get(f"{symbol}|{interval}") or {})


def is_stale(symbol: str, interval: str = "1d") -> bool:
    """True when the data behind `symbol` is degraded, old, or absent.

    Unknown counts as stale. A caller asking this question is deciding how much
    to trust a number, and "I have no idea where that came from" is not the
    answer that should read as fine.
    """
    p = provenance(symbol, interval)
    return True if not p else bool(p.get("stale", True))


def source_label(symbol: str) -> str:
    """The provenance string modules cite for price-derived claims.

    Carries staleness in the citation itself, so a stale number cannot be
    quoted as a fresh one anywhere the label is printed.
    """
    p = provenance(symbol)
    if p.get("ok"):
        served = p.get("served_from") or "unknown source"
        if served == "raw_csv":
            base = f"data/raw/{_safe(symbol)}.csv"
        elif served == "disk_cache":
            base = f"cached daily close ({symbol})"
        elif served == "stale_cache":
            base = f"CACHED daily close ({symbol})"
        elif served.startswith("vendor:"):
            vendor = served.split(":", 1)[1]
            pretty = {"yfinance": "Yahoo Finance",
                      "alphavantage": "Alpha Vantage",
                      "stooq": "Stooq"}.get(vendor, vendor)
            base = f"{pretty} ({symbol}, daily close)"
        else:
            base = f"{served} ({symbol})"
        if p.get("stale"):
            return (f"{base} — STALE as of {p.get('last_bar') or 'unknown date'}"
                    f" ({p.get('stale_reason') or 'degraded source'})")
        if p.get("last_bar"):
            return f"{base}, to {p['last_bar']}"
        return base

    q = RAW_DIR / f"{_safe(symbol)}.csv"
    if q.exists() and _age_days(q) <= RAW_MAX_AGE_DAYS:
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


def _from_vendors(symbol: str, interval: str,
                  prov: dict) -> tuple[Optional[pd.DataFrame], dict]:
    """Live fetch through the vendor chain, then the stale cache as last resort.

    The stale cache is still here — old data beats no data for a research
    system that cites the dates it used — but it is no longer indistinguishable
    from a fresh fetch. It comes back labelled `stale_cache`, and everything
    downstream, up to and including the confidence number on the report, is
    told.
    """
    from src.v5 import vendors

    with _quiet_vendor():
        yahoo = resolve(symbol)

    df, report = vendors.fetch(yahoo, interval, FULL_PERIOD)
    if df is not None:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            out = df.copy()
            if getattr(out.index, "tz", None) is not None:
                out.index = out.index.tz_localize(None)
            out.to_csv(_cache_path(symbol, interval))
        except Exception as e:
            logger.debug(f"v5 cache write failed for {symbol}: {e}")
        return df, {**prov,
                    "served_from": f"vendor:{report['vendor']}",
                    "vendor": report["vendor"],
                    # A fallback vendor is degraded even when its data is
                    # perfectly fresh: the primary is down and somebody should
                    # know before it matters.
                    "degraded": not report["is_primary"],
                    "detail": ("primary vendor" if report["is_primary"] else
                               f"FALLBACK vendor — {vendors.PRIMARY} failed"),
                    "vendor_attempts": report["attempts"]}

    p = _cache_path(symbol, interval)
    if p.exists():
        try:
            age = _age_days(p)
            logger.warning(f"v5 using STALE cache for {symbol} "
                           f"({age:.1f} days old) — every vendor failed")
            return pd.read_csv(p, index_col=0, parse_dates=True), {
                **prov,
                "served_from": "stale_cache",
                "vendor": None,
                "degraded": True,
                "detail": f"every vendor failed; cache is {age:.1f} days old",
                "vendor_attempts": report["attempts"]}
        except Exception:
            pass
    return None, {**prov, "served_from": None, "vendor": None, "degraded": True,
                  "detail": "no vendor and no cache",
                  "vendor_attempts": report["attempts"]}


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
