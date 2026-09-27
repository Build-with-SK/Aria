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
from datetime import date, datetime, time, timezone
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
