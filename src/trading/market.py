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
  crypto   always open
  forex    Sunday 17:00 to Friday 17:00, New York time
  equity   09:30–16:00 New York time, Monday–Friday, excluding NYSE holidays

The holiday list is static and dated. For a year it does not cover, equities are
reported CLOSED — an out-of-date calendar stops trading rather than letting it
trade on a day the exchange is shut. Extend `NYSE_HOLIDAYS` each December.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

#: Full-day NYSE closures. Early closes are treated as full sessions — the
#: validator refuses near-close anyway via max_quote_age on thin books.
NYSE_HOLIDAYS = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
}
CALENDAR_YEARS = {d.year for d in NYSE_HOLIDAYS}

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


def market_open(asset_class: str, now: Optional[datetime] = None) -> tuple[bool, str]:
    now = (now or datetime.now(timezone.utc)).astimezone(NY)
    ac = (asset_class or "").lower()
    if ac == "crypto":
        return True, "crypto trades continuously"
    if ac == "forex":
        wd, t = now.weekday(), now.time()
        if wd == 5 or (wd == 4 and t >= time(17)) or (wd == 6 and t < time(17)):
            return False, "FX market closed for the weekend"
        return True, "FX market open"
    if ac == "equity":
        if now.year not in CALENDAR_YEARS:
            return False, (f"no NYSE holiday calendar for {now.year} — refusing "
                           f"rather than guessing; extend market.NYSE_HOLIDAYS")
        if now.weekday() >= 5:
            return False, "weekend"
        if now.date() in NYSE_HOLIDAYS:
            return False, "NYSE holiday"
        if not (time(9, 30) <= now.time() < time(16, 0)):
            return False, "outside regular trading hours (09:30-16:00 New York)"
        return True, "regular session"
    return False, f"no trading hours defined for asset class {asset_class!r}"


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
    """True/False for a NYSE session day, None when the holiday calendar does
    not cover that year — unknown is not reported as either answer."""
    if d.year not in CALENDAR_YEARS:
        return None
    return d.weekday() < 5 and d not in NYSE_HOLIDAYS


def session_close(d: date) -> datetime:
    """The regular-session close of `d`, as an aware New York datetime."""
    return datetime.combine(d, SESSION_CLOSE, NY)


def _as_ny(now: Optional[datetime]) -> datetime:
    # A naive datetime is the machine's local time — astimezone() on a naive
    # value interprets it that way, which is what every caller in this repo
    # that passes datetime.now() means.
    return (now or datetime.now(timezone.utc)).astimezone(NY)


def latest_completed_session(now: Optional[datetime] = None,
                             settle_minutes: int = 60) -> Optional[date]:
    """The most recent NYSE session whose close is at least `settle_minutes`
    in the past. None when the calendar cannot answer — never a guess."""
    ny = _as_ny(now)
    d = ny.date()
    for _ in range(15):
        td = is_trading_day(d)
        if td is None:
            return None
        if td and ny >= session_close(d) + timedelta(minutes=settle_minutes):
            return d
        d -= timedelta(days=1)
    return None


def in_session_window(now: Optional[datetime] = None,
                      settle_minutes: int = 60) -> Optional[bool]:
    """True from the open until `settle_minutes` after the close of a trading
    day: the stretch in which today's daily bar exists but is not final. A
    daily call made inside it is built on a different information set from one
    made after it, so the two must never be pooled as one measurement."""
    ny = _as_ny(now)
    td = is_trading_day(ny.date())
    if td is None:
        return None
    if not td:
        return False
    opened = datetime.combine(ny.date(), SESSION_OPEN, NY)
    return opened <= ny < session_close(ny.date()) + timedelta(minutes=settle_minutes)
