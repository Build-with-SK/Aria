"""
src/trading/market.py
=====================
Quotes, and whether one is fit to trade on.

NEVER TRADE FROM STALE PRICES
-----------------------------
A quote without a timestamp is treated as stale, not as fresh. That is the whole
design: every other choice fails open. Also refused: a quote from the future
(clock skew, or a bad feed), a crossed book, a missing side, a non-positive price.

MARKET HOURS
------------
Every session question is answered by src/markets/sessions.py, the one
abstraction prediction scheduling, order planning, backtests and the UI also
use. `market_open(asset_class, now, symbol=...)` resolves THAT instrument's
venue, timezone and calendar; without a symbol it falls back to the asset
class's default venue (equity -> NYSE, forex -> FX, crypto -> 24/7), which is
what callers that predate the abstraction meant. Only OPEN is tradable;
UNKNOWN (no calendar for the year, unmodelled venue) is refused, never
guessed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

#: Full-day NYSE closures — now held in src/markets/calendars.py with every
#: other venue's; re-exported because callers and messages name them.
from src.markets import calendars as _calendars          # noqa: E402

NYSE_HOLIDAYS = set(_calendars.get("XNYS").holidays)
CALENDAR_YEARS = set(_calendars.get("XNYS").years)

#: Tolerated clock skew for a quote timestamped slightly ahead of us.
MAX_FUTURE_SKEW_S = 2.0


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    timestamp: Optional[datetime]           # when the VENUE produced it (aware)
    bid_size: float = 0.0
    ask_size: float = 0.0
    venue: str = ""
    asset_class: str = "equity"

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def spread_bps(self) -> float:
        return (self.spread / self.mid) * 1e4 if self.mid > 0 else math.inf


@dataclass(frozen=True)
class Freshness:
    ok: bool
    age_s: Optional[float]
    reason: str = ""


def check(quote: Optional[Quote], *, max_age_s: float,
          now: Optional[datetime] = None) -> Freshness:
    """Is this quote fit to trade on right now?"""
    now = now or datetime.now(timezone.utc)
    if quote is None:
        return Freshness(False, None, "no quote")
    if quote.timestamp is None:
        return Freshness(False, None, "quote carries no timestamp — treated as stale")
    ts = quote.timestamp
    if ts.tzinfo is None:
        return Freshness(False, None, "quote timestamp has no timezone")
    age = (now - ts).total_seconds()
    if age < -MAX_FUTURE_SKEW_S:
        return Freshness(False, age, f"quote is {-age:.1f}s in the future (clock or feed fault)")
    for name, v in (("bid", quote.bid), ("ask", quote.ask)):
        if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
            return Freshness(False, age, f"{name} is missing or non-positive")
    if quote.bid > quote.ask:
        return Freshness(False, age, "crossed book (bid above ask)")
    if age > max_age_s:
        return Freshness(False, age, f"quote is {age:.1f}s old (limit {max_age_s:.0f}s)")
    return Freshness(True, max(age, 0.0))


_DEFAULT_VENUE = {"equity": "XNYS", "etf": "XNYS", "forex": "FX", "fx": "FX",
                  "crypto": "CRYPTO", "option": "US_EQUITY_OPTIONS"}


def market_open(asset_class: str, now: Optional[datetime] = None, *,
                symbol: Optional[str] = None) -> tuple[bool, str]:
    """(tradable, why) — from the instrument's own session when `symbol` is
    given, else from the asset class's default venue."""
    from src.markets import sessions
    now = now or datetime.now(timezone.utc)
    if symbol:
        info = sessions.session(symbol, now)
        if info.venue is None:
            return False, info.reasons[0] if info.reasons else "unknown venue"
        return info.tradable, _why(info.state.value, info.reasons, info.venue)
    code = _DEFAULT_VENUE.get((asset_class or "").lower())
    if code is None:
        return False, f"no trading hours defined for asset class {asset_class!r}"
    state, why, _end, _d = sessions.venue_state(code, now)
    return state.value == "OPEN", _why(state.value, [why], code)


def _why(state: str, reasons: list, venue: str) -> str:
    detail = "; ".join(r for r in reasons if r)
    if state == "OPEN":
        return "regular session" if venue != "CRYPTO" else "crypto trades continuously"
    if state == "HOLIDAY":
        return f"{'NYSE' if venue == 'XNYS' else venue} holiday"
    if state == "CLOSED" and detail == "weekend":
        return "weekend" if venue != "FX" else "FX market closed for the weekend"
    if state == "CLOSED":
        return f"outside regular trading hours ({venue}: {detail or 'closed'})"
    return f"{state.lower()} at {venue}: {detail}"


# ── sessions ─────────────────────────────────────────────────────────────────
#
# "Which close is the latest one a daily call can be built on?" is a calendar
# question, and getting it wrong in either direction corrupts a track record:
# answer it with a wall-clock hour in some other timezone and a machine that
# is never awake at that hour stops predicting; answer it per calendar day and
# a Saturday call is logged as new evidence when it is Friday's close again.

SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)


def is_trading_day(d: date) -> Optional[bool]:
    """NYSE: True/False, or None when no verified calendar covers that year."""
    from src.markets.sessions import is_trading_day as _itd
    return _itd("XNYS", d)


def session_close(d: date) -> datetime:
    """The NYSE close of `d` (early closes included), aware New York time.
    Falls back to the regular 16:00 for a day the calendar cannot answer."""
    from src.markets.sessions import session_close as _sc
    return _sc("XNYS", d) or datetime.combine(d, SESSION_CLOSE, NY)


def latest_completed_session(now: Optional[datetime] = None,
                             settle_minutes: int = 60) -> Optional[date]:
    """The most recent NYSE session whose close is at least `settle_minutes`
    in the past. None when the calendar cannot answer — never a guess."""
    from src.markets.sessions import latest_completed_session as _lcs
    return _lcs("XNYS", (now or datetime.now(timezone.utc)).astimezone(NY), settle_minutes)


def in_session_window(now: Optional[datetime] = None,
                      settle_minutes: int = 60) -> Optional[bool]:
    """True from the open until `settle_minutes` after the close of an NYSE
    trading day: the stretch in which today's daily bar exists but is not
    final. Delegates to src/markets/sessions."""
    from src.markets.sessions import in_session_window as _isw
    return _isw("XNYS", (now or datetime.now(timezone.utc)).astimezone(NY), settle_minutes)
