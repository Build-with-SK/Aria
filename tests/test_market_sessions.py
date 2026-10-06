"""
tests/test_market_sessions.py
=============================
One session abstraction for every market — src/markets/sessions.py.

Instants are written in UTC on purpose: the point is that the abstraction,
not the test, does the timezone and daylight-saving arithmetic.
"""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.markets import calendars, sessions as S
from src.markets.sessions import SessionState as St

UTC = timezone.utc


def u(*a):
    return datetime(*a, tzinfo=UTC)


@pytest.fixture
def nse_and_tokyo_calendars():
    """Fixture calendars for venues with no built-in one, the way an owner's
    data/calendars/<CODE>.json would supply them."""
    calendars.register(calendars.CalendarData(
        "XNSE", frozenset({2026}), holidays=frozenset({date(2026, 11, 9)}),
        special_sessions={date(2026, 11, 8): (("OPEN", "18:00", "19:00"),)},
        source="test fixture"))
    calendars.register(calendars.CalendarData(
        "XTKS", frozenset({2026}), holidays=frozenset({date(2026, 11, 3)}),
        source="test fixture"))
    yield
    calendars.register(None, "XNSE")
    calendars.register(None, "XTKS")


# ── resolution: one symbol, one venue, one timezone ─────────────────────────

@pytest.mark.parametrize("symbol,venue,tz", [
    ("AAPL", "XNYS", "America/New_York"),
    ("BARC.L", "XLON", "Europe/London"),
    ("RELIANCE.NS", "XNSE", "Asia/Kolkata"),
    ("7203.T", "XTKS", "Asia/Tokyo"),
    ("0005.HK", "XHKG", "Asia/Hong_Kong"),
    ("SAP.DE", "XETR", "Europe/Berlin"),
    ("ES=F", "CME_GLOBEX", "America/Chicago"),
    ("ZC=F", "CBOT_GRAINS", "America/Chicago"),
    ("KC=F", "ICE_KC", "America/New_York"),
    ("EURUSD=X", "FX", "America/New_York"),
    ("BTC-USD", "CRYPTO", "UTC"),
    ("AAPL261218C00200000", "US_EQUITY_OPTIONS", "America/New_York"),
    ("SPXW261218P05000000", "US_INDEX_OPTIONS", "America/New_York"),
    ("^N225", "XTKS", "Asia/Tokyo"),
])
def test_each_instrument_resolves_its_own_venue(symbol, venue, tz):
    info = S.session(symbol, u(2026, 10, 6, 14, 0))
    assert info.venue == venue and info.timezone == tz


def test_an_unmodelled_venue_is_unknown_never_open():
    for sym in ("ENI.MI", "XX=F", "^MERV"):
        info = S.session(sym, u(2026, 10, 6, 14, 0))
        assert info.state == St.UNKNOWN and not info.tradable


def test_option_expiry_comes_from_the_occ_symbol():
    inst = S.resolve("AAPL261218C00200000")
    assert inst.expiry == date(2026, 12, 18) and inst.underlying == "AAPL"


# ── one instant, many markets ───────────────────────────────────────────────

def test_one_instant_different_states(nse_and_tokyo_calendars):
    t = u(2026, 10, 6, 7, 55)          # 08:55 London, 03:55 NY, 13:25 IST, 16:55 Tokyo
    assert S.session("BARC.L", t).state == St.OPEN
    assert S.session("AAPL", t).state == St.CLOSED         # 03:55 NY: before 04:00 pre-market
    assert S.session("RELIANCE.NS", t).state == St.OPEN
    assert S.session("7203.T", t).state == St.CLOSED


def test_us_premarket_regular_and_post():
    assert S.session("AAPL", u(2026, 10, 6, 7, 55)).state == St.CLOSED       # 03:55 ET
    assert S.session("AAPL", u(2026, 10, 6, 8, 5)).state == St.PRE_OPEN      # 04:05 ET
    assert S.session("AAPL", u(2026, 10, 6, 13, 30)).state == St.OPEN        # 09:30 ET
    assert S.session("AAPL", u(2026, 10, 6, 20, 30)).state == St.POST_MARKET # 16:30 ET


def test_lunch_breaks_in_asia(nse_and_tokyo_calendars):
    # 11:45 Tokyo = 02:45 UTC
    assert S.session("7203.T", u(2026, 10, 6, 2, 45)).state == St.BREAK
    assert S.session("7203.T", u(2026, 10, 6, 6, 27)).state == St.AUCTION   # 15:27 JST


# ── closed -> pre-open -> open, with next_open computed ─────────────────────

def test_the_transition_sequence_and_next_open():
    seq = [(u(2026, 10, 6, 6, 40), St.CLOSED),     # 07:40 BST
           (u(2026, 10, 6, 6, 55), St.PRE_OPEN),   # 07:55 opening auction
           (u(2026, 10, 6, 7, 1), St.OPEN),        # 08:01
           (u(2026, 10, 6, 15, 32), St.AUCTION),   # 16:32 closing auction
           (u(2026, 10, 6, 15, 40), St.CLOSED)]
    for t, st in seq:
        assert S.session("BARC.L", t).state == st, t
    closed = S.session("BARC.L", u(2026, 10, 6, 6, 40))
    assert closed.next_open == u(2026, 10, 6, 7, 0).isoformat()


def test_next_open_skips_the_weekend():
    fri_evening = u(2026, 10, 9, 21, 0)
    assert S.session("AAPL", fri_evening).next_open == u(2026, 10, 12, 13, 30).isoformat()


# ── daylight saving ─────────────────────────────────────────────────────────

def test_dst_gap_weeks_between_new_york_and_london():
    """US clocks move on 8 Mar 2026, UK clocks on 29 Mar. In between, the
    London close (16:30) is 12:30 in New York, not 11:30."""
    gap_week = u(2026, 3, 16, 16, 15)          # 16:15 London (GMT) = 12:15 NY (EDT)
    assert S.session("BARC.L", gap_week).state == St.OPEN
    assert S.session("AAPL", gap_week).state == St.OPEN
    winter = u(2026, 1, 12, 14, 15)            # 14:15 GMT = 09:15 EST
    assert S.session("AAPL", winter).state == St.PRE_OPEN
    summer = u(2026, 7, 13, 13, 15)            # 13:15 UTC = 09:15 EDT
    assert S.session("AAPL", summer).state == St.PRE_OPEN


def test_the_us_open_moves_in_utc_across_dst():
    assert S.session_open("XNYS", date(2026, 1, 12)).astimezone(UTC).hour == 14
    assert S.session_open("XNYS", date(2026, 7, 13)).astimezone(UTC).hour == 13


# ── holidays, early closes, special sessions ────────────────────────────────

def test_holidays_are_per_venue():
    # 31 Aug 2026: UK summer bank holiday, US trading day
    t = u(2026, 8, 31, 14, 0)
    assert S.session("BARC.L", t).state == St.HOLIDAY
    assert S.session("AAPL", t).state == St.OPEN
    # 26 Nov 2026: Thanksgiving — US closed, London open
    t = u(2026, 11, 26, 15, 0)
    assert S.session("AAPL", t).state == St.HOLIDAY
    assert S.session("BARC.L", t).state == St.OPEN


def test_early_closes():
    # 24 Dec 2026: LSE closes 12:30 with a closing auction; NYSE closes 13:00
    assert S.session("BARC.L", u(2026, 12, 24, 12, 31)).state == St.AUCTION
    assert S.session("BARC.L", u(2026, 12, 24, 12, 40)).state == St.CLOSED
    assert S.session("AAPL", u(2026, 12, 24, 17, 59)).state == St.OPEN       # 12:59 ET
    assert S.session("AAPL", u(2026, 12, 24, 18, 1)).state == St.CLOSED      # 13:01 ET
    assert S.session_close("XNYS", date(2026, 12, 24)).hour == 13


def test_a_special_session_on_a_sunday(nse_and_tokyo_calendars):
    """Muhurat-style evening session: the calendar's special_sessions replace
    the day's phases entirely."""
    sunday_1830_ist = u(2026, 11, 8, 13, 0)
    assert S.session("RELIANCE.NS", sunday_1830_ist).state == St.OPEN
    assert S.session("RELIANCE.NS", u(2026, 11, 9, 5, 0)).state == St.HOLIDAY


def test_without_a_calendar_a_weekday_is_unknown_inside_hours_closed_outside():
    inside = S.session("RELIANCE.NS", u(2026, 10, 6, 5, 0))       # 10:30 IST
    outside = S.session("RELIANCE.NS", u(2026, 10, 6, 16, 0))     # 21:30 IST
    weekend = S.session("RELIANCE.NS", u(2026, 10, 10, 5, 0))     # Saturday
    assert inside.state == St.UNKNOWN and not inside.tradable
    assert "refusing rather than guessing" in inside.reasons[0]
    assert outside.state == St.CLOSED
    assert weekend.state == St.CLOSED


def test_an_uncovered_year_is_unknown():
    info = S.session("AAPL", u(2031, 3, 4, 15, 0))
    assert info.state == St.UNKNOWN and info.next_open is None


def test_a_calendar_file_is_loaded_and_needs_a_source(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(calendars, "CALENDAR_DIR", tmp_path)
    calendars.clear_cache()
    (tmp_path / "XHKG.json").write_text(json.dumps(
        {"source": "HKEX circular", "years": [2026], "holidays": ["2026-10-01"]}))
    (tmp_path / "XSHG.json").write_text(json.dumps({"years": [2026], "holidays": []}))
    try:
        assert S.session("0005.HK", u(2026, 10, 1, 3, 0)).state == St.HOLIDAY
        assert S.session("0005.HK", u(2026, 10, 2, 3, 0)).state == St.OPEN
        assert S.session("600519.SS", u(2026, 10, 2, 3, 0)).state == St.UNKNOWN   # no source
    finally:
        calendars.clear_cache()


# ── futures and options ─────────────────────────────────────────────────────

def test_cme_globex_week_and_daily_break():
    assert S.session("ES=F", u(2026, 10, 4, 21, 30)).state == St.CLOSED   # Sun 16:30 CT
    assert S.session("ES=F", u(2026, 10, 4, 22, 30)).state == St.OPEN     # Sun 17:30 CT
    assert S.session("ES=F", u(2026, 10, 6, 21, 30)).state == St.BREAK    # Tue 16:30 CT
    assert S.session("ES=F", u(2026, 10, 9, 21, 30)).state == St.CLOSED   # Fri 16:30 CT
    assert S.session("ES=F", u(2026, 10, 10, 15, 0)).state == St.CLOSED   # Saturday
    brk = S.session("ES=F", u(2026, 10, 6, 21, 30))
    assert brk.next_open == u(2026, 10, 6, 22, 0).isoformat()            # 17:00 CT


def test_grain_and_soft_futures_keep_their_own_hours():
    t = u(2026, 10, 6, 13, 0)                 # 08:00 CT / 09:00 ET
    assert S.session("ZC=F", t).state == St.BREAK          # grains pause 07:45-08:30 CT
    assert S.session("KC=F", t).state == St.OPEN           # coffee 04:15-13:30 ET
    assert S.session("KC=F", u(2026, 10, 6, 19, 0)).state == St.CLOSED   # 15:00 ET


def test_futures_on_a_us_holiday_are_unknown_not_open():
    assert S.session("ES=F", u(2026, 11, 26, 16, 0)).state == St.UNKNOWN  # Thanksgiving
    assert S.session("ES=F", u(2026, 11, 25, 23, 30)).state == St.UNKNOWN # eve of it


def test_options_sessions_and_expiry():
    opt = "AAPL261218C00200000"
    assert S.session(opt, u(2026, 12, 17, 15, 0)).state == St.OPEN
    expiring = S.session(opt, u(2026, 12, 18, 15, 0))
    assert expiring.state == St.EXPIRING and not expiring.tradable
    assert S.session(opt, u(2026, 12, 21, 15, 0)).state == St.CLOSED
    assert S.session("SPXW261218P05000000", u(2026, 12, 17, 21, 10)).state == St.OPEN  # 16:10 ET
    assert S.session(opt, u(2026, 12, 17, 21, 10)).state == St.CLOSED


def test_fx_week_and_crypto_always():
    assert S.session("EURUSD=X", u(2026, 10, 10, 12, 0)).state == St.CLOSED   # Saturday
    assert S.session("EURUSD=X", u(2026, 10, 11, 21, 30)).state == St.OPEN    # Sun 17:30 NY
    assert S.session("BTC-USD", u(2026, 12, 25, 3, 0)).state == St.OPEN


def test_a_reported_halt_overrides_the_schedule():
    info = S.session("AAPL", u(2026, 10, 6, 15, 0), halted=True)
    assert info.state == St.HALTED and not info.tradable
    assert any("halt status not observed" in r
               for r in S.session("AAPL", u(2026, 10, 6, 15, 0)).reasons)


# ── point in time across markets ────────────────────────────────────────────

def test_a_us_bar_is_not_known_at_tokyos_open_the_same_day():
    tokyo_open_mon = u(2026, 10, 5, 0, 0)               # 09:00 JST Monday
    assert not S.bar_known_by("XNYS", date(2026, 10, 5), tokyo_open_mon)
    assert S.bar_known_by("XNYS", date(2026, 10, 2), tokyo_open_mon)   # Friday's close
    tokyo_open_tue = u(2026, 10, 6, 0, 0)
    assert S.bar_known_by("XNYS", date(2026, 10, 5), tokyo_open_tue)


def test_an_unknown_venue_bar_is_known_only_when_the_date_is_over_everywhere():
    assert not S.bar_known_by(None, date(2026, 10, 5), u(2026, 10, 6, 12, 0))
    assert S.bar_known_by(None, date(2026, 10, 5), u(2026, 10, 7, 0, 0))


def test_replay_at_an_instant_hides_bars_that_had_not_closed(monkeypatch):
    """The V5 replay: at 09:00 Tokyo on Monday a Tokyo replay may see Tokyo's
    Friday close and New York's Friday close — not New York's Monday bar,
    which a date-based cut would have handed it."""
    from src.v5 import marketdata as md
    idx = pd.bdate_range("2026-09-21", "2026-10-06")
    frame = pd.DataFrame({"Close": range(len(idx))}, index=idx, dtype=float)
    monkeypatch.setattr(md, "_full_history", lambda symbol, interval: frame)
    monkeypatch.setattr(md, "resolve", lambda s: s)
    when = pd.Timestamp(datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo("Asia/Tokyo")))
    with md.as_of(when):
        us = md.history("@AAPL", period="10y")
    assert us.index.max() == pd.Timestamp("2026-10-02")
    with md.as_of(pd.Timestamp("2026-10-05")):                  # date mode, legacy
        legacy = md.history("@AAPL", period="10y")
    assert legacy.index.max() == pd.Timestamp("2026-10-05")


# ── the old entry points read the same source ───────────────────────────────

def test_trading_market_and_sessions_agree():
    from src.trading import market
    for t in (u(2026, 11, 26, 15, 0), u(2026, 10, 6, 15, 0), u(2026, 10, 10, 15, 0),
              u(2026, 12, 24, 18, 30)):
        ok, _ = market.market_open("equity", t)
        assert ok == S.session("AAPL", t).tradable
    assert market.latest_completed_session(u(2026, 12, 24, 19, 30)) == date(2026, 12, 24)


def test_the_desk_check_is_holiday_aware_now():
    from src.desk.desk_daemon import instrument_open, us_equities_open
    thanksgiving = u(2026, 11, 26, 16, 0)
    assert us_equities_open(thanksgiving) is False          # the old check said True
    assert instrument_open("BARC.L", thanksgiving) is True


# ── the API serves the same answer ──────────────────────────────────────────

def test_markets_api_serves_the_abstraction(monkeypatch, tmp_path):
    import os
    from fastapi.testclient import TestClient
    from backend.main import app
    from src.auth import hardening
    from src.auth import session as sess
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "test-owner-token-markets")
    monkeypatch.delenv("ARIA_OWNER_TOKEN_HASH", raising=False)
    monkeypatch.delenv("ARIA_TRUST_PROXY", raising=False)
    monkeypatch.setattr(sess.SESSIONS, "store", tmp_path / "s.json")
    monkeypatch.setattr(sess.SESSIONS, "session_key_file", tmp_path / ".k")
    sess.SESSIONS._attempts.clear()
    hardening._limiter._hits.clear()
    c = TestClient(app, client=("203.0.113.31", 53201))
    assert c.get("/api/markets/session?symbols=AAPL").status_code in (401, 403)
    assert c.post("/api/auth/login", json={"token": "test-owner-token-markets"}).status_code == 200
    body = c.get("/api/markets/session",
                 params={"symbols": "AAPL,BARC.L", "at": "2026-11-26T15:00:00+00:00"}).json()
    states = {r["symbol"]: r["state"] for r in body["sessions"]}
    assert states == {"AAPL": "HOLIDAY", "BARC.L": "OPEN"}
    cal = c.get("/api/markets/calendars").json()
    assert cal["calendars"]["XNYS"]["years"] == [2026, 2027]
    plan = c.post("/api/markets/plan", json={"symbol": "AAPL", "side": "buy", "qty": 1}).json()
    assert plan["kind"] in ("EXECUTE_NOW", "SCHEDULED", "DEFERRED")


def test_prediction_information_sets_use_the_instruments_own_venue():
    """At 18:00 London on a Tuesday, London's Tuesday close has settled but
    New York is mid-session: the two calls are built on different closes."""
    from src.v5.learning import information_key, venue_for
    t = u(2026, 10, 6, 17, 0)                       # 18:00 BST, 13:00 NY
    assert venue_for("BARC.L") == "XLON" and venue_for("AAPL") == "XNYS"
    assert information_key(t, "XLON") == ("2026-10-06", False)
    assert information_key(t, "XNYS") == ("2026-10-05", True)
    assert venue_for("BTC-USD") == "XNYS"            # no daily close: legacy key
