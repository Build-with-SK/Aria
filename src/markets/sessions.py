"""
src/markets/sessions.py
=======================
For any instrument at any instant: where it trades, which session it is in,
when that changes, and which of its bars could have been known.

    resolve(symbol)                 -> Instrument (venue, asset class, expiry)
    session(symbol, at)             -> SessionInfo (state, reasons, next open, close)
    is_trading_day(venue, d)        -> True | False | None (unknown)
    session_close(venue, d)         -> aware close of that trading day, or None
    latest_completed_session(venue, at, settle_minutes)
    in_session_window(venue, at, settle_minutes)
    bar_available_at(venue, d, settle_minutes)   point-in-time availability

THE RULE THAT MATTERS: UNKNOWN IS NEVER OPEN
--------------------------------------------
A venue with no spec, a year with no verified holiday calendar, a futures
holiday schedule that is not modelled — each yields UNKNOWN with the reason,
and SessionInfo.tradable is True only for OPEN. Weekend closures ARE certain
(no exchange here trades a Saturday outside a listed special session), so a
weekend reads CLOSED even without a calendar.

Halts cannot be inferred from a schedule. A caller holding a feed's halt flag
passes `halted=True`; without it the reasons say the halt status was not
observed, and the execution path's quote-freshness check is the backstop.

Everything is computed in the venue's own IANA timezone, so daylight-saving
transitions (and the weeks when London and New York are four hours apart, not
five) fall out of the timezone database rather than out of hand-written
offsets. Naive datetimes are read as the machine's local time, consistently
with the rest of the codebase.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

from src.markets import calendars
from src.markets.venues import (AUCTION, BREAK, EXCHANGE_ALIASES, FUTURES_ROOTS,
                                INDEX_OPTION_ROOTS, INDEX_VENUES, OPEN, POST_MARKET,
                                PRE_OPEN, VENUES, Phase, VenueSpec, _m)


class SessionState(str, Enum):
    OPEN = "OPEN"
    PRE_OPEN = "PRE_OPEN"
    AUCTION = "AUCTION"
    BREAK = "BREAK"
    POST_MARKET = "POST_MARKET"
    CLOSED = "CLOSED"
    HALTED = "HALTED"
    HOLIDAY = "HOLIDAY"
    EXPIRING = "EXPIRING"
    UNKNOWN = "UNKNOWN"


#: Only continuous trading is executable by default. An auction, a break, an
#: expiring contract and anything unknown are not.
TRADABLE = frozenset({SessionState.OPEN})

_HOLIDAY = object()


# ── instruments ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Instrument:
    symbol: str
    asset_class: Optional[str]
    venue: Optional[str]             # code in VENUES, or None when unknown
    expiry: Optional[date] = None
    underlying: Optional[str] = None
    basis: str = ""


_OCC = re.compile(r"^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


def resolve(symbol: str, *, venue: Optional[str] = None,
            asset_class: Optional[str] = None,
            expiry: Optional[date] = None) -> Instrument:
    """Instrument metadata from a provider symbol. Explicit arguments win.

    Reads the symbol's own shape — the same rules as
    src/core/identity.classify_provider_symbol — and never a display-name
    lookup, so "BA" is the US listing and "BA.L" is London.
    """
    sym = (symbol or "").strip().upper()
    if venue:
        return Instrument(sym, asset_class, venue if venue in VENUES else None,
                          expiry, basis=f"venue given: {venue}")
    m = _OCC.match(sym)
    if m:
        root = m.group(1)
        exp = date(2000 + int(m.group(2)), int(m.group(3)), int(m.group(4)))
        v = "US_INDEX_OPTIONS" if root in INDEX_OPTION_ROOTS else "US_EQUITY_OPTIONS"
        return Instrument(sym, "option", v, expiry or exp, underlying=root,
                          basis="OCC option symbol: root, expiry, type, strike")
    if sym.startswith("^"):
        return Instrument(sym, "index", INDEX_VENUES.get(sym), expiry,
                          basis=("index level, timed by its venue's session"
                                 if sym in INDEX_VENUES else "index with no mapped venue"))
    if sym.endswith("=F"):
        root = sym[:-2]
        return Instrument(sym, "futures", FUTURES_ROOTS.get(root), expiry, underlying=root,
                          basis=(f"continuous future, root {root}" if root in FUTURES_ROOTS
                                 else f"future root {root} has no modelled session"))
    if sym.endswith("=X"):
        return Instrument(sym, "forex", "FX", expiry, basis="FX cross")
    if sym.endswith("-USD") or "/" in sym:
        return Instrument(sym, asset_class or "crypto", "CRYPTO", expiry, basis="crypto pair")
    from src.core.identity import classify_provider_symbol
    c = classify_provider_symbol(sym)
    ex = c.get("exchange")
    code = EXCHANGE_ALIASES.get(ex or "")
    return Instrument(sym, asset_class or c.get("asset_class"), code, expiry,
                      basis=(c.get("basis") or "") + ("" if code else
                                                      f"; venue {ex!r} has no modelled session"))


# ── per-day phases ──────────────────────────────────────────────────────────

def _truncate(spec: VenueSpec, phases: tuple, close_hhmm: str) -> tuple:
    """An early close: continuous trading ends at `close`, a closing auction
    (if the venue has one) follows it, extended hours are dropped."""
    close = _m(close_hhmm)
    out = []
    for p in phases:
        if p.state in (AUCTION, POST_MARKET) or p.start >= close:
            continue
        out.append(Phase(p.state, p.start, min(p.end, close)))
    if spec.closing_auction_minutes:
        out.append(Phase(AUCTION, close, close + spec.closing_auction_minutes))
    return tuple(out)


def _day(spec: VenueSpec, d: date):
    """(phases | _HOLIDAY | None, reason). None means UNKNOWN."""
    week = spec.phases(d.weekday())
    if spec.holiday_rule == "none":
        return week, ""
    if spec.holiday_rule == "us_futures":
        cal = calendars.get("XNYS")
        if cal is None or d.year not in cal.years or (d + timedelta(days=1)).year not in cal.years:
            return None, f"no verified US holiday calendar for {d.year} — futures hours unknown"
        if week and (d in cal.holidays or (d + timedelta(days=1)) in cal.holidays):
            return None, ("US holiday: futures trade an abbreviated schedule that is "
                          "not modelled here")
        return week, ""
    cal = calendars.get(spec.calendar)
    if cal is not None and d.year in cal.years and d in cal.special_sessions:
        return tuple(Phase.of(*p) for p in cal.special_sessions[d]), "special session"
    if not week:
        return week, "weekend"
    if cal is None or d.year not in cal.years:
        return None, (f"no verified {spec.calendar or spec.code} holiday calendar for "
                      f"{d.year} — refusing rather than guessing (add data/calendars/"
                      f"{spec.calendar or spec.code}.json)")
    if d in cal.holidays:
        return _HOLIDAY, f"{spec.code} holiday"
    if d in cal.early_closes:
        return _truncate(spec, week, cal.early_closes[d]), f"early close {cal.early_closes[d]}"
    return week, ""


def _local(spec: VenueSpec, at: Optional[datetime]) -> datetime:
    at = at or datetime.now(timezone.utc)
    return at.astimezone(ZoneInfo(spec.tz))


def _at(spec: VenueSpec, d: date, minute: int) -> datetime:
    tz = ZoneInfo(spec.tz)
    if minute >= 1440:
        return datetime.combine(d + timedelta(days=1), time(0), tz)
    return datetime.combine(d, time(minute // 60, minute % 60), tz)


# ── the public answers ──────────────────────────────────────────────────────

@dataclass
class SessionInfo:
    symbol: str
    venue: Optional[str]
    venue_name: Optional[str]
    country: Optional[str]
    timezone: Optional[str]
    asset_class: Optional[str]
    state: SessionState
    local_time: Optional[str]
    trading_day: Optional[str]
    phase_ends_at: Optional[str] = None          # ISO UTC
    next_open: Optional[str] = None              # ISO UTC, None when unknown
    calendar_verified: bool = False
    expiry: Optional[str] = None
    reasons: list = field(default_factory=list)

    @property
    def tradable(self) -> bool:
        return self.state in TRADABLE

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        d["tradable"] = self.tradable
        return d


def _unknown(sym: str, inst: Optional[Instrument], why: str) -> SessionInfo:
    return SessionInfo(sym, inst.venue if inst else None, None, None, None,
                       inst.asset_class if inst else None, SessionState.UNKNOWN,
                       None, None, reasons=[why])


def venue_state(code: str, at: Optional[datetime] = None) -> tuple:
    """(SessionState, reason, phase_end_local | None, trading_day) for a venue."""
    spec = VENUES.get(code)
    if spec is None:
        return SessionState.UNKNOWN, f"no session spec for venue {code!r}", None, None
    loc = _local(spec, at)
    d = loc.date()
    phases, why = _day(spec, d)
    minute = loc.hour * 60 + loc.minute
    if phases is None:
        # A missing holiday calendar can only REMOVE trading hours, so outside
        # the regular schedule the venue is certainly not trading. Inside it,
        # it might be a holiday: UNKNOWN. (A special session outside regular
        # hours cannot be known without the calendar either — reading that as
        # CLOSED errs towards not trading, the safe side.)
        regular = spec.phases(d.weekday())
        if spec.holiday_rule == "calendar" and regular and \
                not any(p.start <= minute < p.end for p in regular):
            return (SessionState.CLOSED,
                    "outside regular hours (holiday calendar unverified)", None, d)
        return SessionState.UNKNOWN, why, None, d
    if phases is _HOLIDAY:
        return SessionState.HOLIDAY, why, None, d
    for p in phases:
        if p.start <= minute < p.end:
            return SessionState(p.state), why or p.state.lower(), _at(spec, d, p.end), d
    return SessionState.CLOSED, why or "outside the venue's trading phases", None, d


def _next_open(spec: VenueSpec, at: datetime, horizon_days: int = 15) -> Optional[datetime]:
    loc = _local(spec, at)
    minute = loc.hour * 60 + loc.minute
    state, *_ = venue_state(spec.code, at)
    for k in range(horizon_days):
        d = loc.date() + timedelta(days=k)
        phases, _ = _day(spec, d)
        if phases is None:
            return None                         # cannot see past an unknown day
        if phases is _HOLIDAY:
            continue
        for p in phases:
            if p.state != OPEN:
                continue
            if k == 0 and p.start <= minute:
                continue
            if state == SessionState.OPEN and k == 0:
                continue
            start = _at(spec, d, p.start)
            # an OPEN phase that merely continues across midnight is not a re-open
            if p.start == 0:
                prev, _ = _day(spec, d - timedelta(days=1))
                if isinstance(prev, tuple) and any(q.state == OPEN and q.end == 1440 for q in prev):
                    continue
            return start.astimezone(timezone.utc)
    return None


def session(symbol: str, at: Optional[datetime] = None, *,
            halted: Optional[bool] = None, venue: Optional[str] = None,
            expiry: Optional[date] = None) -> SessionInfo:
    """The authoritative session for one instrument at one instant."""
    at = at or datetime.now(timezone.utc)
    inst = resolve(symbol, venue=venue, expiry=expiry)
    if inst.venue is None:
        return _unknown(inst.symbol, inst, inst.basis or "no venue could be resolved")
    spec = VENUES[inst.venue]
    state, why, phase_end, d = venue_state(spec.code, at)
    loc = _local(spec, at)
    cal = calendars.get(spec.calendar) if spec.holiday_rule == "calendar" else (
        calendars.get("XNYS") if spec.holiday_rule == "us_futures" else None)
    reasons = [why] if why else []

    if inst.expiry is not None and state not in (SessionState.UNKNOWN,):
        if d and d > inst.expiry:
            state, reasons = SessionState.CLOSED, [f"contract expired {inst.expiry}"]
        elif d == inst.expiry and state in (SessionState.OPEN, SessionState.PRE_OPEN,
                                            SessionState.AUCTION):
            state = SessionState.EXPIRING
            reasons.append(f"last trading day ({inst.expiry})")
    if halted and state in (SessionState.OPEN, SessionState.AUCTION, SessionState.PRE_OPEN,
                            SessionState.EXPIRING):
        state = SessionState.HALTED
        reasons.append("trading halted (reported by the data feed)")
    elif halted is None and state == SessionState.OPEN and spec.kind != "crypto":
        reasons.append("halt status not observed — quote freshness is the backstop")

    nxt = None if state == SessionState.OPEN else _next_open(spec, at)
    return SessionInfo(
        symbol=inst.symbol, venue=spec.code, venue_name=spec.name, country=spec.country,
        timezone=spec.tz, asset_class=inst.asset_class, state=state,
        local_time=loc.isoformat(timespec="minutes"),
        trading_day=d.isoformat() if d else None,
        phase_ends_at=phase_end.astimezone(timezone.utc).isoformat() if phase_end else None,
        next_open=nxt.isoformat() if nxt else None,
        calendar_verified=(spec.holiday_rule == "none") or bool(cal and d and d.year in cal.years),
        expiry=inst.expiry.isoformat() if inst.expiry else None,
        reasons=reasons)


def is_trading_day(code: str, d: date) -> Optional[bool]:
    spec = VENUES.get(code)
    if spec is None:
        return None
    phases, _ = _day(spec, d)
    if phases is None:
        return None
    if phases is _HOLIDAY:
        return False
    return any(p.state == OPEN for p in phases)


def session_close(code: str, d: date) -> Optional[datetime]:
    """When trading day `d` ends at `code` — the end of its last OPEN or
    AUCTION phase, early closes included — as an aware datetime in the
    venue's timezone. None for a non-trading or unknown day."""
    spec = VENUES.get(code)
    if spec is None:
        return None
    phases, _ = _day(spec, d)
    if not isinstance(phases, tuple):
        return None
    ends = [p.end for p in phases if p.state in (OPEN, AUCTION)]
    return _at(spec, d, max(ends)) if ends else None


def session_open(code: str, d: date) -> Optional[datetime]:
    spec = VENUES.get(code)
    if spec is None:
        return None
    phases, _ = _day(spec, d)
    if not isinstance(phases, tuple):
        return None
    starts = [p.start for p in phases if p.state == OPEN]
    return _at(spec, d, min(starts)) if starts else None


def latest_completed_session(code: str, at: Optional[datetime] = None,
                             settle_minutes: int = 60) -> Optional[date]:
    """The most recent trading day at `code` whose close is at least
    `settle_minutes` before `at`. None when the calendar cannot answer."""
    spec = VENUES.get(code)
    if spec is None:
        return None
    at = at or datetime.now(timezone.utc)
    d = _local(spec, at).date()
    for _ in range(20):
        td = is_trading_day(code, d)
        if td is None:
            return None
        if td:
            close = session_close(code, d)
            if close is not None and at.astimezone(timezone.utc) >= \
                    (close + timedelta(minutes=settle_minutes)).astimezone(timezone.utc):
                return d
        d -= timedelta(days=1)
    return None


def in_session_window(code: str, at: Optional[datetime] = None,
                      settle_minutes: int = 60) -> Optional[bool]:
    """True from the regular open until `settle_minutes` after the close: the
    stretch in which today's daily bar exists but is not final."""
    spec = VENUES.get(code)
    if spec is None:
        return None
    at = at or datetime.now(timezone.utc)
    d = _local(spec, at).date()
    td = is_trading_day(code, d)
    if td is None:
        return None
    if not td:
        return False
    opened, close = session_open(code, d), session_close(code, d)
    if opened is None or close is None:
        return False
    return opened <= at.astimezone(opened.tzinfo) < close + timedelta(minutes=settle_minutes)


def bar_available_at(code: Optional[str], d: date,
                     settle_minutes: int = 0) -> Optional[datetime]:
    """When the daily bar dated `d` at `code` could first be known (UTC).

    This is what point-in-time alignment across markets keys on: a US bar
    dated Monday is not knowable at Tokyo's Monday open, nine hours before
    New York trades. None when the venue or day is unknown — callers must
    then treat the bar as NOT available, never as available.
    """
    if code is None:
        return None
    spec = VENUES.get(code)
    if spec is None:
        return None
    if spec.kind == "crypto":
        return datetime.combine(d + timedelta(days=1), time(0), timezone.utc) \
            + timedelta(minutes=settle_minutes)
    close = session_close(code, d)
    if close is None:
        return None
    return (close + timedelta(minutes=settle_minutes)).astimezone(timezone.utc)


def bar_known_by(code: Optional[str], d: date, when: datetime,
                 settle_minutes: int = 0) -> bool:
    """Could the daily bar dated `d` at venue `code` have been known at the
    aware instant `when`? Conservative: an unknown venue or day is known only
    once the whole date is over everywhere on Earth (UTC+14), i.e. from
    00:00 UTC two days later."""
    avail = bar_available_at(code, d, settle_minutes)
    if avail is None:
        avail = datetime.combine(d + timedelta(days=2), time(0), timezone.utc)
    return avail <= when.astimezone(timezone.utc)
