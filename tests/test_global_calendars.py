"""
tests/test_global_calendars.py
==============================
The calendars ARIA did not have after a4413de — NSE, Shenzhen, the India and
China horizon, contract-specific futures holidays — and the failure modes
around owner calendars, all through the one session engine.

NSE and Shenzhen are answered by the files shipped in data/calendars/ (their
`provenance` names every exchange document). The test harness redirects the
data directory to an empty tree, so tests that use them say so explicitly
with the `shipped` fixture; without it NSE and Shenzhen read UNKNOWN, which is
also tested. Expected instants are written by hand from the circulars, never
computed by the engine under test.
"""
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("exchange_calendars")

from src.markets import calendars, providers, sessions as S          # noqa: E402
from src.markets.sessions import SessionState as St                   # noqa: E402
from src.markets.venues import FUTURES_CALENDARS, FUTURES_ROOTS, VENUES   # noqa: E402

UTC = timezone.utc
SHIPPED = ROOT / "data" / "calendars"


def u(*a):
    return datetime(*a, tzinfo=UTC)


def at(tz, *a):
    return datetime(*a, tzinfo=ZoneInfo(tz))


def ist(*a):
    return at("Asia/Kolkata", *a)


def cst(*a):
    return at("Asia/Shanghai", *a)


def ct(*a):
    return at("America/Chicago", *a)


@pytest.fixture(autouse=True)
def _clean_chain():
    providers.default_chain()
    calendars.clear_cache()
    yield
    providers.default_chain()
    calendars.clear_cache()


@pytest.fixture
def shipped(monkeypatch):
    """Read the calendars shipped in data/calendars/ (read-only)."""
    monkeypatch.setattr(calendars, "CALENDAR_DIR", SHIPPED)
    calendars.clear_cache()
    yield
    calendars.clear_cache()


@pytest.fixture
def owner_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(calendars, "CALENDAR_DIR", tmp_path)
    calendars.clear_cache()

    def write(code, payload):
        text = payload if isinstance(payload, str) else json.dumps(payload)
        (tmp_path / f"{code}.json").write_text(text, encoding="utf-8")
        calendars.clear_cache()
    yield write
    calendars.clear_cache()


# ═══ the shipped files themselves ═══════════════════════════════════════════

@pytest.mark.parametrize("code", ["XNSE", "XSHE"])
def test_shipped_calendars_validate_and_carry_provenance(code):
    raw = json.loads((SHIPPED / f"{code}.json").read_text(encoding="utf-8"))
    cal = calendars.parse(code, raw)
    assert cal.source and cal.provenance
    for doc in cal.provenance:
        assert doc.get("url", "").startswith("https://"), doc
        assert doc.get("date") and doc.get("ref"), doc
    assert raw.get("retrieved") == "2026-10-07"


def test_nse_file_matches_the_circulars_day_names():
    """Every weekday holiday transcribed from NSE/CMTR/65587, 71775 and 72260,
    with the weekday the circular prints beside it."""
    printed = {
        "2025-02-26": "Wed", "2025-03-14": "Fri", "2025-03-31": "Mon", "2025-04-10": "Thu",
        "2025-04-14": "Mon", "2025-04-18": "Fri", "2025-05-01": "Thu", "2025-08-15": "Fri",
        "2025-08-27": "Wed", "2025-10-02": "Thu", "2025-10-21": "Tue", "2025-10-22": "Wed",
        "2025-11-05": "Wed", "2025-12-25": "Thu",
        "2026-01-15": "Thu", "2026-01-26": "Mon", "2026-03-03": "Tue", "2026-03-26": "Thu",
        "2026-03-31": "Tue", "2026-04-03": "Fri", "2026-04-14": "Tue", "2026-05-01": "Fri",
        "2026-05-28": "Thu", "2026-06-26": "Fri", "2026-09-14": "Mon", "2026-10-02": "Fri",
        "2026-10-20": "Tue", "2026-11-10": "Tue", "2026-11-24": "Tue", "2026-12-25": "Fri",
    }
    cal = calendars.parse("XNSE", json.loads((SHIPPED / "XNSE.json").read_text(encoding="utf-8")))
    weekday_holidays = {d for d in cal.holidays if d.weekday() < 5}
    assert {d.isoformat() for d in weekday_holidays} == set(printed)
    for d in weekday_holidays:
        assert d.strftime("%a") == printed[d.isoformat()], d


# ═══ NSE (India) ════════════════════════════════════════════════════════════

def test_nse_normal_day_holiday_and_weekend(shipped):
    ok = S.session("RELIANCE.NS", ist(2026, 10, 6, 11, 0))
    assert ok.state == St.OPEN and ok.calendar_verified
    dussehra = S.session("RELIANCE.NS", ist(2026, 10, 20, 11, 0))
    assert dussehra.state == St.HOLIDAY and "NSE" in dussehra.reasons[0]
    # added by NSE/CMTR/72260 three days ahead, in partial modification
    assert S.session("RELIANCE.NS", ist(2026, 1, 15, 11, 0)).state == St.HOLIDAY
    assert S.session("RELIANCE.NS", ist(2026, 10, 10, 11, 0)).state == St.CLOSED


def test_nse_regular_phases(shipped):
    assert S.session("RELIANCE.NS", ist(2026, 10, 6, 9, 5)).state == St.PRE_OPEN
    assert S.session("RELIANCE.NS", ist(2026, 10, 6, 15, 35)).state == St.CLOSED
    assert S.session("RELIANCE.NS", ist(2026, 10, 6, 15, 45)).state == St.POST_MARKET
    assert S.session_close("XNSE", date(2026, 10, 6)) == ist(2026, 10, 6, 15, 30)


def test_nse_muhurat_2025_is_a_special_session_on_a_holiday(shipped):
    """NSE/CMTR/70319: pre-open 13:30-13:45, normal market 13:45-14:45,
    closing session 14:55-15:05 on 21 Oct 2025 (itself a listed holiday)."""
    sym = "RELIANCE.NS"
    assert S.session(sym, ist(2025, 10, 21, 11, 0)).state == St.CLOSED
    assert S.session(sym, ist(2025, 10, 21, 13, 35)).state == St.PRE_OPEN
    live = S.session(sym, ist(2025, 10, 21, 13, 50))
    assert live.state == St.OPEN and live.tradable
    assert live.next_close == ist(2025, 10, 21, 14, 45).astimezone(UTC).isoformat()
    assert S.session(sym, ist(2025, 10, 21, 14, 50)).state == St.CLOSED
    assert S.session(sym, ist(2025, 10, 21, 15, 0)).state == St.POST_MARKET
    # 22 Oct is Balipratipada: the next open after the Muhurat hour is the 23rd
    after = S.session(sym, ist(2025, 10, 21, 16, 0))
    assert after.next_open == ist(2025, 10, 23, 9, 15).astimezone(UTC).isoformat()


def test_nse_muhurat_2026_has_no_hours_yet_so_the_whole_day_is_unknown(shipped):
    """NSE/CMTR/71775: Muhurat on Sunday 8 Nov 2026, 'timings ... shall be
    notified subsequently'. Not CLOSED, not a guessed 18:00 session."""
    for hhmm in ((3, 0), (13, 45), (18, 30), (23, 0)):
        info = S.session("RELIANCE.NS", ist(2026, 11, 8, *hhmm))
        assert info.state == St.UNKNOWN and not info.tradable, hhmm
        assert "Muhurat" in info.reasons[0]
        assert info.next_open is None and info.next_close is None
    # The Saturday before is closed, but nothing can say when trading next opens
    sat = S.session("RELIANCE.NS", ist(2026, 11, 7, 12, 0))
    assert sat.state == St.CLOSED and sat.next_open is None
    assert S.is_trading_day("XNSE", date(2026, 11, 8)) is None


def test_nse_budget_sunday_2026_is_a_live_session(shipped):
    """NSE/CMTR/72349: live session on Sunday 1 Feb 2026 at standard timings."""
    sun = S.session("RELIANCE.NS", ist(2026, 2, 1, 10, 0))
    assert sun.state == St.OPEN
    assert sun.next_close == ist(2026, 2, 1, 15, 30).astimezone(UTC).isoformat()
    sat = S.session("RELIANCE.NS", ist(2026, 1, 31, 12, 0))
    assert sat.next_open == ist(2026, 2, 1, 9, 15).astimezone(UTC).isoformat()


def test_nse_next_open_across_indian_holidays(shipped):
    # Tue 20 Oct 2026 is Dussehra: Monday's after-close next open is Wednesday
    mon = S.session("RELIANCE.NS", ist(2026, 10, 19, 16, 30))
    assert mon.next_open == ist(2026, 10, 21, 9, 15).astimezone(UTC).isoformat()
    # Diwali-Balipratipada Tue 10 Nov 2026
    assert S.session("RELIANCE.NS", ist(2026, 11, 9, 16, 30)).next_open == \
        ist(2026, 11, 11, 9, 15).astimezone(UTC).isoformat()


def test_nse_date_bounds_are_explicit(shipped):
    b = S.calendar_bounds("XNSE")
    assert b["spans"] == [{"from": "2025-01-01", "to": "2026-12-31", "by": "owner file"}]
    assert b["final_known_date"] == "2026-12-31" and b["first_unknown_date"] == "2027-01-01"
    assert b["unknown_days"] == ["2026-11-08"]
    assert S.session("RELIANCE.NS", ist(2026, 12, 31, 11, 0)).state == St.OPEN
    assert S.session("RELIANCE.NS", ist(2027, 1, 1, 11, 0)).state == St.UNKNOWN
    assert S.session("RELIANCE.NS", ist(2027, 3, 2, 11, 0)).state == St.UNKNOWN
    assert S.session("RELIANCE.NS", ist(2024, 6, 4, 11, 0)).state == St.UNKNOWN
    # out of range is UNKNOWN only inside trading hours, CLOSED outside them
    assert S.session("RELIANCE.NS", ist(2027, 3, 2, 21, 0)).state == St.CLOSED


def test_nse_is_not_bse(shipped):
    """Separate exchanges, separate sources, read independently.
    data/calendars/XNSE.json (NSE/CMTR/70319) and data/calendars/XBOM.json
    (BSE Notice 20250922-21) each verify the 2025 Muhurat hour from their own
    exchange's own circular, so the two venues happen to agree there. BSE
    also independently verifies its own Union Budget 2026 Sunday session from
    BSE Notice 20260116-33 — NSE's file makes no claim about that date at
    all, so the two venues agreeing there too is not BSE borrowing NSE's
    file, it is BSE's own notice. The scope/identity checks below prove that:
    XBOM's reason names a BSE notice, never NSE's circular number or XNSE,
    and XNSE's calendar_bounds never mentions XBOM."""
    assert S.session("RELIANCE.NS", ist(2025, 10, 21, 13, 50)).state == St.OPEN
    assert S.session("500325.BO", ist(2025, 10, 21, 13, 50)).state == St.OPEN
    assert S.session("RELIANCE.NS", ist(2026, 2, 1, 10, 0)).state == St.OPEN
    budget = S.session("500325.BO", ist(2026, 2, 1, 10, 0))
    assert budget.state == St.OPEN
    assert "20260116-33" in budget.reasons[0]
    assert "NSE" not in budget.reasons[0] and "CMTR" not in budget.reasons[0]
    assert "XBOM" not in json.dumps(S.calendar_bounds("XNSE"))


def test_without_the_file_nse_is_unknown_again():
    """The harness's empty data tree: nothing answers for NSE."""
    assert S.session("RELIANCE.NS", ist(2026, 10, 6, 11, 0)).state == St.UNKNOWN


# ═══ BSE (India, Muhurat only) ══════════════════════════════════════════════
# data/calendars/XBOM.json carries ONLY the two verified Muhurat sessions
# below (BSE Notices 20241019-1 and 20250922-21) — it is not a BSE holiday
# calendar, so every BSE day it does not name keeps answering from
# exchange_calendars exactly as before that file existed.

def test_bse_shipped_file_validates_and_is_narrow():
    raw = json.loads((SHIPPED / "XBOM.json").read_text(encoding="utf-8"))
    cal = calendars.parse("XBOM", raw)
    assert cal.source and cal.provenance
    for doc in cal.provenance:
        assert doc.get("url", "").startswith("https://"), doc
        assert doc.get("date") and doc.get("ref"), doc
    assert raw.get("retrieved") == "2026-10-16"
    assert cal.years == frozenset()            # claims no year's full holiday list
    assert set(cal.special_sessions) == {date(2024, 11, 1), date(2025, 10, 21), date(2026, 2, 1)}


def test_bse_muhurat_2024_matches_bse_notice_20241019_1(shipped):
    """BSE Notice 20241019-1: pre-open 17:45-18:00, continuous trading
    18:00-19:00, post-closing 19:10-19:20 IST on Fri 1 Nov 2024 — a real
    trading session, not the plain holiday exchange_calendars alone reports."""
    sym = "500325.BO"
    assert S.session(sym, ist(2024, 11, 1, 11, 0)).state == St.CLOSED
    assert S.session(sym, ist(2024, 11, 1, 17, 50)).state == St.PRE_OPEN
    live = S.session(sym, ist(2024, 11, 1, 18, 30))
    assert live.state == St.OPEN and live.tradable
    assert live.next_close == ist(2024, 11, 1, 19, 0).astimezone(UTC).isoformat()
    assert S.session(sym, ist(2024, 11, 1, 19, 5)).state == St.CLOSED
    assert S.session(sym, ist(2024, 11, 1, 19, 15)).state == St.POST_MARKET


def test_bse_muhurat_2025_matches_bse_notice_20250922_21_and_nse(shipped):
    """BSE Notice 20250922-21: pre-open 13:30-13:45, continuous trading
    13:45-14:45, post-closing 14:55-15:05 IST on Tue 21 Oct 2025 — the same
    hour NSE/CMTR/70319 verifies for NSE (data/calendars/XNSE.json), read
    from BSE's own notice rather than copied from NSE's file."""
    sym = "500325.BO"
    assert S.session(sym, ist(2025, 10, 21, 11, 0)).state == St.CLOSED
    assert S.session(sym, ist(2025, 10, 21, 13, 35)).state == St.PRE_OPEN
    live = S.session(sym, ist(2025, 10, 21, 13, 50))
    assert live.state == St.OPEN and live.tradable
    assert live.next_close == ist(2025, 10, 21, 14, 45).astimezone(UTC).isoformat()
    assert S.session(sym, ist(2025, 10, 21, 14, 50)).state == St.CLOSED
    assert S.session(sym, ist(2025, 10, 21, 15, 0)).state == St.POST_MARKET
    assert S.session("RELIANCE.NS", ist(2025, 10, 21, 13, 50)).state == live.state == St.OPEN


def test_bse_budget_2026_matches_bse_notice_20260116_33(shipped):
    """BSE Notice 20260116-33: pre-open 9:00-9:15am, continuous trading
    9:15am-3:30pm, closing 3:30-3:40pm, post-closing 3:40-4:00pm IST on
    Sunday 1 Feb 2026 for the Union Budget 2026 presentation — read from
    BSE's own notice, not inferred from NSE or from BSE's Muhurat precedent."""
    sym = "500325.BO"
    assert S.session(sym, ist(2026, 2, 1, 8, 50)).state == St.CLOSED
    assert S.session(sym, ist(2026, 2, 1, 9, 5)).state == St.PRE_OPEN
    live = S.session(sym, ist(2026, 2, 1, 10, 0))
    assert live.state == St.OPEN and live.tradable
    assert live.next_close == ist(2026, 2, 1, 15, 30).astimezone(UTC).isoformat()
    assert S.session(sym, ist(2026, 2, 1, 15, 35)).state == St.CLOSED
    assert S.session(sym, ist(2026, 2, 1, 15, 50)).state == St.POST_MARKET
    assert S.session(sym, ist(2026, 2, 1, 16, 5)).state == St.CLOSED


def test_bse_budget_sunday_opens_only_that_window_and_other_sundays_dont(shipped):
    """The Union Budget 2026 session opens Sunday 2026-02-01 and only that
    window (09:15-15:30); this file claims nothing about any other Sunday,
    so the Sundays immediately either side of it stay whatever the provider
    says for an ordinary BSE Sunday (closed, never a guessed open)."""
    from src.markets.candle_sessions import session_windows
    w = session_windows("500325.BO", date(2026, 2, 1))
    assert w["status"] == "verified"
    assert w["windows"] == [{"open": ist(2026, 2, 1, 9, 15), "close": ist(2026, 2, 1, 15, 30)}]
    # the one OPEN window is entirely inside 2026-02-01 local time — a
    # same-day span an aggregator can bin whole, never one that rolls past
    # local midnight into the next calendar date.
    assert w["windows"][0]["open"].date() == w["windows"][0]["close"].date() == date(2026, 2, 1)
    for adjacent in (date(2026, 1, 25), date(2026, 2, 8)):
        assert S.session("500325.BO", ist(adjacent.year, adjacent.month,
                                          adjacent.day, 10, 0)).state != St.OPEN


def test_bse_ordinary_days_still_come_from_the_provider(shipped):
    """XBOM.json does not list 2024 or 2025 under `years`, so every day it
    does not name stays exchange_calendars' answer, unchanged by this file's
    existence — a real BSE holiday must not become a false "open"."""
    independence_day = S.session("500325.BO", ist(2025, 8, 15, 11, 0))
    assert independence_day.state == St.HOLIDAY and "XBOM" in independence_day.reasons[0]
    ordinary = S.session("500325.BO", ist(2025, 1, 2, 11, 0))
    assert ordinary.state == St.OPEN and ordinary.calendar_verified
    assert S.session("500325.BO", ist(2024, 1, 26, 11, 0)).state == St.HOLIDAY


def test_bse_calendar_bounds_show_the_provider_not_a_fake_owner_span(shipped):
    """years=[] must not credit "owner file" with 2024 or 2025 in
    calendar_bounds — exchange_calendars' own verified range is what
    actually covers them, as it did before this file existed."""
    b = S.calendar_bounds("XBOM")
    assert all(span["by"] != "owner file" for span in b["spans"])
    assert b["unknown_days"] == []


def test_bse_session_windows_helper_agrees_with_session(shipped):
    """TECH-09's session_windows() calls the same _day() session() does; it
    must report the same verified Muhurat window for both dates, not a
    separately-derived answer."""
    from src.markets.candle_sessions import session_windows
    w2024 = session_windows("500325.BO", date(2024, 11, 1))
    assert w2024["status"] == "verified"
    assert w2024["windows"] == [{"open": ist(2024, 11, 1, 18, 0), "close": ist(2024, 11, 1, 19, 0)}]
    w2025 = session_windows("500325.BO", date(2025, 10, 21))
    assert w2025["status"] == "verified"
    assert w2025["windows"] == [{"open": ist(2025, 10, 21, 13, 45), "close": ist(2025, 10, 21, 14, 45)}]
    assert S.session("500325.BO", ist(2025, 10, 21, 13, 50)).state == St.OPEN
    holiday_window = session_windows("500325.BO", date(2025, 8, 15))
    assert holiday_window == {"status": "closed", "venue": "XBOM", "timezone": "Asia/Kolkata",
                              "reason": holiday_window["reason"], "windows": []}
    assert "XBOM holiday" in holiday_window["reason"]


# ═══ Shenzhen (China) ═══════════════════════════════════════════════════════

def test_shenzhen_day_open_lunch_open_close(shipped):
    """OPEN -> BREAK -> OPEN -> AUCTION -> CLOSED, and the lunch break is not
    the close."""
    sym = "000001.SZ"
    seq = [((9, 20), St.PRE_OPEN), ((10, 0), St.OPEN), ((12, 0), St.BREAK),
           ((13, 30), St.OPEN), ((14, 58), St.AUCTION), ((15, 5), St.CLOSED)]
    for hhmm, state in seq:
        assert S.session(sym, cst(2026, 10, 9, *hhmm)).state == state, hhmm
    morning = S.session(sym, cst(2026, 10, 9, 10, 0))
    lunch_start = cst(2026, 10, 9, 11, 30).astimezone(UTC).isoformat()
    day_close = cst(2026, 10, 9, 15, 0).astimezone(UTC).isoformat()
    assert morning.next_close == day_close and morning.next_close != lunch_start
    assert morning.phase_ends_at == lunch_start          # the PHASE ends at lunch
    lunch = S.session(sym, cst(2026, 10, 9, 12, 0))
    assert not lunch.tradable
    assert lunch.next_open == cst(2026, 10, 9, 13, 0).astimezone(UTC).isoformat()
    assert lunch.next_close == day_close


def test_shenzhen_spring_festival_and_golden_week(shipped):
    for d in (16, 17, 18, 19, 20, 23):
        assert S.session("000001.SZ", cst(2026, 2, d, 10, 0)).state == St.HOLIDAY, d
    assert S.session("000001.SZ", cst(2026, 2, 24, 10, 0)).state == St.OPEN
    fri = S.session("000001.SZ", cst(2026, 2, 13, 16, 0))
    assert fri.next_open == cst(2026, 2, 24, 9, 30).astimezone(UTC).isoformat()
    for d in (1, 2, 5, 6, 7):
        assert S.session("000001.SZ", cst(2026, 10, d, 10, 0)).state == St.HOLIDAY, d
    assert S.session("000001.SZ", cst(2026, 10, 8, 10, 0)).state == St.OPEN
    assert S.session("000001.SZ", cst(2026, 9, 30, 16, 0)).next_open == \
        cst(2026, 10, 8, 9, 30).astimezone(UTC).isoformat()


def test_make_up_working_weekends_are_not_trading_days(shipped):
    """China's 调休 Saturdays are official working days; the exchange is shut."""
    for d in (date(2026, 2, 14), date(2026, 2, 28), date(2026, 5, 9), date(2026, 10, 10)):
        assert S.session("000001.SZ", cst(d.year, d.month, d.day, 10, 0)).state == St.CLOSED, d
        assert S.is_trading_day("XSHE", d) is False


def test_shenzhen_bounds_and_2027(shipped):
    b = S.calendar_bounds("XSHE")
    assert b["final_known_date"] == "2026-12-31" and b["first_unknown_date"] == "2027-01-01"
    assert S.session("000001.SZ", cst(2026, 12, 31, 10, 0)).state == St.OPEN
    assert S.session("000001.SZ", cst(2027, 1, 4, 10, 0)).state == St.UNKNOWN
    assert S.session("000001.SZ", cst(2025, 6, 3, 10, 0)).state == St.UNKNOWN


def test_shenzhen_and_shanghai_agree_in_2026_without_being_aliased(shipped):
    """A consistency check between two independent sources (the SZSE notice,
    exchange_calendars' Shanghai data) — not a mapping."""
    for d in pd.bdate_range("2026-01-01", "2026-12-31"):
        dd = d.date()
        assert S.is_trading_day("XSHE", dd) == S.is_trading_day("XSHG", dd), dd
    assert "owner file" in S.calendar_bounds("XSHE")["spans"][0]["by"]


def test_without_the_file_shenzhen_is_unknown_even_though_shanghai_answers():
    assert S.session("000001.SZ", cst(2026, 10, 9, 10, 0)).state == St.UNKNOWN
    assert S.session("600519.SS", cst(2026, 10, 9, 10, 0)).state == St.OPEN


# ═══ the India / China horizon ══════════════════════════════════════════════

@pytest.mark.parametrize("code,sym,tz", [("XBOM", "500325.BO", "Asia/Kolkata"),
                                         ("XSHG", "600519.SS", "Asia/Shanghai")])
def test_library_india_and_china_end_at_2026(code, sym, tz):
    b = S.calendar_bounds(code)
    assert b["final_known_date"] == "2026-12-31" and b["first_unknown_date"] == "2027-01-01"
    assert S.session(sym, at(tz, 2026, 12, 31, 11, 0)).state == St.OPEN
    jan4 = S.session(sym, at(tz, 2027, 1, 4, 11, 0))
    assert jan4.state == St.UNKNOWN and not jan4.tradable
    # no extrapolation: the last known day's next open is unknown, not 2027-01-04
    assert S.session(sym, at(tz, 2026, 12, 31, 20, 0)).next_open is None


def test_no_2027_india_or_china_data_was_invented(shipped):
    for code in ("XNSE", "XBOM", "XSHG", "XSHE"):
        assert S.calendar_bounds(code)["first_unknown_date"] == "2027-01-01", code


# ═══ provider edges ═════════════════════════════════════════════════════════

@pytest.mark.parametrize("sym,tz", [("7203.T", "Asia/Tokyo"), ("SAP.DE", "Europe/Berlin"),
                                    ("NESN.SW", "Europe/Zurich")])
def test_a_holiday_on_the_last_day_of_the_range_is_a_holiday_not_unknown(sym, tz):
    """The library refuses dates after its last SESSION even inside the range
    it was built for, so 31 Dec 2027 (a holiday at all three) used to read
    UNKNOWN."""
    assert S.session(sym, at(tz, 2027, 12, 31, 10, 0)).state == St.HOLIDAY
    assert S.session(sym, at(tz, 2027, 12, 30, 10, 0)).state == St.OPEN


def test_provider_bounds_report_the_range_not_the_last_session():
    b = providers.ExchangeCalendarsProvider().bounds("XTKS")
    assert b["to"] == "2027-12-31" and b["last_session"] == "2027-12-30"
    assert providers.ExchangeCalendarsProvider().bounds("XBOM")["to"] == "2026-12-31"


# ═══ US futures: contract calendars ════════════════════════════════════════

def test_every_future_root_has_a_contract_calendar_and_cme_groups_are_distinct():
    assert set(FUTURES_ROOTS) == set(FUTURES_CALENDARS)
    groups = {FUTURES_CALENDARS[r] for r in ("ES", "CL", "GC", "ZN", "6E", "BTC", "ZC")}
    assert len(groups) == 7, groups          # one CME calendar does not cover all CME products
    assert S.resolve("ES=F").calendar == "CME_EQUITY"
    assert S.resolve("ZN=F").calendar == "CME_RATES"


#: Shaped like a CME holiday schedule. NOT CME data: it exists to prove the
#: mechanics, and nothing like it ships in data/calendars/.
FIXTURE = calendars.CalendarData(
    "CME_EQUITY", frozenset({2026}),
    holidays=frozenset({date(2026, 12, 25)}),
    special_sessions={
        date(2026, 11, 26): (("OPEN", "00:00", "12:00"), ("OPEN", "17:00", "24:00")),
        date(2026, 11, 27): (("OPEN", "00:00", "12:15"),),
        date(2026, 12, 24): (("OPEN", "00:00", "12:15"),),
    },
    regular_days=frozenset({date(2026, 11, 25)}),
    source="TEST FIXTURE - not CME data")


@pytest.fixture
def cme_equity_fixture():
    calendars.register(FIXTURE)
    yield FIXTURE
    calendars.register(None, "CME_EQUITY")


def test_futures_without_a_contract_calendar_keep_us_holidays_unknown():
    for sym in ("ES=F", "CL=F", "ZN=F", "ZC=F", "KC=F"):
        info = S.session(sym, ct(2026, 11, 26, 11, 0))
        assert info.state == St.UNKNOWN and not info.tradable, sym
    assert "no CME_EQUITY contract calendar" in S.session("ES=F", ct(2026, 11, 26, 11, 0)).reasons[0]


def test_a_contract_calendar_answers_for_its_own_group_only(cme_equity_fixture):
    assert S.session("ES=F", ct(2026, 11, 26, 11, 0)).state == St.OPEN
    assert S.session("MES=F", ct(2026, 11, 26, 11, 0)).state == St.OPEN
    # crude oil and Treasuries keep their own (absent) calendars
    assert S.session("CL=F", ct(2026, 11, 26, 11, 0)).state == St.UNKNOWN
    assert S.session("ZN=F", ct(2026, 11, 26, 11, 0)).state == St.UNKNOWN


def test_abbreviated_holiday_sessions_and_next_open(cme_equity_fixture):
    halted = S.session("ES=F", ct(2026, 11, 26, 13, 0))
    assert halted.state == St.CLOSED
    assert halted.next_open == ct(2026, 11, 26, 17, 0).astimezone(UTC).isoformat()
    assert S.session("ES=F", ct(2026, 11, 26, 11, 0)).next_close == \
        ct(2026, 11, 26, 12, 0).astimezone(UTC).isoformat()
    assert S.session("ES=F", ct(2026, 11, 25, 20, 0)).state == St.OPEN   # confirmed regular eve
    fri = S.session("ES=F", ct(2026, 11, 27, 12, 30))
    assert fri.state == St.CLOSED
    assert fri.next_open == ct(2026, 11, 29, 17, 0).astimezone(UTC).isoformat()


def test_next_session_across_a_contract_holiday(cme_equity_fixture):
    assert S.session("ES=F", ct(2026, 12, 25, 10, 0)).state == St.HOLIDAY
    eve = S.session("ES=F", ct(2026, 12, 24, 13, 0))
    assert eve.state == St.CLOSED
    assert eve.next_open == ct(2026, 12, 27, 17, 0).astimezone(UTC).isoformat()


def test_a_us_holiday_the_contract_calendar_is_silent_about_is_unknown(cme_equity_fixture):
    good_friday = S.session("ES=F", ct(2026, 4, 3, 10, 0))
    assert good_friday.state == St.UNKNOWN and "does not state" in good_friday.reasons[0]
    assert S.session("ES=F", ct(2026, 4, 2, 20, 0)).state == St.UNKNOWN   # its eve
    # 2027 is outside the fixture: the default rule applies again
    assert S.session("ES=F", ct(2027, 11, 25, 11, 0)).state == St.UNKNOWN


def test_an_early_close_alone_is_not_enough_for_a_future():
    calendars.register(calendars.CalendarData(
        "CME_EQUITY", frozenset({2026}),
        early_closes={date(2026, 11, 27): "12:15"}, source="TEST FIXTURE"))
    try:
        info = S.session("ES=F", ct(2026, 11, 27, 10, 0))
        assert info.state == St.UNKNOWN and "special_sessions" in info.reasons[0]
    finally:
        calendars.register(None, "CME_EQUITY")


def test_futures_regular_week_break_and_overnight():
    over = S.session("ES=F", ct(2026, 10, 6, 2, 0))
    assert over.state == St.OPEN
    assert over.next_close == ct(2026, 10, 6, 16, 0).astimezone(UTC).isoformat()
    brk = S.session("ES=F", ct(2026, 10, 6, 16, 30))
    assert brk.state == St.BREAK and not brk.tradable
    assert brk.next_open == ct(2026, 10, 6, 17, 0).astimezone(UTC).isoformat()
    assert S.session("ZC=F", ct(2026, 10, 6, 8, 0)).state == St.BREAK     # grains 07:45-08:30


def test_futures_year_boundaries():
    assert S.session("ES=F", ct(2026, 12, 31, 18, 0)).state == St.UNKNOWN   # eve of 1 Jan
    assert S.session("ES=F", ct(2027, 12, 30, 20, 0)).state == St.OPEN
    # Fri 31 Dec 2027 has no evening session and Saturday is certainly shut,
    # so the day is known; the first session on a 2028 trade date is Sunday
    # 2 Jan's evening, which no US calendar covers.
    assert S.session("ES=F", ct(2027, 12, 31, 10, 0)).state == St.OPEN
    after = S.session("ES=F", ct(2027, 12, 31, 16, 30))
    assert after.state == St.CLOSED and after.next_open is None     # cannot see into 2028
    assert S.session("ES=F", ct(2028, 1, 2, 18, 0)).state == St.UNKNOWN
    b = S.calendar_bounds("CME_GLOBEX")
    assert (b["final_known_date"], b["first_unknown_date"]) == ("2027-12-31", "2028-01-01")


def test_invalid_or_unmodelled_contracts():
    assert S.session("XX=F", u(2026, 10, 6, 15, 0)).state == St.UNKNOWN
    es = S.resolve("ES", asset_class="futures")
    assert (es.venue, es.calendar) == ("CME_GLOBEX", "CME_EQUITY")
    assert S.resolve("ESZ6", asset_class="futures").venue is None   # a month code is not a root
    assert S.session("ESZ6", u(2026, 10, 6, 15, 0), asset_class="futures").state == St.UNKNOWN


def test_an_invalid_contract_calendar_file_is_unknown(owner_dir):
    owner_dir("CME_EQUITY", {"source": "x", "years": [2026], "holidays": ["2026-13-40"]})
    info = S.session("ES=F", ct(2026, 10, 6, 10, 0))
    assert info.state == St.UNKNOWN and "invalid" in info.reasons[0]
    assert S.session("CL=F", ct(2026, 10, 6, 10, 0)).state == St.OPEN    # other groups unaffected


# ═══ owner calendar files: validation and failure modes ════════════════════

BAD_FILES = [
    ("no source", {"years": [2026], "holidays": []}),
    ("empty source", {"source": " ", "years": [2026]}),
    ("no years", {"source": "s"}),
    ("bad year", {"source": "s", "years": ["2026"]}),
    ("bad date", {"source": "s", "years": [2026], "holidays": ["2026-02-30"]}),
    ("not a date", {"source": "s", "years": [2026], "holidays": ["26/01/2026"]}),
    ("outside its years", {"source": "s", "years": [2026], "holidays": ["2027-01-01"]}),
    ("typo key", {"source": "s", "years": [2026], "holiday": ["2026-01-26"]}),
    ("bad time", {"source": "s", "years": [2026], "early_closes": {"2026-12-24": "12h30"}}),
    ("bad state", {"source": "s", "years": [2026],
                   "special_sessions": {"2026-11-08": [["TRADING", "18:00", "19:00"]]}}),
    ("backwards phase", {"source": "s", "years": [2026],
                         "special_sessions": {"2026-11-08": [["OPEN", "19:00", "18:00"]]}}),
    ("overlap", {"source": "s", "years": [2026], "special_sessions": {
        "2026-11-08": [["PRE_OPEN", "17:45", "18:05"], ["OPEN", "18:00", "19:00"]]}}),
    ("no open phase", {"source": "s", "years": [2026],
                       "special_sessions": {"2026-11-08": [["PRE_OPEN", "17:45", "18:00"]]}}),
    ("unknown day without a reason", {"source": "s", "years": [2026],
                                      "unknown_days": {"2026-11-08": ""}}),
    ("unknown day with hours", {"source": "s", "years": [2026],
                                "unknown_days": {"2026-11-08": "tba"},
                                "special_sessions": {"2026-11-08": [["OPEN", "18:00", "19:00"]]}}),
    ("closure backwards", {"source": "s", "years": [2026],
                           "closures": [{"from": "2026-10-07", "to": "2026-10-01"}]}),
    ("not an object", ["2026-01-26"]),
    ("empty years, no special session or unknown day to justify it",
     {"source": "s", "years": []}),
]


@pytest.mark.parametrize("why,payload", BAD_FILES, ids=[b[0] for b in BAD_FILES])
def test_a_malformed_owner_calendar_is_refused(why, payload):
    with pytest.raises(calendars.CalendarError):
        calendars.parse("XHKG", payload)


@pytest.mark.parametrize("payload", [
    {"years": [2026], "holidays": []},                                   # no source
    {"source": "s", "years": [2026], "holidays": ["2026-02-30"]},        # malformed
    "{ this is not json",                                                 # corrupt
])
def test_an_invalid_owner_file_makes_its_venue_unknown_not_the_library(owner_dir, payload):
    """The owner meant to override HKEX's calendar. A broken override must
    not silently hand the decision back to the library."""
    owner_dir("XHKG", payload)
    inside = S.session("0005.HK", at("Asia/Hong_Kong", 2026, 10, 6, 10, 0))
    assert inside.state == St.UNKNOWN and not inside.tradable
    assert "invalid" in inside.reasons[0]
    assert S.session("0005.HK", at("Asia/Hong_Kong", 2026, 10, 6, 22, 0)).state == St.CLOSED
    assert S.session("0005.HK", at("Asia/Hong_Kong", 2026, 10, 10, 10, 0)).state == St.CLOSED
    cov = calendars.coverage()["XHKG"]
    assert cov["providers"][0]["valid"] is False
    assert S.calendar_bounds("XHKG")["first_unknown_date"] is None


def test_a_valid_owner_file_with_ranges_and_named_days(owner_dir):
    owner_dir("XHKG", {"source": "test circular", "years": [2026],
                       "closures": [{"from": "2026-10-05", "to": "2026-10-06", "name": "test"}],
                       "holidays": [{"date": "2026-10-08", "name": "named"}]})
    for d in (5, 6, 8):
        assert S.session("0005.HK", at("Asia/Hong_Kong", 2026, 10, d, 10, 0)).state == St.HOLIDAY
    assert S.session("0005.HK", at("Asia/Hong_Kong", 2026, 10, 7, 10, 0)).state == St.OPEN


def test_a_special_session_only_file_does_not_need_years(owner_dir):
    """A file that states one verified special session needs no `years`: it
    is not claiming to be that venue's holiday calendar, just one fact a
    notice confirms — the mechanism data/calendars/XBOM.json uses for its
    two Muhurat sessions (calendars.py's module docstring)."""
    owner_dir("XHKG", {"source": "test notice",
                       "special_sessions": {"2026-10-07": [["OPEN", "10:00", "11:00"]]}})
    narrow = S.session("0005.HK", at("Asia/Hong_Kong", 2026, 10, 7, 10, 30))
    assert narrow.state == St.OPEN and narrow.tradable
    # every other HKEX day, including ones this file never mentions, still
    # comes from the library exactly as if the file did not exist
    assert S.session("0005.HK", at("Asia/Hong_Kong", 2026, 10, 6, 10, 0)).state == St.OPEN
    lny = S.session("0005.HK", at("Asia/Hong_Kong", 2026, 2, 17, 10, 0))      # Lunar New Year
    assert lny.state == St.HOLIDAY


def test_special_session_date_need_not_be_inside_years():
    """A `years`-bearing file can still carry a special session for a year
    outside it — the two fields are independent claims."""
    cal = calendars.parse("XHKG", {"source": "t", "years": [2025],
                                   "special_sessions": {"2030-05-01": [["OPEN", "09:00", "10:00"]]}})
    assert cal.years == frozenset({2025})
    assert date(2030, 5, 1) in cal.special_sessions


def test_a_stale_owner_file_is_not_extrapolated(owner_dir):
    """A file for 2025 says nothing about 2026; the library answers 2026 for
    HKEX, and for a venue with no library (Shenzhen) 2026 is UNKNOWN."""
    owner_dir("XSHE", {"source": "test", "years": [2025], "holidays": ["2025-10-01"]})
    assert S.session("000001.SZ", cst(2026, 10, 9, 10, 0)).state == St.UNKNOWN
    assert S.session("000001.SZ", cst(2025, 10, 1, 10, 0)).state == St.HOLIDAY


def test_an_invalid_timezone_on_an_owner_file_venue(shipped, monkeypatch):
    from dataclasses import replace
    monkeypatch.setitem(VENUES, "XNSE", replace(VENUES["XNSE"], tz="Asia/Atlantis"))
    info = S.session("RELIANCE.NS", u(2026, 10, 6, 5, 30))
    assert info.state == St.UNKNOWN and "timezone" in info.reasons[0]


def test_owner_files_still_answer_when_the_library_is_missing(shipped, monkeypatch):
    monkeypatch.setitem(sys.modules, "exchange_calendars", None)
    providers.set_chain([providers.ExchangeCalendarsProvider()])
    assert S.session("RELIANCE.NS", ist(2026, 10, 6, 11, 0)).state == St.OPEN
    assert S.session("000001.SZ", cst(2026, 10, 9, 10, 0)).state == St.OPEN
    assert S.session("0005.HK", at("Asia/Hong_Kong", 2026, 10, 6, 10, 0)).state == St.UNKNOWN
    assert S.session("AAPL", at("America/New_York", 2026, 11, 26, 11, 0)).state == St.HOLIDAY


# ═══ session semantics: providers answer days, the engine keeps phases ════

def test_every_state_means_what_it_says():
    ny = "America/New_York"
    cases = [
        ("BARC.L", at("Europe/London", 2026, 10, 6, 7, 55), St.PRE_OPEN),
        ("BARC.L", at("Europe/London", 2026, 10, 6, 11, 0), St.OPEN),
        ("BARC.L", at("Europe/London", 2026, 10, 6, 16, 32), St.AUCTION),
        ("7203.T", at("Asia/Tokyo", 2026, 10, 6, 12, 0), St.BREAK),
        ("AAPL", at(ny, 2026, 10, 6, 16, 30), St.POST_MARKET),
        ("AAPL", at(ny, 2026, 4, 3, 11, 0), St.HOLIDAY),
        ("AAPL", at(ny, 2026, 10, 10, 11, 0), St.CLOSED),
        ("ENI.MI", u(2026, 10, 6, 10, 0), St.UNKNOWN),
    ]
    for sym, when, state in cases:
        info = S.session(sym, when)
        assert info.state == state, (sym, when, info.reasons)
        assert info.tradable is (state == St.OPEN)
    halted = S.session("AAPL", at(ny, 2026, 10, 6, 11, 0), halted=True)
    assert halted.state == St.HALTED and not halted.tradable


def test_an_auction_is_not_continuous_trading_and_a_lunch_is_not_a_close():
    auction = S.session("BARC.L", at("Europe/London", 2026, 10, 6, 16, 32))
    assert auction.next_close == at("Europe/London", 2026, 10, 6, 16, 35).astimezone(UTC).isoformat()
    lunch = S.session("7203.T", at("Asia/Tokyo", 2026, 10, 6, 12, 0))
    assert lunch.next_close == at("Asia/Tokyo", 2026, 10, 6, 15, 30).astimezone(UTC).isoformat()
    assert lunch.next_open == at("Asia/Tokyo", 2026, 10, 6, 12, 30).astimezone(UTC).isoformat()
    hk_lunch = S.session("0005.HK", at("Asia/Hong_Kong", 2026, 10, 6, 12, 30))
    assert hk_lunch.state == St.BREAK
    assert hk_lunch.next_close == at("Asia/Hong_Kong", 2026, 10, 6, 16, 10).astimezone(UTC).isoformat()


def test_a_holiday_next_open_is_the_next_trading_day_not_later_today():
    gf = S.session("AAPL", at("America/New_York", 2026, 4, 3, 8, 0))     # Good Friday morning
    assert gf.state == St.HOLIDAY
    assert gf.next_open == at("America/New_York", 2026, 4, 6, 9, 30).astimezone(UTC).isoformat()


def test_library_backed_days_keep_the_venues_pre_open_and_auctions():
    assert S.session("0005.HK", at("Asia/Hong_Kong", 2026, 10, 6, 9, 15)).state == St.PRE_OPEN
    assert S.session("0005.HK", at("Asia/Hong_Kong", 2026, 10, 6, 16, 5)).state == St.AUCTION
    assert S.session("SAP.DE", at("Europe/Berlin", 2026, 10, 6, 17, 32)).state == St.AUCTION
    assert S.session("BHP.AX", at("Australia/Sydney", 2026, 10, 6, 16, 5)).state == St.AUCTION


def test_unknown_stays_unknown_everywhere_it_surfaces():
    info = S.session("AAPL", u(2031, 3, 4, 15, 0))
    assert info.state == St.UNKNOWN
    assert (info.next_open, info.next_close, info.tradable) == (None, None, False)
    assert S.is_trading_day("XNYS", date(2031, 3, 4)) is None
    assert S.session_close("XNYS", date(2031, 3, 4)) is None
    assert S.bar_available_at("XNYS", date(2031, 3, 4)) is None


# ═══ asset-class-aware resolution ═══════════════════════════════════════════

@pytest.mark.parametrize("sym,ac,venue", [
    ("BTCUSD", "crypto", "CRYPTO"),       # how Alpaca reports a crypto position
    ("ETHUSDT", "crypto", "CRYPTO"),
    ("BTC-USD", None, "CRYPTO"),
    ("BTCUSD", None, "XNYS"),             # a bare ticker with no class is a US listing
    ("SPY", "crypto", None),              # a label contradicting the symbol: no venue
    ("COIN", "crypto", None),
    ("EURUSD", "forex", "FX"),
    ("EURUSD=X", "equity", "FX"),         # the shape wins over the label
    ("AAPL", "equity", "XNYS"),
    ("AAPL", "option", None),             # an option needs its OCC symbol
])
def test_resolution_uses_a_known_asset_class_only_to_settle_format(sym, ac, venue):
    assert S.resolve(sym, asset_class=ac).venue == venue


def test_a_crypto_pair_trades_on_a_saturday_and_a_mislabelled_equity_does_not():
    sat = u(2026, 10, 10, 15, 0)
    assert S.session("BTCUSD", sat, asset_class="crypto").tradable
    assert not S.session("SPY", sat, asset_class="crypto").tradable


# ═══ performance contract ═══════════════════════════════════════════════════

def test_the_library_stays_lazy_and_owner_files_do_not_need_it():
    """Importing the engine loads neither exchange_calendars nor pandas, and
    an NSE or Shenzhen answer (owner files) never loads the library."""
    import subprocess
    code = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "import src.markets.sessions as S, src.markets.calendars as C\n"
        "assert 'exchange_calendars' not in sys.modules and 'pandas' not in sys.modules\n"
        "from pathlib import Path; from datetime import datetime, timezone\n"
        "C.CALENDAR_DIR = Path(r'%s'); C.clear_cache()\n"
        "assert S.session('RELIANCE.NS', datetime(2026,10,6,5,30,tzinfo=timezone.utc)).state.value == 'OPEN'\n"
        "assert S.session('000001.SZ', datetime(2026,10,9,2,0,tzinfo=timezone.utc)).state.value == 'OPEN'\n"
        "assert 'exchange_calendars' not in sys.modules\n"
    ) % (ROOT, SHIPPED)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]


def test_warm_lookups_stay_well_under_a_millisecond(shipped):
    import time
    t = u(2026, 10, 6, 15, 0)
    for sym in ("AAPL", "7203.T", "RELIANCE.NS", "ES=F"):
        S.session(sym, t)                                   # warm
    n = 300
    t0 = time.perf_counter()
    for _ in range(n):
        for sym in ("AAPL", "7203.T", "RELIANCE.NS", "ES=F"):
            S.session(sym, t)
    per_call = (time.perf_counter() - t0) / (4 * n)
    assert per_call < 1e-3, f"{per_call * 1e6:.0f} us per session()"
