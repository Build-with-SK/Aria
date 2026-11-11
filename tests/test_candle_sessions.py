"""
tests/test_candle_sessions.py
==============================
src/markets/candle_sessions.py: the OPEN-only window helper a session-
anchored multi-hour candle aggregator reads calendar facts through.

Dates are real 2026 dates read from the same built-in / exchange_calendars
facts src/markets/sessions.py itself uses (verified by printing
sessions._day() for each fixture before writing its assertion) — no network
access, no new fixtures beyond what sessions.py already ships.
"""
import sys
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.markets import calendars
from src.markets.candle_sessions import session_windows


@pytest.fixture
def xnse_calendar():
    """Test isolation (tests/conftest.py) redirects data/calendars/ away
    from the owner's real files, so XNSE/XBOM need a registered fixture
    calendar here, the same way tests/test_market_sessions.py supplies one."""
    calendars.register(calendars.CalendarData(
        "XNSE", frozenset({2026}), source="test fixture"))
    yield
    calendars.register(None, "XNSE")


def _hm(dt) -> str:
    return dt.strftime("%H:%M")


# ── a normal day: exactly the continuous phase, nothing else ───────────────

def test_nyse_normal_day_is_one_window_930_to_1600():
    r = session_windows("AAPL", date(2026, 10, 19))          # a Monday
    assert r["status"] == "verified"
    assert r["venue"] == "XNYS"
    assert r["timezone"] == "America/New_York"
    assert len(r["windows"]) == 1
    w = r["windows"][0]
    assert _hm(w["open"]) == "09:30" and _hm(w["close"]) == "16:00"
    assert w["open"].tzinfo is not None and w["close"].tzinfo is not None


def test_nse_quarter_hour_utc_offset_session_is_915_to_1530(xnse_calendar):
    r = session_windows("RELIANCE.NS", date(2026, 10, 19))
    assert r["status"] == "verified" and r["venue"] == "XNSE"
    assert r["timezone"] == "Asia/Kolkata"
    assert len(r["windows"]) == 1
    w = r["windows"][0]
    assert _hm(w["open"]) == "09:15" and _hm(w["close"]) == "15:30"
    assert w["open"].utcoffset().total_seconds() == 5.5 * 3600


def test_bse_shares_nse_session_shape():
    r = session_windows("SAIL.BO", date(2026, 10, 19))
    assert r["status"] == "verified" and r["venue"] == "XBOM"
    w = r["windows"][0]
    assert _hm(w["open"]) == "09:15" and _hm(w["close"]) == "15:30"


# ── holiday: closed, not unknown, not an empty "verified" ──────────────────

def test_nyse_holiday_is_closed_not_unknown():
    r = session_windows("AAPL", date(2026, 1, 1))            # New Year's Day
    assert r["status"] == "closed"
    assert r["windows"] == []
    assert "holiday" in r["reason"].lower()


def test_weekend_is_closed():
    r = session_windows("AAPL", date(2026, 10, 17))          # a Saturday
    assert r["status"] == "closed"
    assert r["windows"] == []
    assert "weekend" in r["reason"].lower()


# ── early close: the window is truncated, auction/post-market excluded ─────

def test_nyse_early_close_thanksgiving_friday_truncates_to_1300():
    r = session_windows("AAPL", date(2026, 11, 27))
    assert r["status"] == "verified"
    assert len(r["windows"]) == 1
    w = r["windows"][0]
    assert _hm(w["open"]) == "09:30" and _hm(w["close"]) == "13:00"


def test_lse_christmas_eve_half_day_excludes_closing_auction():
    r = session_windows("BARC.L", date(2026, 12, 24))
    assert r["status"] == "verified" and r["venue"] == "XLON"
    assert len(r["windows"]) == 1
    w = r["windows"][0]
    # continuous trading ends 12:30; the 5-minute closing auction after it
    # must not appear in an OPEN window.
    assert _hm(w["open"]) == "08:00" and _hm(w["close"]) == "12:30"


# ── lunch-break venues: two windows, the break is a gap, never bridged ─────

def test_hkex_lunch_break_gives_two_separate_windows():
    r = session_windows("0005.HK", date(2026, 10, 20))       # a Tuesday
    assert r["status"] == "verified" and r["venue"] == "XHKG"
    assert len(r["windows"]) == 2
    morning, afternoon = r["windows"]
    assert _hm(morning["open"]) == "09:30" and _hm(morning["close"]) == "12:00"
    assert _hm(afternoon["open"]) == "13:00" and _hm(afternoon["close"]) == "16:00"
    # the gap between them is the lunch break — not covered by any window.
    assert morning["close"] < afternoon["open"]


def test_tokyo_lunch_break_gives_two_separate_windows():
    r = session_windows("7203.T", date(2026, 10, 19))        # a Monday
    assert r["status"] == "verified" and r["venue"] == "XTKS"
    assert len(r["windows"]) == 2
    morning, afternoon = r["windows"]
    assert _hm(morning["open"]) == "09:00" and _hm(morning["close"]) == "11:30"
    assert _hm(afternoon["open"]) == "12:30" and _hm(afternoon["close"]) == "15:25"


# ── LSE DST: same local hours either side of the clock change ──────────────

def test_lse_local_hours_are_the_same_in_bst_and_gmt():
    bst = session_windows("BARC.L", date(2026, 6, 15))       # British Summer Time
    gmt = session_windows("BARC.L", date(2026, 11, 16))      # Greenwich Mean Time
    assert bst["status"] == gmt["status"] == "verified"
    assert _hm(bst["windows"][0]["open"]) == _hm(gmt["windows"][0]["open"]) == "08:00"
    assert _hm(bst["windows"][0]["close"]) == _hm(gmt["windows"][0]["close"]) == "16:30"
    # the UTC offset itself differs — that's the point of reading local time.
    assert bst["windows"][0]["open"].utcoffset() != gmt["windows"][0]["open"].utcoffset()


# ── FX overnight: the day's own window(s), no silent cross-midnight join ───

def test_fx_weekday_window_does_not_cross_into_the_next_calendar_date():
    r = session_windows("EURUSD=X", date(2026, 10, 19))      # a Monday
    assert r["status"] == "verified" and r["venue"] == "FX"
    for w in r["windows"]:
        assert w["open"].date() == date(2026, 10, 19)
        # a window may end at local midnight, but never past it: the next
        # day's own continuation is that day's own answer, not this one's.
        assert w["close"].date() in (date(2026, 10, 19), date(2026, 10, 20))
        if w["close"].date() == date(2026, 10, 20):
            assert w["close"].time() == time(0, 0)


def test_fx_saturday_is_closed():
    r = session_windows("EURUSD=X", date(2026, 10, 17))      # Saturday: no session
    assert r["status"] == "closed"
    assert r["windows"] == []


def test_crypto_is_a_single_full_day_window():
    r = session_windows("BTC-USD", date(2026, 10, 17))       # weekends included
    assert r["status"] == "verified" and r["venue"] == "CRYPTO"
    assert len(r["windows"]) == 1
    w = r["windows"][0]
    assert _hm(w["open"]) == "00:00"
    assert w["close"].date() == date(2026, 10, 18) and w["close"].time() == time(0, 0)


# ── unknown venue / unknown calendar year: never verified, never OPEN ──────

def test_unresolvable_symbol_is_unknown_not_closed():
    r = session_windows("NOTAREALSYMBOL", date(2026, 10, 19))
    assert r["status"] == "unknown"
    assert r["venue"] is None
    assert r["windows"] == []


def test_unmodelled_venue_is_unknown():
    r = session_windows("ENI.MI", date(2026, 10, 19))
    assert r["status"] == "unknown"
    assert r["windows"] == []


def test_year_with_no_verified_calendar_is_unknown_not_regular_hours():
    r = session_windows("AAPL", date(2030, 5, 15))            # a Wednesday
    assert r["status"] == "unknown"
    assert r["windows"] == []
    assert "no verified" in r["reason"].lower()


# ── no auction/pre-open/post-market remainder leaks into a window ──────────

def test_windows_never_include_pre_open_or_post_market():
    r = session_windows("AAPL", date(2026, 10, 19))
    assert len(r["windows"]) == 1
    w = r["windows"][0]
    # XNYS pre-open starts 04:00 and post-market ends 20:00; neither must
    # appear in the OPEN-only window.
    assert w["open"] >= w["open"].replace(hour=9, minute=30)
    assert w["close"] <= w["close"].replace(hour=16, minute=0)


def test_asx_closing_auction_excluded_from_window():
    r = session_windows("BHP.AX", date(2026, 10, 19))
    assert r["status"] == "verified" and r["venue"] == "XASX"
    w = r["windows"][0]
    assert _hm(w["open"]) == "10:00" and _hm(w["close"]) == "16:00"


# ── timezone sanity: windows carry the venue's own IANA zone ───────────────

def test_window_datetimes_are_tz_aware_in_the_venues_own_zone():
    r = session_windows("7203.T", date(2026, 10, 19))
    tz = ZoneInfo("Asia/Tokyo")
    for w in r["windows"]:
        assert w["open"].tzinfo is not None
        assert w["open"].utcoffset() == tz.utcoffset(w["open"].replace(tzinfo=None))
