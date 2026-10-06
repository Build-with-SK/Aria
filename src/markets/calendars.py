"""
src/markets/calendars.py
========================
Holidays, early closes and special sessions — per venue, per VERIFIED year.

A year is either covered or it is not. For a covered year every weekday not
listed as a holiday is a trading day; for an uncovered year a weekday is
UNKNOWN, because "probably open" is how an order gets sent into a closed
market on Diwali or Golden Week.

Built in, because they can be stated with confidence and are dated:

  XNYS   NYSE full-day closures 2026-2027 (moved here from
         src/trading/market.py) and the 13:00 early closes known for those
         years. A missing early close is caught downstream by quote freshness.
  XLON   England & Wales bank holidays 2026-2027, and the 12:30 half days on
         24 and 31 December.

Everything else is loaded from data/calendars/<CODE>.json, which the owner (or
a sync job) fills from the exchange's own circular:

    {"source": "NSE circular NSE/CMTR/xxxx, 2025-12-12",
     "years": [2026],
     "holidays": ["2026-01-26", "..."],
     "early_closes": {"2026-12-24": "12:30"},
     "special_sessions": {"2026-11-08": [["OPEN", "18:00", "19:00"]]}}

`special_sessions` replaces that day's phases entirely (e.g. NSE Muhurat
trading on a Sunday). Nothing in this file is inferred from a rule of thumb.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
CALENDAR_DIR = ROOT / "data" / "calendars"


@dataclass(frozen=True)
class CalendarData:
    code: str
    years: frozenset
    holidays: frozenset = frozenset()
    early_closes: dict = field(default_factory=dict)        # date -> "HH:MM" local
    special_sessions: dict = field(default_factory=dict)    # date -> ((state, "HH:MM", "HH:MM"), ...)
    source: str = ""
    #: An owner decision that this venue shares another calendar code's
    #: trading days (e.g. XNSE -> XBOM). Never made in code — only in a file
    #: that states its source.
    alias_of: Optional[str] = None


_BUILTIN: dict[str, CalendarData] = {
    "XNYS": CalendarData(
        "XNYS", frozenset({2026, 2027}),
        holidays=frozenset({
            date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
            date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
            date(2026, 11, 26), date(2026, 12, 25),
            date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
            date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
            date(2027, 11, 25), date(2027, 12, 24)}),
        early_closes={date(2026, 11, 27): "13:00", date(2026, 12, 24): "13:00",
                      date(2027, 11, 26): "13:00"},
        source="NYSE holiday and early-close schedule 2026-2027"),
    "XLON": CalendarData(
        "XLON", frozenset({2026, 2027}),
        holidays=frozenset({
            date(2026, 1, 1), date(2026, 4, 3), date(2026, 4, 6), date(2026, 5, 4),
            date(2026, 5, 25), date(2026, 8, 31), date(2026, 12, 25), date(2026, 12, 28),
            date(2027, 1, 1), date(2027, 3, 26), date(2027, 3, 29), date(2027, 5, 3),
            date(2027, 5, 31), date(2027, 8, 30), date(2027, 12, 27), date(2027, 12, 28)}),
        early_closes={date(2026, 12, 24): "12:30", date(2026, 12, 31): "12:30",
                      date(2027, 12, 24): "12:30", date(2027, 12, 31): "12:30"},
        source="England & Wales bank holidays 2026-2027; LSE half days 24/31 Dec"),
}

_overrides: dict[str, CalendarData] = {}
_file_cache: dict[str, Optional[CalendarData]] = {}


def _parse_date(s: str) -> date:
    return date.fromisoformat(str(s)[:10])


def _from_file(code: str) -> Optional[CalendarData]:
    if code in _file_cache:
        return _file_cache[code]
    path = CALENDAR_DIR / f"{code}.json"
    cal = None
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            cal = CalendarData(
                code, frozenset(int(y) for y in raw.get("years") or []),
                holidays=frozenset(_parse_date(d) for d in raw.get("holidays") or []),
                early_closes={_parse_date(d): str(t) for d, t in
                              (raw.get("early_closes") or {}).items()},
                special_sessions={_parse_date(d): tuple(tuple(p) for p in phases)
                                  for d, phases in (raw.get("special_sessions") or {}).items()},
                source=str(raw.get("source") or "").strip(),
                alias_of=(str(raw["alias_of"]).strip() or None) if raw.get("alias_of") else None)
            if not cal.source or not (cal.years or cal.alias_of):
                logger.warning("calendar %s has no source or no years — ignored", code)
                cal = None
        except Exception as e:                       # a broken file is no calendar
            logger.warning("calendar %s unreadable: %s", code, e)
            cal = None
    _file_cache[code] = cal
    return cal


def get_override(code: Optional[str]) -> Optional[CalendarData]:
    """An owner-supplied calendar (runtime registration, else
    data/calendars/<CODE>.json). Highest precedence: an exchange circular the
    owner filed is more current than any library release."""
    if not code:
        return None
    if code in _overrides:
        return _overrides[code]
    return _from_file(code)


def get_builtin(code: Optional[str]) -> Optional[CalendarData]:
    """The dated calendars shipped in this file (XNYS, XLON 2026-2027).
    Lowest precedence; they agree with exchange_calendars on every 2026 date
    (tests/test_calendar_provider.py checks it)."""
    return _BUILTIN.get(code) if code else None


def get(code: Optional[str]) -> Optional[CalendarData]:
    """The calendar for `code`: test/runtime override, then a data file, then
    the built-in one. Years from a file EXTEND the built-in coverage."""
    if not code:
        return None
    if code in _overrides:
        return _overrides[code]
    builtin, fileside = _BUILTIN.get(code), _from_file(code)
    if builtin and fileside:
        return CalendarData(
            code, builtin.years | fileside.years,
            holidays=builtin.holidays | fileside.holidays,
            early_closes={**builtin.early_closes, **fileside.early_closes},
            special_sessions={**builtin.special_sessions, **fileside.special_sessions},
            source=f"{builtin.source}; {fileside.source}")
    return fileside or builtin


def register(cal: Optional[CalendarData], code: Optional[str] = None) -> None:
    """Install (or with None, remove) a calendar at runtime. Tests use this;
    a sync job could too."""
    key = code or (cal.code if cal else None)
    if not key:
        return
    if cal is None:
        _overrides.pop(key, None)
    else:
        _overrides[key] = cal


def clear_cache() -> None:
    _file_cache.clear()


def coverage() -> dict:
    """Which venues have a verified calendar for which dates, from which
    provider — for the UI and the docs, so the gaps are visible rather than
    discovered. Order is the precedence the session engine applies."""
    from src.markets import providers
    from src.markets.venues import VENUES
    out = {}
    for spec in VENUES.values():
        if spec.holiday_rule != "calendar":
            continue
        code = spec.calendar or spec.code
        ov, bi = get_override(code), get_builtin(code)
        out[spec.code] = {
            "calendar": code,
            # the static years (owner file + built-in), as before
            "years": sorted((ov.years if ov else frozenset()) | (bi.years if bi else frozenset())),
            "source": "; ".join(c.source for c in (ov, bi) if c) or None,
            "providers": [x for x in (
                {"provider": "owner file", "years": sorted(ov.years), "source": ov.source} if ov else None,
                *[b for b in (p.bounds(code) for p in providers.chain()) if b],
                {"provider": "built-in", "years": sorted(bi.years), "source": bi.source} if bi else None,
            ) if x],
        }
    return out
