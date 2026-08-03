"""
src/data/currency.py
====================
Native currency resolution and FX rates.

Two facts make "just show everything in the user's currency" dangerous, and
both are handled here rather than in the UI:

  1. Prices in this system are NATIVE, not USD. signals.json stores whatever
     the vendor quoted — dollars for AAPL, rupees for RELIANCE.NS, pence for
     HSBA.L. Converting a rupee price as though it were dollars overstates it
     by ~88×. So nothing is converted until its native currency is known.

  2. The London Stock Exchange quotes most shares in PENCE (GBp), not pounds.
     Yahoo returns 812.4 for a stock trading at £8.12. Treating GBp as GBP is a
     100× error, and it is the single most common currency bug in retail
     finance tooling. `native_currency` returns GBp for LSE equities and
     `to_base` divides by 100 on the way out.

If the native currency cannot be established, the caller is told so and the
value is shown as-is. An unconverted honest number beats a converted wrong one.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
UNIVERSE_DB = ROOT / "data" / "universe.db"

# Currencies the UI can display in. Rates are quoted as units per 1 USD.
SUPPORTED = ["USD", "INR", "GBP", "EUR", "JPY", "AUD", "CAD", "CHF",
             "SGD", "HKD", "AED", "CNY", "ZAR", "BRL"]

RATE_TTL_MINUTES = 60

_rate_cache: dict[str, tuple[datetime, dict]] = {}
_lock = threading.Lock()
_symbol_ccy_cache: dict[str, Optional[str]] = {}

# Suffix → (currency, exchange label). Yahoo's suffix convention.
SUFFIX_CCY = {
    ".NS": "INR", ".BO": "INR",       # India — NSE / BSE
    ".L": "GBp",                       # London — quoted in PENCE
    ".DE": "EUR", ".PA": "EUR", ".AS": "EUR", ".MI": "EUR", ".MC": "EUR",
    ".BR": "EUR", ".LS": "EUR", ".HE": "EUR", ".VI": "EUR", ".IR": "EUR",
    ".SW": "CHF", ".ST": "SEK", ".OL": "NOK", ".CO": "DKK",
    ".T": "JPY", ".KS": "KRW", ".KQ": "KRW",
    ".HK": "HKD", ".SS": "CNY", ".SZ": "CNY", ".TW": "TWD",
    ".AX": "AUD", ".NZ": "NZD",
    ".TO": "CAD", ".V": "CAD",
    ".SA": "BRL", ".MX": "MXN", ".JO": "ZAR", ".SI": "SGD",
}

EXCHANGE_CCY = {
    "NSE": "INR", "BSE": "INR", "LSE": "GBp",
    "NASDAQ": "USD", "NYSE": "USD", "AMEX": "USD", "ARCA": "USD", "BATS": "USD",
}


def native_currency(symbol: str, exchange: Optional[str] = None) -> Optional[str]:
    """The currency a symbol's price is quoted in, or None when unknown.

    Returns "GBp" (pence) for London equities — that is a real, distinct
    quoting unit, not a typo for GBP.
    """
    if not symbol:
        return None
    key = f"{symbol}|{exchange or ''}"
    if key in _symbol_ccy_cache:
        return _symbol_ccy_cache[key]

    sym = symbol.strip()
    upper = sym.upper()
    ccy: Optional[str] = None

    # Crypto and FX pairs carry their quote currency in the ticker itself.
    if upper.endswith("-USD"):
        ccy = "USD"
    elif upper.endswith("=X") and len(upper) >= 8:
        ccy = upper[3:-2] or None            # USDINR=X → INR
    elif upper.endswith("=F"):
        ccy = "USD"                          # CME/COMEX futures
    elif upper.startswith("^"):
        ccy = None                           # index level, not a price

    if ccy is None:
        for suffix, c in SUFFIX_CCY.items():
            if upper.endswith(suffix.upper()):
                ccy = c
                break

    # The universe index knows the exchange and sometimes the currency.
    if ccy is None:
        row = _universe_row(sym)
        if row:
            db_ccy, db_exch = row
            if db_ccy:
                ccy = "GBp" if (db_ccy == "GBP" and (db_exch or "") == "LSE") else db_ccy
            elif db_exch:
                ccy = EXCHANGE_CCY.get(db_exch)

    if ccy is None and exchange:
        ccy = EXCHANGE_CCY.get(exchange)

    # A bare alphabetic ticker with no suffix is a US listing in this universe.
    if ccy is None and upper.isalpha() and 1 <= len(upper) <= 5:
        ccy = "USD"

    _symbol_ccy_cache[key] = ccy
    return ccy


def _universe_row(symbol: str) -> Optional[tuple]:
    if not UNIVERSE_DB.exists():
        return None
    try:
        with sqlite3.connect(str(UNIVERSE_DB)) as conn:
            cur = conn.execute(
                "SELECT currency, exchange FROM symbols WHERE symbol=? OR yahoo=? LIMIT 1",
                (symbol, symbol))
            return cur.fetchone()
    except Exception as e:
        logger.debug(f"currency: universe lookup failed for {symbol}: {e}")
        return None


# ── rates ───────────────────────────────────────────────────────────────────

def rates(base: str = "USD") -> dict:
    """{currency: units per 1 `base`} for the supported set, plus metadata.

    Cached for an hour: display conversion does not need tick-level FX, and
    hammering the vendor for it would be the fastest way to get rate-limited.
    """
    base = (base or "USD").upper()
    with _lock:
        hit = _rate_cache.get(base)
        if hit and datetime.now() - hit[0] < timedelta(minutes=RATE_TTL_MINUTES):
            return hit[1]

    from src.v5 import marketdata as md

    out: dict[str, float] = {base: 1.0}
    missing = []
    for ccy in SUPPORTED:
        if ccy == base:
            continue
        pair = f"{base}{ccy}=X"
        c = md.closes(pair, period="1mo")
        if c is not None and len(c):
            out[ccy] = round(float(c.iloc[-1]), 6)
        else:
            missing.append(ccy)

    payload = {
        "base": base,
        "rates": out,
        "unavailable": missing,
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "ttl_minutes": RATE_TTL_MINUTES,
        "source": "Yahoo Finance spot FX pairs (daily close)",
        "note": ("Rates are daily closes for display conversion only — they are not "
                 "dealable prices and carry no spread."),
    }
    with _lock:
        _rate_cache[base] = (datetime.now(), payload)
    return payload


def to_base(amount: float, from_ccy: str, base: str = "USD") -> Optional[float]:
    """Convert `amount` from its native currency into `base`. None when the rate
    is unknown. Handles the pence→pounds step for London quotes."""
    if amount is None or not from_ccy:
        return None
    r = rates(base)["rates"]
    ccy = from_ccy
    value = float(amount)
    if ccy == "GBp":                 # pence → pounds
        value, ccy = value / 100.0, "GBP"
    if ccy == base:
        return value
    rate = r.get(ccy)
    if not rate:
        return None
    return value / rate              # rate is units-of-ccy per 1 base


def convert(amount: float, from_ccy: str, to_ccy: str) -> Optional[float]:
    in_base = to_base(amount, from_ccy, "USD")
    if in_base is None:
        return None
    if to_ccy == "USD":
        return in_base
    rate = rates("USD")["rates"].get("GBP" if to_ccy == "GBp" else to_ccy)
    if not rate:
        return None
    out = in_base * rate
    return out * 100.0 if to_ccy == "GBp" else out


def currencies_for(symbols: list[str]) -> dict:
    """Batch native-currency lookup — what the UI needs to convert a table."""
    return {s: native_currency(s) for s in symbols if s}
