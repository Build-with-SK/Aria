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

from src.markets import calendars, providers
from src.markets.venues import (AUCTION, BREAK, EXCHANGE_ALIASES, FUTURES_CALENDARS,
                                FUTURES_ROOTS, INDEX_OPTION_ROOTS, INDEX_VENUES, OPEN,
                                POST_MARKET, PRE_OPEN, VENUES, Phase, VenueSpec, _m)


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
    #: Futures: the contract calendar (product group) whose holidays apply —
    #: venues.FUTURES_CALENDARS. None for everything else.
    calendar: Optional[str] = None


_OCC = re.compile(r"^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")
#: A broker's crypto pair without a separator (Alpaca reports "BTCUSD").
_CRYPTO_PAIR = re.compile(r"^[A-Z0-9]{2,10}(USD|USDT|USDC|BTC|ETH|EUR|GBP)$")
_FX_PAIR = re.compile(r"^[A-Z]{6}$")


def _by_asset_class(sym: str, asset_class: str,
                    expiry: Optional[date]) -> Optional[Instrument]:
    """A bare symbol whose shape says nothing, read with the asset class the
    caller already knows (a broker position, a queued trade). The class only
    settles a FORMAT ambiguity: "BTCUSD" labelled crypto is the BTC/USD pair.
    A class that contradicts the symbol ("SPY" labelled crypto) resolves to
    no venue — UNKNOWN — rather than letting a label make an equity 24/7."""
    ac = (asset_class or "").strip().lower()
    if ac == "crypto":
        if _CRYPTO_PAIR.match(sym):
            return Instrument(sym, "crypto", "CRYPTO", expiry,
                              basis="crypto pair (asset class given by the caller)")
        return Instrument(sym, "crypto", None, expiry,
                          basis=f"labelled crypto but {sym!r} is not a crypto pair — "
                                f"no venue rather than a guessed one")
    if ac in ("forex", "fx"):
        if _FX_PAIR.match(sym):
            return Instrument(sym, "forex", "FX", expiry,
                              basis="FX pair (asset class given by the caller)")
        return Instrument(sym, "forex", None, expiry,
                          basis=f"labelled forex but {sym!r} is not a currency pair")
    if ac in ("futures", "future"):
        return Instrument(sym, "futures", FUTURES_ROOTS.get(sym), expiry, underlying=sym,
                          basis=(f"future root {sym} (asset class given by the caller)"
                                 if sym in FUTURES_ROOTS else
                                 f"future {sym!r} has no modelled contract session"),
                          calendar=FUTURES_CALENDARS.get(sym))
    if ac in ("option", "options"):
        return Instrument(sym, "option", None, expiry,
                          basis="option without an OCC symbol: its expiry and venue are unknown")
    return None


def resolve(symbol: str, *, venue: Optional[str] = None,
            asset_class: Optional[str] = None,
            expiry: Optional[date] = None) -> Instrument:
    """Instrument metadata from a provider symbol. Explicit arguments win.

    Reads the symbol's own shape — the same rules as
    src/core/identity.classify_provider_symbol — and never a display-name
    lookup, so "BA" is the US listing and "BA.L" is London. A bare symbol
    with a crypto, FX, futures or option `asset_class` is read as that class
    (see _by_asset_class); the shape, when it says something, still wins.
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
                                 else f"future root {root} has no modelled session"),
                          calendar=FUTURES_CALENDARS.get(root))
    if sym.endswith("=X"):
        return Instrument(sym, "forex", "FX", expiry, basis="FX cross")
    if sym.endswith("-USD") or "/" in sym:
        return Instrument(sym, asset_class or "crypto", "CRYPTO", expiry, basis="crypto pair")
    if asset_class:
        by_class = _by_asset_class(sym, asset_class, expiry)
        if by_class is not None:
            return by_class
    from src.core.identity import classify_provider_symbol
    c = classify_provider_symbol(sym)
    ex = c.get("exchange")
    code = EXCHANGE_ALIASES.get(ex or "")
    return Instrument(sym, asset_class or c.get("asset_class"), code, expiry,
                      basis=(c.get("basis") or "") + ("" if code else
                                                      f"; venue {ex!r} has no modelled session"))


# ── per-day phases ──────────────────────────────────────────────────────────

def _truncate_at(spec: VenueSpec, phases: tuple, close: int) -> tuple:
    """An early close: continuous trading ends at `close` (minutes), a closing
    auction (if the venue has one) follows it, extended hours are dropped."""
    out = []
    for p in phases:
        if p.state in (AUCTION, POST_MARKET) or p.start >= close:
            continue
        out.append(Phase(p.state, p.start, min(p.end, close)))
    if spec.closing_auction_minutes:
        out.append(Phase(AUCTION, close, close + spec.closing_auction_minutes))
    return tuple(out)


def _truncate(spec: VenueSpec, phases: tuple, close_hhmm: str) -> tuple:
    return _truncate_at(spec, phases, _m(close_hhmm))


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _static_day(spec: VenueSpec, cal, d: date, week: tuple, label: str):
    """A day from a dated static calendar (owner file or built-in)."""
    if d in getattr(cal, "unknown_days", {}):
        return None, (f"{cal.unknown_days[d]} ({label}) — the whole day is UNKNOWN "
                      f"until the source gives its hours")
    if d in cal.special_sessions:
        return tuple(Phase.of(*p) for p in cal.special_sessions[d]), f"special session ({label})"
    if not week:
        return week, "weekend"
    if d in cal.holidays:
        return _HOLIDAY, f"{spec.code} holiday ({label})"
    if d in cal.early_closes:
        return (_truncate(spec, week, cal.early_closes[d]),
                f"early close {cal.early_closes[d]} ({label})")
    return week, ""


def _facts_day(spec: VenueSpec, f, week: tuple):
    """A day from a calendar provider's facts, applied to the venue's phases.

    Deviations are measured against the PROVIDER'S OWN regular times for that
    date, so a calendar whose close includes the closing auction and one whose
    close does not are both read correctly. A date whose regular times differ
    from today's (a historical schedule) and a session on a day the venue
    spec has no hours for (a special session) are built from the facts.
    """
    if not f.is_session:
        return (_HOLIDAY, f"{spec.code} holiday ({f.source})") if week else (week, "weekend")
    if f.open_minute is None or f.close_minute is None or f.close_minute <= f.open_minute:
        return None, f"calendar facts unusable for {spec.code} ({f.source})"
    if not week or f.historical_schedule:
        bs, be = f.break_start, f.break_end
        if bs is not None and be is not None and f.open_minute < bs < be < f.close_minute:
            phases = (Phase(OPEN, f.open_minute, bs), Phase(BREAK, bs, be),
                      Phase(OPEN, be, f.close_minute))
        else:
            phases = (Phase(OPEN, f.open_minute, f.close_minute),)
        kind = "special session" if not week else "historical schedule"
        return phases, f"{kind} {_hhmm(f.open_minute)}-{_hhmm(f.close_minute)} ({f.source})"
    phases, notes = week, []
    if f.regular_close is not None and f.close_minute != f.regular_close:
        delta = f.close_minute - f.regular_close
        cont_end = max(p.end for p in week if p.state == OPEN)
        new_end = cont_end + delta
        if delta < 0 and spec.early_close_includes_auction:
            new_end -= spec.closing_auction_minutes
        if delta < 0:
            phases = _truncate_at(spec, phases, new_end)
            notes.append(f"early close {_hhmm(new_end)}")
        else:                                     # a late close: extend, shift the rest
            phases = tuple(Phase(p.state, p.start + (delta if p.start >= cont_end else 0),
                                 min(1440, p.end + delta if p.end >= cont_end else p.end))
                           for p in phases)
            notes.append(f"late close {_hhmm(new_end)}")
    if f.regular_open is not None and f.open_minute != f.regular_open:
        delta = f.open_minute - f.regular_open
        first = min(p.start for p in phases if p.state == OPEN)
        phases = tuple(Phase(p.state, max(p.start, first + delta) if p.state == OPEN
                             and p.start == first else p.start, p.end)
                       for p in phases)
        notes.append(f"{'late' if delta > 0 else 'early'} open {_hhmm(first + delta)}")
    return phases, (f"{'; '.join(notes)} ({f.source})" if notes else "")


def _calendar_code(spec: VenueSpec) -> str:
    return spec.calendar or spec.code


def _futures_day(spec: VenueSpec, d: date, week: tuple, contract: Optional[str]):
    """A futures day: the venue's contract hours, the CONTRACT's holidays.

    A contract calendar (data/calendars/<GROUP>.json, e.g. CME_EQUITY) that
    covers the year answers for the dates it lists: an abbreviated session
    (special_sessions, the day's phases in full), a full closure (holidays),
    an announced-but-unspecified day (unknown_days), or an explicit regular
    day (regular_days). Every US holiday and the evening before it must be
    listed one way or another; one the calendar is silent about is UNKNOWN,
    not assumed regular. Without a contract calendar every US holiday and its
    eve are UNKNOWN, as before. US holiday facts come from the XNYS chain.
    """
    cal = calendars.get_override(contract) if contract else None
    if cal is not None and cal.invalid:
        if not week:
            return week, "weekend"
        return None, f"contract calendar {contract} is invalid — {cal.invalid}"
    covered = cal is not None and d.year in cal.years
    if covered:
        label = cal.source or f"{contract} contract calendar"
        if d in cal.unknown_days:
            return None, f"{cal.unknown_days[d]} ({label})"
        if d in cal.special_sessions:
            return (tuple(Phase.of(*p) for p in cal.special_sessions[d]),
                    f"{contract} abbreviated session ({label})")
        if d in cal.early_closes:
            return None, (f"{contract}: an early close alone does not say when the "
                          f"evening session reopens — state the day in special_sessions")
        if d in cal.holidays:
            return (_HOLIDAY, f"{contract} holiday ({label})") if week else (week, "weekend")
        if d in cal.regular_days:
            return week, f"regular session confirmed by {label}"
    nyse = VENUES["XNYS"]
    for dd in (d, d + timedelta(days=1)):
        ph, why = _day(nyse, dd)
        if ph is None:
            return None, f"no verified US holiday calendar for {dd} — futures hours unknown"
        if ph is _HOLIDAY and week:
            if covered:
                return None, (f"{dd} is a US holiday and the {contract} calendar does not "
                              f"state {d}'s schedule — UNKNOWN, not assumed regular")
            return None, ("US holiday: futures trade an abbreviated schedule that is "
                          "not modelled here" + (f" (no {contract} contract calendar)"
                                                 if contract else ""))
    return week, ""


def _day(spec: VenueSpec, d: date, contract: Optional[str] = None):
    """(phases | _HOLIDAY | None, reason). None means UNKNOWN.

    The ONE place a venue-day is resolved. Calendar precedence:
      1. owner file / runtime registration (calendars.get_override) — an
         owner file that exists but does not validate makes the venue
         UNKNOWN; it does not fall through to what it meant to override
      2. calendar providers (src/markets/providers.py: exchange_calendars)
      3. built-in static calendars (calendars.get_builtin)
      4. none -> UNKNOWN (a weekend is still certainly closed)
    Futures take `contract` (their product group's calendar) instead.
    """
    week = spec.phases(d.weekday())
    if _zone(spec) is None:
        return None, f"invalid timezone {spec.tz!r} for {spec.code}"
    if spec.holiday_rule == "none":
        return week, ""
    if spec.holiday_rule == "us_futures":
        return _futures_day(spec, d, week, contract)

    code = _calendar_code(spec)
    override = calendars.get_override(code)
    if override is not None and override.invalid:
        if not week:
            return week, "weekend"
        return None, f"owner calendar for {code} is invalid — {override.invalid}"
    if override is not None and d.year in override.years:
        label = override.source or "owner calendar"
        if getattr(override, "verification", "") == "provisional":
            label += " — PROVISIONALLY verified: not yet read from the issuer directly"
        return _static_day(spec, override, d, week, label)
    provider_code = override.alias_of if (override is not None and override.alias_of) else code
    try:
        zone_ok = bool(ZoneInfo(spec.tz))
    except Exception:
        zone_ok = False
    if zone_ok:
        for provider in providers.chain():
            try:
                facts = provider.facts(provider_code, d, spec.tz)
            except Exception as e:                # a broken provider is no answer
                _warn_once(f"{getattr(provider, 'name', provider)}:{type(e).__name__}",
                           f"calendar provider {getattr(provider, 'name', provider)} raised "
                           f"{type(e).__name__}: {e} — falling back")
                facts = None
            if facts is not None:
                ph, why = _facts_day(spec, facts, week)
                if provider_code != code and ph is not None:
                    why = (why + "; " if why else "") + \
                        f"{code} reads {provider_code}'s calendar per owner file ({override.source})"
                return ph, why
    builtin = calendars.get_builtin(code)
    if builtin is not None and d.year in builtin.years:
        return _static_day(spec, builtin, d, week, builtin.source or "built-in calendar")
    if not week:
        return week, "weekend"
    return None, (f"no verified {code} holiday calendar for {d.isoformat()} — refusing "
                  f"rather than guessing (add data/calendars/{code}.json)")


_warned: set = set()


def _warn_once(key: str, msg: str) -> None:
    if key not in _warned:
        _warned.add(key)
        import logging
        logging.getLogger(__name__).warning(msg)


def _zone(spec: VenueSpec) -> Optional[ZoneInfo]:
    try:
        return ZoneInfo(spec.tz)
    except Exception:
        return None


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
    next_close: Optional[str] = None             # ISO UTC: the end of the current or next session
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


def _announced_unknown(spec: VenueSpec, d: date, contract: Optional[str] = None) -> bool:
    """Has the venue's calendar declared `d` a deviation it cannot specify
    (a Muhurat session whose hours are not yet notified)? Then no time of
    that day is known — not even "outside regular hours"."""
    code = contract if spec.holiday_rule == "us_futures" else _calendar_code(spec)
    cal = calendars.get_override(code) if code else None
    return cal is not None and not cal.invalid and d in cal.unknown_days


def venue_state(code: str, at: Optional[datetime] = None,
                contract: Optional[str] = None) -> tuple:
    """(SessionState, reason, phase_end_local | None, trading_day) for a venue.
    `contract` is a futures product group's calendar (venues.FUTURES_CALENDARS)."""
    spec = VENUES.get(code)
    if spec is None:
        return SessionState.UNKNOWN, f"no session spec for venue {code!r}", None, None
    if _zone(spec) is None:
        return SessionState.UNKNOWN, f"invalid timezone {spec.tz!r} for {code}", None, None
    loc = _local(spec, at)
    d = loc.date()
    phases, why = _day(spec, d, contract)
    minute = loc.hour * 60 + loc.minute
    if phases is None:
        # A missing holiday calendar can only REMOVE trading hours, so outside
        # the regular schedule the venue is certainly not trading. Inside it,
        # it might be a holiday: UNKNOWN. (A special session outside regular
        # hours cannot be known without the calendar either — reading that as
        # CLOSED errs towards not trading, the safe side.) A day the calendar
        # itself declares unknown is UNKNOWN at every hour.
        regular = spec.phases(d.weekday())
        if spec.holiday_rule == "calendar" and regular and \
                not _announced_unknown(spec, d) and \
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


def _next_open(spec: VenueSpec, at: datetime, horizon_days: int = 15,
               contract: Optional[str] = None) -> Optional[datetime]:
    loc = _local(spec, at)
    minute = loc.hour * 60 + loc.minute
    state, *_ = venue_state(spec.code, at, contract)
    for k in range(horizon_days):
        d = loc.date() + timedelta(days=k)
        phases, _ = _day(spec, d, contract)
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
                prev, _ = _day(spec, d - timedelta(days=1), contract)
                if isinstance(prev, tuple) and any(q.state == OPEN and q.end == 1440 for q in prev):
                    continue
            return start.astimezone(timezone.utc)
    return None


def session(symbol: str, at: Optional[datetime] = None, *,
            halted: Optional[bool] = None, venue: Optional[str] = None,
            expiry: Optional[date] = None,
            asset_class: Optional[str] = None) -> SessionInfo:
    """The authoritative session for one instrument at one instant.
    `asset_class` is what the caller already knows about the instrument (a
    broker position's class); see resolve()."""
    at = at or datetime.now(timezone.utc)
    inst = resolve(symbol, venue=venue, expiry=expiry, asset_class=asset_class)
    if inst.venue is None:
        return _unknown(inst.symbol, inst, inst.basis or "no venue could be resolved")
    spec = VENUES[inst.venue]
    contract = inst.calendar
    state, why, phase_end, d = venue_state(spec.code, at, contract)
    if _zone(spec) is None:
        return _unknown(inst.symbol, inst, why)
    loc = _local(spec, at)
    verified = spec.holiday_rule == "none" or (d is not None and
                                               _day(spec, d, contract)[0] is not None)
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

    nxt = None if state == SessionState.OPEN else _next_open(spec, at, contract=contract)
    nclose = next_close(spec.code, at, contract=contract) \
        if state != SessionState.UNKNOWN else None
    return SessionInfo(
        symbol=inst.symbol, venue=spec.code, venue_name=spec.name, country=spec.country,
        timezone=spec.tz, asset_class=inst.asset_class, state=state,
        local_time=loc.isoformat(timespec="minutes"),
        trading_day=d.isoformat() if d else None,
        phase_ends_at=phase_end.astimezone(timezone.utc).isoformat() if phase_end else None,
        next_open=nxt.isoformat() if nxt else None,
        next_close=nclose.isoformat() if nclose else None,
        calendar_verified=verified,
        expiry=inst.expiry.isoformat() if inst.expiry else None,
        reasons=reasons)


def is_trading_day(code: str, d: date, contract: Optional[str] = None) -> Optional[bool]:
    spec = VENUES.get(code)
    if spec is None or _zone(spec) is None:
        return None
    phases, _ = _day(spec, d, contract)
    if phases is None:
        return None
    if phases is _HOLIDAY:
        return False
    return any(p.state == OPEN for p in phases)


def session_close(code: str, d: date, contract: Optional[str] = None) -> Optional[datetime]:
    """When trading day `d` ends at `code` — the end of its last OPEN or
    AUCTION phase, early closes included — as an aware datetime in the
    venue's timezone. None for a non-trading or unknown day."""
    spec = VENUES.get(code)
    if spec is None or _zone(spec) is None:
        return None
    phases, _ = _day(spec, d, contract)
    if not isinstance(phases, tuple):
        return None
    ends = [p.end for p in phases if p.state in (OPEN, AUCTION)]
    return _at(spec, d, max(ends)) if ends else None


def next_close(code: str, at: Optional[datetime] = None,
               horizon_days: int = 15, contract: Optional[str] = None) -> Optional[datetime]:
    """When trading next stops (UTC).

    Exchanges and options: the session close of the trading day in progress,
    or of the next one — an intraday lunch break is not a close. Futures and
    FX: the end of the contiguous trading stretch, because their daily
    maintenance break (and the weekend) really does stop trading. Crypto never
    closes (None). None as well when a day on the way is UNKNOWN.
    """
    spec = VENUES.get(code)
    if spec is None or _zone(spec) is None or spec.kind == "crypto":
        return None
    at = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    d = _local(spec, at).date()
    if spec.kind in ("exchange", "options"):
        for k in range(horizon_days):
            dd = d + timedelta(days=k)
            td = is_trading_day(code, dd)
            if td is None:
                return None
            c = session_close(code, dd) if td else None
            if c is not None and c.astimezone(timezone.utc) > at:
                return c.astimezone(timezone.utc)
        return None
    end = None
    for k in range(horizon_days):
        dd = d + timedelta(days=k)
        phases, _ = _day(spec, dd, contract)
        if phases is None:
            return None
        if phases is _HOLIDAY:
            return end
        for p in sorted((p for p in phases if p.state in (OPEN, AUCTION)), key=lambda p: p.start):
            start = _at(spec, dd, p.start).astimezone(timezone.utc)
            stop = _at(spec, dd, p.end).astimezone(timezone.utc)
            if stop <= at:
                continue
            if end is not None and start > end:
                return end                         # the stretch ended at a gap
            end = max(end, stop) if end else stop
    return end


def session_open(code: str, d: date) -> Optional[datetime]:
    spec = VENUES.get(code)
    if spec is None or _zone(spec) is None:
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
    if spec is None or _zone(spec) is None:
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
    if spec is None or _zone(spec) is None:
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
    if spec is None or _zone(spec) is None:
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


# ── where the calendar ends ─────────────────────────────────────────────────

def _merge(spans: list) -> list:
    out = []
    for lo, hi, who in sorted(spans):
        if out and lo <= out[-1][1] + timedelta(days=1):
            plo, phi, pwho = out[-1]
            out[-1] = (plo, max(phi, hi), pwho if who in pwho else f"{pwho} + {who}")
        else:
            out.append((lo, hi, who))
    return out


def calendar_bounds(code: str) -> dict:
    """The deterministic edge of what the engine can say about venue `code`.

    `spans` are the date ranges some provider in the chain covers (owner
    file > exchange_calendars > built-in), merged; `final_known_date` is the
    last covered date and `first_unknown_date` the day after it, from which
    every weekday reads UNKNOWN inside trading hours. `unknown_days` are
    holes the calendar itself declares (an announced session without hours).
    Nothing here extrapolates a holiday pattern past the last covered date.
    """
    spec = VENUES.get(code)
    if spec is None:
        return {"venue": code, "rule": "no session spec", "spans": [],
                "final_known_date": None, "first_unknown_date": None}
    if spec.holiday_rule == "none":
        return {"venue": code, "rule": "no exchange holidays (weekly hours only)",
                "spans": [], "final_known_date": None, "first_unknown_date": None}
    if spec.holiday_rule == "us_futures":
        nyse = calendar_bounds("XNYS")
        last = date.fromisoformat(nyse["final_known_date"]) if nyse["final_known_date"] else None
        # A futures day can need the US calendar for the NEXT day too (an
        # evening session belongs to tomorrow's trade date), so the edge is
        # the last day the engine itself can resolve near the US horizon.
        while last is not None:
            ph, why = _day(spec, last)
            if ph is not None or not why.startswith("no verified US holiday calendar"):
                break
            last -= timedelta(days=1)
        return {"venue": code, "spans": nyse["spans"],
                "rule": ("contract hours; US holidays and their eves are UNKNOWN unless "
                         "the root's contract calendar states the day"),
                "final_known_date": last.isoformat() if last else None,
                "first_unknown_date": (last + timedelta(days=1)).isoformat() if last else None,
                "unknown_days": []}
    cal_code = _calendar_code(spec)
    ov = calendars.get_override(cal_code)
    if ov is not None and ov.invalid:
        return {"venue": code, "calendar": cal_code, "valid": False, "why": ov.invalid,
                "spans": [], "final_known_date": None, "first_unknown_date": None,
                "unknown_days": []}
    spans = []
    if ov is not None:
        spans += [(date(y, 1, 1), date(y, 12, 31), "owner file") for y in ov.years]
    provider_code = ov.alias_of if (ov is not None and ov.alias_of) else cal_code
    for p in providers.chain():
        rng = getattr(p, "verified_range", None)
        if rng is None:
            continue
        try:
            lo, hi = rng(provider_code)
        except Exception:
            continue
        if lo <= hi:
            spans.append((lo, hi, getattr(p, "name", "provider")))
    bi = calendars.get_builtin(cal_code)
    if bi is not None:
        spans += [(date(y, 1, 1), date(y, 12, 31), "built-in") for y in bi.years]
    merged = _merge(spans)
    last = merged[-1][1] if merged else None
    return {"venue": code, "calendar": cal_code,
            "spans": [{"from": a.isoformat(), "to": b.isoformat(), "by": w} for a, b, w in merged],
            "final_known_date": last.isoformat() if last else None,
            "first_unknown_date": (last + timedelta(days=1)).isoformat() if last else None,
            "unknown_days": sorted(d.isoformat() for d in (ov.unknown_days if ov else {}))}
