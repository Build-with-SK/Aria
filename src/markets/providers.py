"""
src/markets/providers.py
========================
Calendar providers: WHETHER a venue trades on a date, and its open, close and
break that day. The session engine (src/markets/sessions.py) asks; nothing
else in ARIA imports a calendar library.

    Instrument -> Venue -> Session engine -> Calendar provider chain
                                               1. owner file (data/calendars/*.json)
                                               2. ExchangeCalendarsProvider (exchange_calendars)
                                               3. built-in static (calendars.py)
                                               4. nothing -> UNKNOWN

A provider answers with DayFacts or None. None means "cannot answer": the
venue is not mapped, the date is outside the provider's verified range, the
library is missing, or it raised. The engine then tries the next provider and
finally returns UNKNOWN — which is never OPEN.

WHAT THE PROVIDER DOES NOT DECIDE
---------------------------------
Phase structure — pre-open, auctions, post-market — stays in
src/markets/venues.py, because exchange_calendars models only open, close and
a break. The engine applies the provider's deviations from its OWN regular
times (an early close, a late open) to the venue's phases, so a calendar
whose "close" includes the closing auction (Tokyo) and one whose close does
not (London) are both read correctly. A date whose regular times differ from
today's (Tokyo closed at 15:00 before 2024-11-05) is a historical schedule and
is built from the provider's facts directly.

WHY THE MAPPING IS NARROW
-------------------------
Only venues the library covers AUTHORITATIVELY are mapped. Not mapped, on
purpose:
  XNSE, XSHE      the library has no NSE or Shenzhen calendar; aliasing them
                  to BSE / Shanghai would be an inference, not coverage. An
                  owner file (data/calendars/XNSE.json, with a source) or an
                  explicit {"alias_of": "XBOM", "source": ...} enables them.
  futures         CMES / IEPA / us_futures are generic approximations (CMES
                  models one 17:00-17:00 CT session for every CME product and
                  no daily maintenance break). The contract-specific hours in
                  venues.py are kept; US holidays stay UNKNOWN for futures.
US listed options use the NYSE calendar (their venue specs name "XNYS"), with
their own 16:00 / 16:15 closes, so an NYSE early close moves index options to
13:15 by the same delta.

DETERMINISM
-----------
exchange_calendars defaults its range to "today + 1 year", which would make an
answer depend on when the process started. The range here is fixed:
START..HORIZON, clamped to each calendar's own bound_max() (India and China are
recorded only to 2026; asking beyond raises in the library, and is UNKNOWN
here). No network access: the library is local, rule- and table-based.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

#: Fixed provider range. Moving HORIZON is a reviewed change: re-run
#: tests/test_calendar_provider.py after upgrading exchange_calendars.
START = date(2006, 1, 1)
HORIZON = date(2027, 12, 31)
LIBRARY = "exchange_calendars"
PINNED_VERSION = "4.13.2"

#: ARIA calendar code -> exchange_calendars name. Authoritative coverage only.
XCALS_MAP: dict[str, str] = {
    "XNYS": "XNYS", "XLON": "XLON", "XBOM": "XBOM", "XETR": "XETR",
    "XPAR": "XPAR", "XAMS": "XAMS", "XBRU": "XBRU", "XLIS": "XLIS",
    "XSWX": "XSWX", "XTKS": "XTKS", "XHKG": "XHKG", "XSHG": "XSHG",
    "XTSE": "XTSE", "XASX": "XASX",
}


@dataclass(frozen=True)
class DayFacts:
    """One venue-day, in minutes after LOCAL midnight in the venue's timezone."""
    is_session: bool
    open_minute: Optional[int] = None
    close_minute: Optional[int] = None
    break_start: Optional[int] = None
    break_end: Optional[int] = None
    regular_open: Optional[int] = None       # the calendar's regular open AS OF this date
    regular_close: Optional[int] = None
    current_open: Optional[int] = None       # the calendar's regular open today
    current_close: Optional[int] = None
    source: str = ""

    @property
    def historical_schedule(self) -> bool:
        return (self.regular_open, self.regular_close) != (self.current_open, self.current_close)


class CalendarProvider:
    name = "provider"

    def facts(self, code: str, d: date, tz: str) -> Optional[DayFacts]:   # pragma: no cover
        raise NotImplementedError

    def bounds(self, code: str) -> Optional[dict]:                         # pragma: no cover
        return None


def _minutes(ts, tz: ZoneInfo) -> int:
    t = ts.tz_convert(tz) if ts.tzinfo else ts.tz_localize("UTC").tz_convert(tz)
    return t.hour * 60 + t.minute


def _regular(times, d: date) -> Optional[int]:
    """The regular time in force on `d` from exchange_calendars' ((start, time), ...)."""
    if not times:
        return None
    chosen = None
    for start, t in times:
        if start is None or start.date() <= d:
            chosen = t
    return None if chosen is None else chosen.hour * 60 + chosen.minute


class ExchangeCalendarsProvider(CalendarProvider):
    name = LIBRARY

    def __init__(self):
        self._cals: dict = {}
        self._failed: dict[str, str] = {}
        self._lock = threading.Lock()
        self._warned: set = set()
        # A venue-day's facts are fixed for a given calendar build, so they
        # are cached (bounded). Failures are NOT cached: None is recomputed.
        self._facts_cache: dict = {}

    def _warn_once(self, key: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            logger.warning(msg)

    def calendar(self, code: str):
        """The library calendar for an ARIA calendar code, built once with a
        fixed range. None when unmapped, unavailable or unbuildable."""
        name = XCALS_MAP.get(code)
        if name is None:
            return None
        with self._lock:
            if name in self._cals:
                return self._cals[name]
            if name in self._failed:
                return None
            try:
                import exchange_calendars as xc
                cls = _calendar_class(xc, name)
                start, end = START, HORIZON
                bmin = cls.bound_min() if hasattr(cls, "bound_min") else None
                bmax = cls.bound_max() if hasattr(cls, "bound_max") else None
                if bmin is not None:
                    start = max(start, bmin.date())
                if bmax is not None:              # recorded data ends here (India, China)
                    end = min(end, bmax.date())
                cal = xc.get_calendar(name, start=start.isoformat(), end=end.isoformat())
                self._cals[name] = cal
                return cal
            except ImportError as e:
                self._failed[name] = f"{LIBRARY} not installed: {e}"
            except Exception as e:                 # never let a calendar crash a session
                self._failed[name] = f"{type(e).__name__}: {e}"
            self._warn_once(f"build:{name}",
                            f"calendar provider: {name} unavailable — {self._failed[name]} "
                            f"(sessions at this venue read UNKNOWN)")
            return None

    def facts(self, code: str, d: date, tz: str) -> Optional[DayFacts]:
        key = (code, d, tz)
        hit = self._facts_cache.get(key)
        if hit is not None:
            return hit
        out = self._facts(code, d, tz)
        if out is not None:
            if len(self._facts_cache) > 50_000:
                self._facts_cache.clear()
            self._facts_cache[key] = out
        return out

    def _facts(self, code: str, d: date, tz: str) -> Optional[DayFacts]:
        cal = self.calendar(code)
        if cal is None:
            return None
        try:
            import pandas as pd
            ts = pd.Timestamp(d)
            if ts < cal.first_session or ts > cal.last_session:
                return None                       # outside the verified range
            zone = ZoneInfo(tz)
            src = f"{LIBRARY} {PINNED_VERSION} {XCALS_MAP[code]}"
            reg_open, reg_close = _regular(cal.open_times, d), _regular(cal.close_times, d)
            cur_open = _regular(cal.open_times, date.max)
            cur_close = _regular(cal.close_times, date.max)
            if not cal.is_session(ts):
                return DayFacts(False, regular_open=reg_open, regular_close=reg_close,
                                current_open=cur_open, current_close=cur_close, source=src)
            bs, be = cal.session_break_start(ts), cal.session_break_end(ts)
            return DayFacts(
                True, _minutes(cal.session_open(ts), zone), _minutes(cal.session_close(ts), zone),
                None if pd.isna(bs) else _minutes(bs, zone),
                None if pd.isna(be) else _minutes(be, zone),
                regular_open=reg_open, regular_close=reg_close,
                current_open=cur_open, current_close=cur_close, source=src)
        except Exception as e:
            self._warn_once(f"facts:{code}:{type(e).__name__}",
                            f"calendar provider: {code} failed on {d} — {type(e).__name__}: {e} "
                            f"(this venue-day reads UNKNOWN)")
            return None

    def bounds(self, code: str) -> Optional[dict]:
        cal = self.calendar(code)
        if cal is None:
            why = self._failed.get(XCALS_MAP.get(code, ""), "not mapped")
            return {"provider": LIBRARY, "available": False, "why": why}
        return {"provider": LIBRARY, "available": True, "calendar": XCALS_MAP[code],
                "from": cal.first_session.date().isoformat(),
                "to": cal.last_session.date().isoformat(),
                "version": _installed_version()}


def _calendar_class(xc, name: str):
    """The calendar's class, to read its recorded bounds before building it.
    Uses the library's registry; falls back to building a default instance
    (slower, same class) if a future release moves the registry."""
    try:
        from exchange_calendars.calendar_utils import _default_calendar_factories
        return _default_calendar_factories[name]
    except Exception:
        return type(xc.get_calendar(name))


def _installed_version() -> Optional[str]:
    try:
        from importlib.metadata import version
        return version(LIBRARY)
    except Exception:
        return None


_chain: list[CalendarProvider] = [ExchangeCalendarsProvider()]


def chain() -> list[CalendarProvider]:
    return list(_chain)


def set_chain(providers: list[CalendarProvider]) -> None:
    """Replace the provider chain (tests inject failing providers here)."""
    _chain[:] = providers


def default_chain() -> None:
    _chain[:] = [ExchangeCalendarsProvider()]
