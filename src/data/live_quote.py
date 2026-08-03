"""
src/data/live_quote.py
======================
Live intraday quotes — the price that actually moves.

The existing tier-1 quote (`universe.quote`) fetches `period="5d", interval="1d"`
and caches for 15 minutes. That is a DAILY CLOSE: it cannot change during a
session no matter how often it is polled, which is why the research header
looked frozen. This module is the live path.

What it reports, and why each field is here:

    price         last traded price (not the daily close)
    change/pct    against the previous session's close — the number every
                  exchange and broker quotes
    day_high/low  today's range, so the price has context
    market_state  OPEN / CLOSED / PRE / POST, derived from the exchange's own
                  timezone and the age of the last minute bar
    as_of         timestamp of the last tick, in exchange-local time

`market_state` matters more than it looks. A price that is not moving because
the market is shut is not a broken feed, and the UI must be able to tell the
difference without the user guessing.

HONESTY ABOUT THE FEED: Yahoo is real-time for most US listings and delayed
(commonly 15 minutes) for many other venues. This module never claims
otherwise — it returns the tick timestamp and a `delayed_hint`, and the UI
shows both. "Live" here means continuously updating, not zero-latency.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

CACHE_SECONDS = 8            # a poll cadence the vendor tolerates
STALE_TICK_MINUTES = 6       # last bar older than this ⇒ not actively trading

_cache: dict[str, tuple[datetime, dict]] = {}
_lock = threading.Lock()

# Venues Yahoo generally streams in real time. Everything else is flagged as
# possibly delayed rather than silently presented as live.
REALTIME_EXCHANGES = {"NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "BTS", "CCC", "CCY"}


def _resolve(symbol: str) -> str:
    try:
        from src.data.universe import get_universe
        info = get_universe().resolve(symbol)
        if info and info.get("yahoo"):
            return info["yahoo"]
    except Exception:
        pass
    return symbol


def live_quote(symbol: str, *, with_intraday: bool = True) -> dict:
    """A live snapshot for one symbol. Never raises — returns {'error': ...}."""
    key = f"{symbol}|{with_intraday}"
    with _lock:
        hit = _cache.get(key)
        if hit and (datetime.now() - hit[0]).total_seconds() < CACHE_SECONDS:
            return {**hit[1], "cache": "hit"}

    yahoo = _resolve(symbol)
    try:
        import yfinance as yf
        from src.v5.marketdata import _quiet_vendor
        with _quiet_vendor():
            t = yf.Ticker(yahoo)
            fi = t.fast_info
            snap = {k: _num(getattr(fi, k, None)) for k in
                    ("last_price", "previous_close", "open", "day_high", "day_low",
                     "last_volume")}
            tz_name = getattr(fi, "timezone", None)
            exchange = getattr(fi, "exchange", None)
            ccy = getattr(fi, "currency", None)

            intraday, last_ts = [], None
            if with_intraday:
                bars = t.history(period="1d", interval="1m")
                if bars is not None and not bars.empty:
                    closes = bars["Close"].dropna()
                    last_ts = closes.index[-1]
                    # Down-sample to keep the payload small but the shape honest.
                    step = max(1, len(closes) // 120)
                    intraday = [round(float(v), 4) for v in closes.iloc[::step]]
                    if snap["last_price"] is None:
                        snap["last_price"] = round(float(closes.iloc[-1]), 4)
    except Exception as e:
        logger.debug(f"live quote failed for {symbol}: {e}")
        return {"symbol": symbol, "error": f"live quote unavailable ({type(e).__name__})"}

    price, prev = snap["last_price"], snap["previous_close"]
    if price is None:
        return {"symbol": symbol, "error": "no live price available for this symbol"}

    change = (price - prev) if prev else None
    change_pct = ((price / prev - 1) * 100) if prev else None

    state, as_of, age_min = _market_state(last_ts, tz_name)
    from src.data.currency import native_currency
    native = native_currency(yahoo) or ccy

    out = {
        "symbol": symbol,
        "yahoo": yahoo,
        "price": round(price, 4),
        "previous_close": round(prev, 4) if prev else None,
        "change": round(change, 4) if change is not None else None,
        "change_pct": round(change_pct, 3) if change_pct is not None else None,
        "open": snap["open"],
        "day_high": snap["day_high"],
        "day_low": snap["day_low"],
        "volume": snap["last_volume"],
        "currency": native,
        "exchange": exchange,
        "timezone": tz_name,
        "market_state": state,
        "as_of": as_of,
        "tick_age_minutes": age_min,
        "intraday": intraday,
        "delayed_hint": (
            None if exchange in REALTIME_EXCHANGES else
            "This venue is commonly delayed (often ~15 min) on this data source — "
            "treat the timestamp, not the clock, as the truth."),
        "source": "Yahoo Finance (fast_info + 1-minute bars)",
        "cache": "miss",
    }
    with _lock:
        _cache[key] = (datetime.now(), out)
    return out


def _num(v) -> Optional[float]:
    try:
        f = float(v)
        return round(f, 4) if f == f else None      # NaN check
    except (TypeError, ValueError):
        return None


def _market_state(last_ts, tz_name: Optional[str]) -> tuple[str, Optional[str], Optional[float]]:
    """OPEN / CLOSED / UNKNOWN, from the age of the most recent minute bar.

    Derived from the data rather than from a hardcoded session calendar: a
    calendar would need every exchange's holidays to stay correct, whereas a
    bar that stopped arriving six minutes ago is direct evidence either way.
    """
    if last_ts is None:
        return ("UNKNOWN", None, None)
    try:
        ts = last_ts.to_pydatetime()
        now = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_min = round((now - ts).total_seconds() / 60, 1)
        state = "OPEN" if age_min <= STALE_TICK_MINUTES else "CLOSED"
        return (state, ts.isoformat(timespec="seconds"), age_min)
    except Exception:
        return ("UNKNOWN", None, None)


def live_quotes(symbols: list[str]) -> dict:
    """Several symbols at once — for the tape and tables."""
    return {s: live_quote(s, with_intraday=False) for s in symbols[:40]}
