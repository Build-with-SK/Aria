"""
src/markets/candle_sessions.py
===============================
Which part of one local trading day is one continuous OPEN window — the
building block a session-anchored multi-hour candle aggregator needs to know
that, say, five consecutive hourly bars actually belong to the same
uninterrupted session rather than straddling a lunch break, an early close,
or a day the calendar cannot speak for.

    session_windows(provider_symbol, day) -> {
        "status": "verified" | "closed" | "unknown",
        "venue": str | None,
        "timezone": str | None,
        "reason": str,
        "windows": [{"open": aware datetime, "close": aware datetime}, ...],
    }

This is a read-only, additive view on top of src.markets.sessions: it calls
the SAME resolve() and the SAME _day() that decide every other session
answer in this package, so it inherits that function's calendar precedence
(owner file > calendar provider > built-in static > UNKNOWN) and dated
holidays, early closes, historical schedules and lunch breaks without
re-deriving any of them. UNKNOWN NEVER BECOMES "verified" or OPEN here
either: a year with no verified calendar, an unmapped venue or an invalid
timezone all report status="unknown", and the caller must treat that exactly
like sessions.SessionState.UNKNOWN — not tradable, not aggregatable.

`windows` holds only continuous trading (OPEN) phases, in the venue's own
timezone. Pre-open, auctions, post-market and intraday breaks are excluded,
and a break SPLITS a day into separate windows rather than being bridged —
the caller must not join bars across one. Each call answers for one LOCAL
venue date; a venue whose day rolls over midnight (FX, CME Globex) reports
only the OPEN phase(s) that fall inside that local date, so a window ending
at local midnight is a window boundary here, not a day one, and the caller
must ask again for the next date to see what follows it.
"""
from __future__ import annotations

from datetime import date

from src.markets import sessions
from src.markets.sessions import _HOLIDAY, _at, _day, _zone
from src.markets.venues import OPEN, VENUES


def session_windows(provider_symbol: str, day: date) -> dict:
    """Continuous OPEN windows for `provider_symbol`'s venue on its own
    local calendar date `day` (not a UTC date — a venue's trading day is
    read in its own timezone, same as everywhere else in src.markets)."""
    inst = sessions.resolve(provider_symbol)
    if inst.venue is None:
        return {"status": "unknown", "venue": None, "timezone": None,
                "reason": inst.basis or "no venue could be resolved", "windows": []}
    spec = VENUES[inst.venue]
    if _zone(spec) is None:
        return {"status": "unknown", "venue": spec.code, "timezone": spec.tz,
                "reason": f"invalid timezone {spec.tz!r} for {spec.code}", "windows": []}
    phases, why = _day(spec, day, inst.calendar)
    if phases is None:
        return {"status": "unknown", "venue": spec.code, "timezone": spec.tz,
                "reason": why, "windows": []}
    if phases is _HOLIDAY:
        return {"status": "closed", "venue": spec.code, "timezone": spec.tz,
                "reason": why, "windows": []}
    windows = [{"open": _at(spec, day, p.start), "close": _at(spec, day, p.end)}
               for p in phases if p.state == OPEN]
    if not windows:
        return {"status": "closed", "venue": spec.code, "timezone": spec.tz,
                "reason": why or "not a trading day", "windows": []}
    return {"status": "verified", "venue": spec.code, "timezone": spec.tz,
            "reason": why, "windows": windows}
