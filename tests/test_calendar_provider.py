"""
tests/test_calendar_provider.py
===============================
exchange_calendars behind src/markets/providers.py, under the one session
engine (src/markets/sessions.py).

Anchor dates are the library's own 2026 data (holidays, half days), checked
against well-known closures where they are unambiguous (Good Friday, Republic
Day, Lunar New Year, Canada Day, Australia Day). Instants are UTC so the engine
— not the test — does the timezone and daylight-saving arithmetic.
"""
import logging
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("exchange_calendars")

from src.markets import calendars, providers, sessions as S
from src.markets.sessions import SessionState as St
from src.markets.venues import VENUES

UTC = timezone.utc


def u(*a):
    return datetime(*a, tzinfo=UTC)


def at(tz, *a):
    return datetime(*a, tzinfo=ZoneInfo(tz))


@pytest.fixture(autouse=True)
def _default_chain():
    providers.default_chain()
    yield
    providers.default_chain()


# ── per venue: normal day, weekend, holiday ─────────────────────────────────

VENUE_CASES = [
    # symbol, tz, a normal mid-session local time, a 2026 holiday from the library
    ("AAPL", "America/New_York", (2026, 10, 6, 11, 0), date(2026, 4, 3)),        # Good Friday
    ("BARC.L", "Europe/London", (2026, 10, 6, 11, 0), date(2026, 8, 31)),        # summer bank hol.
    ("500325.BO", "Asia/Kolkata", (2026, 10, 6, 11, 0), date(2026, 1, 26)),      # Republic Day
    ("7203.T", "Asia/Tokyo", (2026, 10, 6, 10, 0), date(2026, 1, 12)),           # Coming of Age Day
    ("0005.HK", "Asia/Hong_Kong", (2026, 10, 6, 10, 0), date(2026, 2, 17)),      # Lunar New Year
    ("SAP.DE", "Europe/Berlin", (2026, 10, 6, 11, 0), date(2026, 12, 24)),       # Xetra shut 24 Dec
    ("MC.PA", "Europe/Paris", (2026, 10, 6, 11, 0), date(2026, 5, 1)),           # Labour Day
    ("ASML.AS", "Europe/Amsterdam", (2026, 10, 6, 11, 0), date(2026, 4, 6)),     # Easter Monday
    ("NESN.SW", "Europe/Zurich", (2026, 10, 6, 11, 0), date(2026, 5, 14)),       # Ascension
    ("RY.TO", "America/Toronto", (2026, 10, 6, 11, 0), date(2026, 7, 1)),        # Canada Day
    ("BHP.AX", "Australia/Sydney", (2026, 10, 6, 11, 0), date(2026, 1, 26)),     # Australia Day
    ("600519.SS", "Asia/Shanghai", (2026, 10, 9, 10, 0), date(2026, 10, 5)),     # National Day week
]


@pytest.mark.parametrize("symbol,tz,normal,holiday", VENUE_CASES)
def test_normal_day_weekend_and_holiday(symbol, tz, normal, holiday):
    info = S.session(symbol, at(tz, *normal))
    assert info.state == St.OPEN, (symbol, info.reasons)
    assert info.calendar_verified is True
    sat = at(tz, 2026, 10, 10, 11, 0)
    assert S.session(symbol, sat).state == St.CLOSED
    hol = S.session(symbol, datetime.combine(holiday, datetime.min.time().replace(hour=11),
                                             ZoneInfo(tz)))
    assert hol.state == St.HOLIDAY, (symbol, holiday, hol.reasons)
    assert "exchange_calendars" in hol.reasons[0] or "built-in" in hol.reasons[0] \
        or "holiday" in hol.reasons[0]


# ── early closes and half days ──────────────────────────────────────────────

@pytest.mark.parametrize("symbol,tz,day,still_open,auction,closed", [
    ("AAPL", "America/New_York", (2026, 11, 27), (12, 59), None, (13, 1)),
    ("BARC.L", "Europe/London", (2026, 12, 24), (12, 29), (12, 31), (12, 36)),
    ("0005.HK", "Asia/Hong_Kong", (2026, 12, 24), (11, 59), (12, 5), (12, 15)),
    ("SAP.DE", "Europe/Berlin", (2026, 12, 30), (13, 59), (14, 2), (14, 6)),
    ("MC.PA", "Europe/Paris", (2026, 12, 24), (13, 59), (14, 2), (14, 6)),
    ("RY.TO", "America/Toronto", (2026, 12, 24), (12, 59), None, (13, 1)),
    ("BHP.AX", "Australia/Sydney", (2026, 12, 24), (14, 9), (14, 11), (14, 30)),
])
def test_early_closes(symbol, tz, day, still_open, auction, closed):
    assert S.session(symbol, at(tz, *day, *still_open)).state == St.OPEN
    if auction:
        assert S.session(symbol, at(tz, *day, *auction)).state == St.AUCTION
    assert S.session(symbol, at(tz, *day, *closed)).state == St.CLOSED


def test_hong_kong_half_day_has_no_afternoon():
    assert S.session("0005.HK", at("Asia/Hong_Kong", 2026, 12, 24, 13, 30)).state == St.CLOSED


def test_index_options_close_by_the_same_delta_on_an_nyse_half_day():
    """NYSE closes 13:00 instead of 16:00; index options 13:15 instead of 16:15."""
    assert S.session("SPXW261218P05000000", at("America/New_York", 2026, 11, 27, 13, 10)).state == St.OPEN
    assert S.session("SPXW261218P05000000", at("America/New_York", 2026, 11, 27, 13, 20)).state == St.CLOSED
    assert S.session("AAPL261218C00200000", at("America/New_York", 2026, 11, 27, 13, 5)).state == St.CLOSED


# ── phases, breaks, next open / next close ──────────────────────────────────

def test_pre_open_and_lunch_breaks():
    assert S.session("500325.BO", at("Asia/Kolkata", 2026, 10, 6, 9, 5)).state == St.PRE_OPEN
    assert S.session("7203.T", at("Asia/Tokyo", 2026, 10, 6, 8, 30)).state == St.PRE_OPEN
    assert S.session("7203.T", at("Asia/Tokyo", 2026, 10, 6, 12, 0)).state == St.BREAK
    assert S.session("0005.HK", at("Asia/Hong_Kong", 2026, 10, 6, 9, 15)).state == St.PRE_OPEN
    assert S.session("0005.HK", at("Asia/Hong_Kong", 2026, 10, 6, 12, 30)).state == St.BREAK


def test_next_open_skips_holidays_and_weekends():
    after_half_day = S.session("0005.HK", at("Asia/Hong_Kong", 2026, 12, 24, 12, 15))
    # 25 Dec holiday, 26-27 weekend -> Monday 28 Dec 09:30 HKT
    assert after_half_day.next_open == u(2026, 12, 28, 1, 30).isoformat()
    lny = S.session("0005.HK", at("Asia/Hong_Kong", 2026, 2, 16, 13, 0))   # LNY eve half day
    assert lny.next_open == u(2026, 2, 20, 1, 30).isoformat()               # 17-19 Feb holidays


def test_next_close():
    assert S.session("7203.T", at("Asia/Tokyo", 2026, 10, 6, 10, 0)).next_close == \
        u(2026, 10, 6, 6, 30).isoformat()                 # lunch is not a close
    assert S.session("AAPL", at("America/New_York", 2026, 11, 27, 11, 0)).next_close == \
        u(2026, 11, 27, 18, 0).isoformat()                # 13:00 ET half day
    assert S.session("ES=F", u(2026, 10, 6, 15, 0)).next_close == u(2026, 10, 6, 21, 0).isoformat()
    assert S.session("BTC-USD", u(2026, 10, 6, 15, 0)).next_close is None


# ── timezones and daylight saving ───────────────────────────────────────────

@pytest.mark.parametrize("code,before,after,utc_before,utc_after", [
    ("XNYS", date(2026, 3, 6), date(2026, 3, 9), 14, 13),       # US DST 8 Mar
    ("XLON", date(2026, 3, 27), date(2026, 3, 30), 8, 7),       # UK DST 29 Mar
    ("XETR", date(2026, 3, 27), date(2026, 3, 30), 8, 7),       # EU DST 29 Mar
    ("XLON", date(2026, 10, 23), date(2026, 10, 26), 7, 8),     # UK back 25 Oct
])
def test_open_moves_in_utc_across_dst(code, before, after, utc_before, utc_after):
    assert S.session_open(code, before).astimezone(UTC).hour == utc_before
    assert S.session_open(code, after).astimezone(UTC).hour == utc_after


def test_australia_dst_is_southern_hemisphere():
    # AEST (+10) until 4 Oct 2026, AEDT (+11) after: a 10:00 open is 00:00 then 23:00 UTC
    assert S.session_open("XASX", date(2026, 10, 2)).astimezone(UTC) == u(2026, 10, 2, 0, 0)
    assert S.session_open("XASX", date(2026, 10, 6)).astimezone(UTC) == u(2026, 10, 5, 23, 0)


@pytest.mark.parametrize("code,utc", [("XTKS", (0, 0)), ("XBOM", (3, 45)), ("XHKG", (1, 30))])
def test_asian_venues_have_no_dst(code, utc):
    for d in (date(2026, 1, 6), date(2026, 7, 7)):
        o = S.session_open(code, d).astimezone(UTC)
        assert (o.hour, o.minute) == utc


def test_london_new_york_autumn_gap_week():
    """UK clocks go back 25 Oct, US on 1 Nov: for a week the LSE close (16:30
    GMT) is 12:30 in New York, and both are open at 16:15 UTC."""
    t = u(2026, 10, 28, 16, 15)
    assert S.session("BARC.L", t).state == St.OPEN
    assert S.session("AAPL", t).state == St.OPEN
    assert S.session("BARC.L", u(2026, 11, 4, 16, 15)).state == St.OPEN    # GMT, NY on EST
    assert S.session("AAPL", u(2026, 11, 4, 14, 15)).state == St.PRE_OPEN   # 09:15 EST


# ── history and the future ──────────────────────────────────────────────────

def test_tokyo_history_uses_the_old_15_00_close():
    """TSE closed at 15:00 until 2024-11-05. A replay of 2023 must not believe
    it traded until 15:30."""
    assert S.session("7203.T", at("Asia/Tokyo", 2023, 6, 1, 15, 10)).state == St.CLOSED
    assert S.session("7203.T", at("Asia/Tokyo", 2026, 6, 1, 15, 10)).state == St.OPEN
    assert S.session_close("XTKS", date(2023, 6, 1)).strftime("%H:%M") == "15:00"


def test_an_ad_hoc_historical_closure():
    # NYSE shut 29-30 Oct 2012 (Hurricane Sandy) — in the library's ad-hoc list
    assert S.session("AAPL", at("America/New_York", 2012, 10, 29, 11, 0)).state == St.HOLIDAY
    assert S.session("AAPL", at("America/New_York", 2012, 10, 31, 11, 0)).state == St.OPEN


def test_future_dates_inside_the_horizon_resolve():
    assert S.session("AAPL", at("America/New_York", 2027, 3, 26, 11, 0)).state == St.HOLIDAY  # Good Friday
    assert S.session("0005.HK", at("Asia/Hong_Kong", 2027, 6, 1, 10, 0)).state == St.OPEN


def test_stale_or_beyond_horizon_is_unknown():
    # India and China are recorded only to 2026 in the library
    assert S.session("500325.BO", at("Asia/Kolkata", 2027, 3, 2, 11, 0)).state == St.UNKNOWN
    assert S.session("600519.SS", at("Asia/Shanghai", 2027, 3, 2, 10, 0)).state == St.UNKNOWN
    # beyond the fixed provider horizon and any built-in coverage
    assert S.session("0005.HK", at("Asia/Hong_Kong", 2028, 3, 1, 10, 0)).state == St.UNKNOWN
    assert S.session("AAPL", at("America/New_York", 2028, 3, 1, 11, 0)).state == St.UNKNOWN


def test_the_provider_range_is_fixed_not_today_plus_a_year():
    p = providers.ExchangeCalendarsProvider()
    assert p.calendar("XNYS").last_session.date() == providers.HORIZON
    assert p.calendar("XBOM").last_session.date() == date(2026, 12, 31)


# ── built-ins and the library agree ─────────────────────────────────────────

@pytest.mark.parametrize("code", ["XNYS", "XLON"])
def test_builtin_calendars_agree_with_exchange_calendars(code):
    p = providers.ExchangeCalendarsProvider()
    cal = p.calendar(code)
    builtin = calendars.get_builtin(code)
    tz = VENUES[code].tz
    for d in pd.bdate_range("2026-01-01", "2026-12-31"):
        dd = d.date()
        f = p.facts(code, dd, tz)
        assert f.is_session == (dd not in builtin.holidays), dd
        if dd in builtin.early_closes:
            h, m = map(int, builtin.early_closes[dd].split(":"))
            assert f.close_minute == h * 60 + m, dd
        elif f.is_session:
            assert f.close_minute == f.regular_close, dd


# ── precedence: owner file > library > built-in ─────────────────────────────

def test_an_owner_calendar_beats_the_library():
    calendars.register(calendars.CalendarData(
        "XHKG", frozenset({2026}), holidays=frozenset({date(2026, 10, 6)}),
        source="test: owner circular"))
    try:
        assert S.session("0005.HK", at("Asia/Hong_Kong", 2026, 10, 6, 10, 0)).state == St.HOLIDAY
    finally:
        calendars.register(None, "XHKG")
    assert S.session("0005.HK", at("Asia/Hong_Kong", 2026, 10, 6, 10, 0)).state == St.OPEN


def test_nse_needs_an_explicit_owner_alias():
    """The library has no NSE calendar. Using BSE's is an owner decision."""
    assert S.session("RELIANCE.NS", at("Asia/Kolkata", 2026, 10, 6, 11, 0)).state == St.UNKNOWN
    calendars.register(calendars.CalendarData(
        "XNSE", frozenset(), alias_of="XBOM", source="test: owner decision, NSE = BSE days"))
    try:
        ok = S.session("RELIANCE.NS", at("Asia/Kolkata", 2026, 10, 6, 11, 0))
        assert ok.state == St.OPEN
        hol = S.session("RELIANCE.NS", at("Asia/Kolkata", 2026, 1, 26, 11, 0))
        assert hol.state == St.HOLIDAY and "XBOM" in hol.reasons[0]
    finally:
        calendars.register(None, "XNSE")


def test_an_alias_needs_a_source_in_a_file(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(calendars, "CALENDAR_DIR", tmp_path)
    calendars.clear_cache()
    (tmp_path / "XNSE.json").write_text(json.dumps({"alias_of": "XBOM"}))
    try:
        assert S.session("RELIANCE.NS", at("Asia/Kolkata", 2026, 10, 6, 11, 0)).state == St.UNKNOWN
    finally:
        calendars.clear_cache()


# ── futures stay contract-specific ──────────────────────────────────────────

def test_futures_keep_contract_hours_and_unknown_us_holidays():
    assert S.session("ES=F", u(2026, 11, 26, 16, 0)).state == St.UNKNOWN          # Thanksgiving
    assert S.session("ES=F", u(2026, 10, 6, 21, 30)).state == St.BREAK             # 16:30 CT
    # History now resolves through the same chain (the NYSE's own calendar):
    assert S.session("ES=F", u(2015, 6, 2, 15, 0)).state == St.OPEN
    assert S.session("ES=F", u(2012, 10, 29, 15, 0)).state == St.UNKNOWN           # Sandy
    assert S.session("KC=F", u(2026, 10, 6, 19, 0)).state == St.CLOSED            # 15:00 ET


def test_futures_are_not_mapped_to_a_generic_library_calendar():
    for code in ("CME_GLOBEX", "CBOT_GRAINS", "ICE_KC", "ICE_SB", "ICE_CC", "ICE_CT"):
        assert VENUES[code].holiday_rule == "us_futures"
        assert code not in providers.XCALS_MAP


# ── cross-market ordering and point in time ─────────────────────────────────

def test_one_dates_closes_are_strictly_ordered_across_markets():
    d = date(2026, 10, 6)
    order = [S.bar_available_at(c, d) for c in ("XTKS", "XHKG", "XBOM", "XLON", "XNYS")]
    assert order == sorted(order) and len(set(order)) == 5
    assert [t.strftime("%H:%M") for t in order] == ["06:30", "08:10", "10:00", "15:35", "20:00"]


def test_bar_known_exactly_at_the_close_boundary():
    close = S.bar_available_at("XNYS", date(2026, 10, 6))
    assert not S.bar_known_by("XNYS", date(2026, 10, 6), close - timedelta(minutes=1))
    assert S.bar_known_by("XNYS", date(2026, 10, 6), close)


def _replay_result(md, when):
    with md.as_of(pd.Timestamp(when)):
        us = md.history("@AAPL", period="10y")
        jp = md.history("@7203.T", period="10y")
        uk = md.history("@BARC.L", period="10y")
    return (float(us["Close"].sum()), float(jp["Close"].sum()), float(uk["Close"].sum()),
            us.index.max(), jp.index.max(), uk.index.max())


@pytest.fixture
def three_markets(monkeypatch):
    from src.v5 import marketdata as md
    idx = pd.bdate_range("2026-09-21", "2026-10-09")
    frames = {s: pd.DataFrame({"Close": [float(i + 1) for i in range(len(idx))]}, index=idx)
              for s in ("@AAPL", "@7203.T", "@BARC.L")}
    monkeypatch.setattr(md, "_full_history", lambda symbol, interval: frames[symbol])
    return md, frames


def test_changing_a_later_close_cannot_change_an_earlier_result(three_markets):
    """Adversarial: at 09:00 Tokyo on Monday 5 Oct no market has closed its
    Monday session yet (Tokyo closes 15:30 JST, London and New York later), so
    rewriting all three Monday bars must change nothing the replay can see.
    Rewriting Tokyo's FRIDAY bar, which had closed, must change the result —
    proving the check can fail."""
    md, frames = three_markets
    when = at("Asia/Tokyo", 2026, 10, 5, 9, 0)
    before = _replay_result(md, when)
    assert before[3:] == (pd.Timestamp("2026-10-02"),) * 3
    for sym in ("@AAPL", "@BARC.L", "@7203.T"):
        frames[sym].loc["2026-10-05", "Close"] = 1e9
    assert _replay_result(md, when) == before
    frames["@7203.T"].loc["2026-10-02", "Close"] = 1e9
    assert _replay_result(md, when) != before


def test_a_us_replay_sees_tokyos_closed_session_but_not_londons_open_one(three_markets):
    md, frames = three_markets
    nine_am_ny = at("America/New_York", 2026, 10, 5, 9, 0)        # 13:00 UTC Monday
    us_max, jp_max, uk_max = _replay_result(md, nine_am_ny)[3:]
    assert jp_max == pd.Timestamp("2026-10-05")      # Tokyo closed at 06:30 UTC
    assert uk_max == pd.Timestamp("2026-10-02")      # London closes 15:35 UTC
    assert us_max == pd.Timestamp("2026-10-02")
    frames["@BARC.L"].loc["2026-10-05", "Close"] = -1.0
    assert _replay_result(md, nine_am_ny)[3:] == (us_max, jp_max, uk_max)


# ── failures fail safe ──────────────────────────────────────────────────────

class _Boom(providers.CalendarProvider):
    name = "boom"

    def facts(self, code, d, tz):
        raise RuntimeError("calendar backend exploded")


def test_a_raising_provider_falls_back_then_fails_safe(caplog):
    providers.set_chain([_Boom()])
    with caplog.at_level(logging.WARNING):
        for _ in range(20):
            hk = S.session("0005.HK", at("Asia/Hong_Kong", 2026, 10, 6, 10, 0))
    assert hk.state == St.UNKNOWN and not hk.tradable          # nothing else covers HKEX
    nyse = S.session("AAPL", at("America/New_York", 2026, 4, 3, 11, 0))
    assert nyse.state == St.HOLIDAY                              # built-in still answers
    assert sum("exploded" in r.getMessage() for r in caplog.records) == 1   # not noisy


def test_a_missing_library_is_unknown_and_logged_once(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "exchange_calendars", None)   # import now fails
    providers.set_chain([providers.ExchangeCalendarsProvider()])
    with caplog.at_level(logging.WARNING):
        for _ in range(10):
            hk = S.session("0005.HK", at("Asia/Hong_Kong", 2026, 10, 6, 10, 0))
    assert hk.state == St.UNKNOWN
    assert sum("not installed" in r.getMessage() for r in caplog.records) == 1


def test_an_invalid_timezone_is_unknown(monkeypatch):
    monkeypatch.setitem(VENUES, "XLON", replace(VENUES["XLON"], tz="Mars/Olympus_Mons"))
    info = S.session("BARC.L", u(2026, 10, 6, 10, 0))
    assert info.state == St.UNKNOWN and "timezone" in info.reasons[0]
    assert S.session_close("XLON", date(2026, 10, 6)) is None
    assert S.next_close("XLON", u(2026, 10, 6, 10, 0)) is None


def test_unsupported_and_uncovered_venues():
    assert S.session("ENI.MI", u(2026, 10, 6, 10, 0)).state == St.UNKNOWN
    shenzhen = S.session("000001.SZ", at("Asia/Shanghai", 2026, 10, 9, 10, 0))
    assert shenzhen.state == St.UNKNOWN and "data/calendars/XSHE.json" in shenzhen.reasons[0]


# ── architecture ────────────────────────────────────────────────────────────

def test_only_the_provider_module_imports_the_library():
    hits = [p.relative_to(ROOT).as_posix() for p in (ROOT / "src").rglob("*.py")
            if "__pycache__" not in p.parts
            and "exchange_calendars" in p.read_text(encoding="utf-8", errors="ignore")
            and ("import exchange_calendars" in p.read_text(encoding="utf-8", errors="ignore")
                 or "from exchange_calendars" in p.read_text(encoding="utf-8", errors="ignore"))]
    assert hits == ["src/markets/providers.py"], hits


def test_the_dependency_is_pinned_where_environments_are_built():
    for name in ("requirements.txt", "requirements-ci.txt"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert f"exchange_calendars=={providers.PINNED_VERSION}" in text, name
    from importlib.metadata import version
    assert version("exchange_calendars") == providers.PINNED_VERSION
