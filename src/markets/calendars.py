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
     "holidays": ["2026-01-26", {"date": "2026-03-03", "name": "Holi"}],
     "closures": [{"from": "2026-02-15", "to": "2026-02-23", "name": "Spring Festival"}],
     "early_closes": {"2026-12-24": "12:30"},
     "special_sessions": {"2026-11-08": [["OPEN", "18:00", "19:00"]]},
     "unknown_days": {"2026-11-08": "Muhurat trading announced, timings not yet notified"},
     "provenance": [{"ref": "NSE/CMTR/xxxx", "date": "2025-12-12", "url": "..."}]}

`special_sessions` replaces that day's phases entirely (e.g. NSE Muhurat
trading on a Sunday). `closures` is a "from X to Y closed" range, the way
Chinese exchanges publish them. `unknown_days` are deviations the source
announces without the detail needed to model them: the whole day is UNKNOWN,
never a guessed session. `regular_days` (contract calendars only) confirms a
date trades its regular schedule although it is, or precedes, a US holiday.
Nothing in this file is inferred from a rule of thumb.

A file that exists but does not validate — no source, a malformed date or
time, a date outside the years it claims, an unknown key — is INVALID, and an
invalid owner calendar makes its venue UNKNOWN. It never falls through to the
calendar the owner meant to override.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
CALENDAR_DIR = ROOT / "data" / "calendars"

#: Phase states a special session may use (venues.py's phase names).
PHASE_STATES = frozenset({"PRE_OPEN", "OPEN", "BREAK", "AUCTION", "POST_MARKET"})

#: Keys an owner file may carry. Anything else is a typo until proven
#: otherwise ("holiday" for "holidays" would silently drop every closure).
FILE_KEYS = frozenset({
    "source", "years", "holidays", "closures", "early_closes", "special_sessions",
    "unknown_days", "regular_days", "provenance", "alias_of", "segment",
    "retrieved", "notes", "limitations", "cross_checks", "kind", "applies_to",
    "verification"})

#: How far a calendar's dates have been checked against the issuer itself.
#:   verified     every date read from the issuing exchange's own document
#:   provisional  the issuer's document could not be read directly; the dates
#:                come from a faithful copy and are flagged in every answer
VERIFICATION_STATES = ("verified", "provisional")


class CalendarError(ValueError):
    """An owner calendar file that cannot be trusted."""


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
    #: date -> why the day cannot be modelled though the source names it
    #: (announced special session, timings to follow). The day is UNKNOWN.
    unknown_days: dict = field(default_factory=dict)
    #: Contract calendars: dates confirmed to trade the regular schedule.
    regular_days: frozenset = frozenset()
    names: dict = field(default_factory=dict)               # date -> holiday / session name
    provenance: tuple = ()
    #: Set when a file exists but failed validation: the venue is UNKNOWN.
    invalid: Optional[str] = None
    #: "verified" | "provisional" | "" (not stated) — see VERIFICATION_STATES.
    verification: str = ""


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


_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HHMM = re.compile(r"^([01]\d|2[0-4]):([0-5]\d)$")
_CODE = re.compile(r"^[A-Z0-9_]{2,24}$")


def _strict_date(value, where: str) -> date:
    s = value if isinstance(value, str) else None
    if s is None or not _ISO_DAY.match(s):
        raise CalendarError(f"{where}: {value!r} is not a YYYY-MM-DD date")
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise CalendarError(f"{where}: {value!r} is not a real date ({e})") from None


def _minute(value, where: str) -> int:
    m = _HHMM.match(value) if isinstance(value, str) else None
    if m is None or (m.group(1) == "24" and m.group(2) != "00"):
        raise CalendarError(f"{where}: {value!r} is not an HH:MM time")
    return int(m.group(1)) * 60 + int(m.group(2))


def _phases(value, where: str) -> tuple:
    """[[state, "HH:MM", "HH:MM"], ...] -> validated, ordered, non-overlapping."""
    if isinstance(value, dict):
        value = value.get("phases")
    if not isinstance(value, list) or not value:
        raise CalendarError(f"{where}: a special session needs a non-empty list of phases")
    out, last_end = [], -1
    for i, p in enumerate(value):
        if not (isinstance(p, (list, tuple)) and len(p) == 3):
            raise CalendarError(f"{where}[{i}]: a phase is [state, start, end]")
        state, start, end = p
        if state not in PHASE_STATES:
            raise CalendarError(f"{where}[{i}]: unknown phase state {state!r}")
        a, b = _minute(start, f"{where}[{i}]"), _minute(end, f"{where}[{i}]")
        if not a < b:
            raise CalendarError(f"{where}[{i}]: phase ends ({end}) before it starts ({start})")
        if a < last_end:
            raise CalendarError(f"{where}[{i}]: phases overlap or are out of order")
        last_end = b
        out.append((state, start, end))
    if not any(s == "OPEN" for s, _, _ in out):
        raise CalendarError(f"{where}: a special session with no OPEN phase is not a session")
    return tuple(out)


def _named(entry, where: str, key: str = "date") -> tuple:
    """A date given bare ("2026-01-26") or with metadata ({"date": ..., "name": ...})."""
    if isinstance(entry, dict):
        return _strict_date(entry.get(key), where), str(entry.get("name") or "").strip()
    return _strict_date(entry, where), ""


def parse(code: str, raw) -> CalendarData:
    """Validate an owner calendar. Raises CalendarError on anything doubtful."""
    if not isinstance(raw, dict):
        raise CalendarError("the file is not a JSON object")
    unknown = sorted(k for k in raw if k not in FILE_KEYS and not str(k).startswith("_"))
    if unknown:
        raise CalendarError(f"unknown key(s) {unknown} — a typo would silently drop data")
    source = raw.get("source")
    if not isinstance(source, str) or not source.strip():
        raise CalendarError("no `source`: a calendar must say where its dates come from")
    alias = raw.get("alias_of")
    if alias is not None and (not isinstance(alias, str) or not _CODE.match(alias.strip())):
        raise CalendarError(f"alias_of {alias!r} is not a calendar code")
    years_raw = raw.get("years") or []
    if not isinstance(years_raw, list) or not all(
            isinstance(y, int) and not isinstance(y, bool) and 1990 <= y <= 2100 for y in years_raw):
        raise CalendarError(f"years {years_raw!r} must be a list of four-digit years")
    years = frozenset(years_raw)
    if not years and not alias:
        raise CalendarError("no `years`: a calendar must say which years it covers")

    def in_years(d: date, where: str) -> date:
        if d.year not in years:
            raise CalendarError(f"{where}: {d} is outside the years the file covers "
                                f"{sorted(years)}")
        return d

    names: dict = {}
    holidays = set()
    for i, h in enumerate(raw.get("holidays") or []):
        d, name = _named(h, f"holidays[{i}]")
        holidays.add(in_years(d, f"holidays[{i}]"))
        if name:
            names[d] = name
    for i, c in enumerate(raw.get("closures") or []):
        if not isinstance(c, dict):
            raise CalendarError(f"closures[{i}]: a closure is {{from, to, name}}")
        a = in_years(_strict_date(c.get("from"), f"closures[{i}].from"), f"closures[{i}]")
        b = in_years(_strict_date(c.get("to"), f"closures[{i}].to"), f"closures[{i}]")
        if b < a or (b - a).days > 31:
            raise CalendarError(f"closures[{i}]: {a}..{b} is not a plausible closure range")
        d = a
        while d <= b:
            holidays.add(d)
            if c.get("name"):
                names.setdefault(d, str(c["name"]).strip())
            d += timedelta(days=1)
    early = {}
    for d_s, t in (raw.get("early_closes") or {}).items():
        d = in_years(_strict_date(d_s, "early_closes"), "early_closes")
        _minute(t, f"early_closes[{d_s}]")
        early[d] = t
    special = {}
    for d_s, phases in (raw.get("special_sessions") or {}).items():
        d = in_years(_strict_date(d_s, "special_sessions"), "special_sessions")
        special[d] = _phases(phases, f"special_sessions[{d_s}]")
        if isinstance(phases, dict) and phases.get("name"):
            names.setdefault(d, str(phases["name"]).strip())
    unknown_days = {}
    for d_s, why in (raw.get("unknown_days") or {}).items():
        d = in_years(_strict_date(d_s, "unknown_days"), "unknown_days")
        reason = why.get("reason") if isinstance(why, dict) else why
        if not isinstance(reason, str) or not reason.strip():
            raise CalendarError(f"unknown_days[{d_s}]: say why the day cannot be modelled")
        if d in special or d in early:
            raise CalendarError(f"unknown_days[{d_s}]: the day is also given hours — "
                                f"it is either known or it is not")
        unknown_days[d] = reason.strip()
    regular = set()
    for i, r in enumerate(raw.get("regular_days") or []):
        d = in_years(_strict_date(r, f"regular_days[{i}]"), f"regular_days[{i}]")
        if d in holidays or d in special or d in unknown_days or d in early:
            raise CalendarError(f"regular_days[{i}]: {d} is also listed as a deviation")
        regular.add(d)
    prov = raw.get("provenance") or []
    if not isinstance(prov, list):
        raise CalendarError("provenance must be a list of documents")
    ver = raw.get("verification")
    status = (ver.get("status") if isinstance(ver, dict) else ver) if ver is not None else ""
    if status and status not in VERIFICATION_STATES:
        raise CalendarError(f"verification status {status!r} is not one of {VERIFICATION_STATES}")
    return CalendarData(
        code, years, holidays=frozenset(holidays), early_closes=early,
        special_sessions=special, source=source.strip(),
        alias_of=alias.strip() if alias else None, unknown_days=unknown_days,
        regular_days=frozenset(regular), names=names,
        provenance=tuple(p for p in prov if isinstance(p, dict)),
        verification=status or "")


def _from_file(code: str) -> Optional[CalendarData]:
    if code in _file_cache:
        return _file_cache[code]
    path = CALENDAR_DIR / f"{code}.json"
    cal = None
    if path.exists():
        try:
            cal = parse(code, json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:                       # a broken file is not "no file"
            why = f"{type(e).__name__}: {e}" if not isinstance(e, CalendarError) else str(e)
            logger.warning("calendar %s INVALID (%s) — %s reads UNKNOWN until the file "
                           "is fixed", path.name, why, code)
            cal = CalendarData(code, frozenset(), invalid=f"data/calendars/{path.name}: {why}")
    _file_cache[code] = cal
    return cal


def get_override(code: Optional[str]) -> Optional[CalendarData]:
    """An owner-supplied calendar (runtime registration, else
    data/calendars/<CODE>.json). Highest precedence: an exchange circular the
    owner filed is more current than any library release. A file that failed
    validation comes back with `invalid` set — the caller must read UNKNOWN."""
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
    if fileside is not None and fileside.invalid:
        fileside = None                  # the session engine reports it; this is a re-export
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


def _describe_override(ov: CalendarData) -> dict:
    if ov.invalid:
        return {"provider": "owner file", "valid": False, "why": ov.invalid}
    return {"provider": "owner file", "valid": True, "years": sorted(ov.years),
            "source": ov.source, "alias_of": ov.alias_of,
            "verification": ov.verification or "not stated",
            "unknown_days": {d.isoformat(): r for d, r in sorted(ov.unknown_days.items())},
            "documents": [p.get("ref") for p in ov.provenance if p.get("ref")]}


def coverage() -> dict:
    """Which venues have a verified calendar for which dates, from which
    provider — for the UI and the docs, so the gaps are visible rather than
    discovered. Order is the precedence the session engine applies."""
    from src.markets import providers, sessions
    from src.markets.venues import FUTURES_CALENDARS, VENUES
    out = {}
    for spec in VENUES.values():
        if spec.holiday_rule != "calendar":
            continue
        code = spec.calendar or spec.code
        ov, bi = get_override(code), get_builtin(code)
        ov_ok = ov if (ov is not None and not ov.invalid) else None
        out[spec.code] = {
            "calendar": code,
            # the static years (owner file + built-in), as before
            "years": sorted((ov_ok.years if ov_ok else frozenset()) | (bi.years if bi else frozenset())),
            "source": "; ".join(c.source for c in (ov_ok, bi) if c) or None,
            "providers": [x for x in (
                _describe_override(ov) if ov else None,
                *[b for b in (p.bounds(code) for p in providers.chain()) if b],
                {"provider": "built-in", "years": sorted(bi.years), "source": bi.source} if bi else None,
            ) if x],
            "bounds": sessions.calendar_bounds(spec.code),
        }
    # Futures: hours per venue, holidays per CONTRACT calendar (product group).
    for group in sorted(set(FUTURES_CALENDARS.values())):
        ov = get_override(group)
        out[f"contract:{group}"] = {
            "calendar": group,
            "roots": sorted(r for r, g in FUTURES_CALENDARS.items() if g == group),
            "providers": [_describe_override(ov)] if ov else [],
            "rule": ("US holidays and their eves read UNKNOWN unless this contract "
                     "calendar states the day's schedule"),
        }
    return out
