"""
tests/test_pit_global.py
========================
Adversarial point-in-time replays across Tokyo, Hong Kong, India, Shanghai,
Shenzhen, London, Europe, New York, Toronto, Australia and CME futures.

The invariant: at any instant T a replay sees a daily bar only if that bar's
session had CLOSED by T at its own venue. Every expected close below is
written by hand from the venue's published hours and that week's UTC offset
— the engine under test is never asked what it should have answered.

Each scenario is adversarial in both directions: rewriting a bar that had not
closed by T must change nothing the replay sees, and rewriting one that had
closed must change it — so a leak, or a check that cannot fail, both fail.
"""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("exchange_calendars")

from src.markets import calendars, providers, sessions as S   # noqa: E402

UTC = timezone.utc


def u(*a):
    return datetime(*a, tzinfo=UTC)


@pytest.fixture(autouse=True)
def shipped(monkeypatch):
    """NSE and Shenzhen are answered by the calendars shipped in data/."""
    providers.default_chain()
    monkeypatch.setattr(calendars, "CALENDAR_DIR", ROOT / "data" / "calendars")
    calendars.clear_cache()
    yield
    calendars.clear_cache()


#: symbol -> (venue, close of Friday 9 Oct 2026 in UTC, by hand)
#:   Sydney AEDT +11 since 4 Oct; Europe on summer time until 25 Oct; New
#:   York and Toronto EDT -4; Chicago CDT -5; Asia has no daylight saving.
FRI = date(2026, 10, 9)
MARKETS = {
    "@BHP.AX":      ("XASX", u(2026, 10, 9, 5, 12)),    # 16:12 AEDT (closing auction)
    "@7203.T":      ("XTKS", u(2026, 10, 9, 6, 30)),    # 15:30 JST
    "@600519.SS":   ("XSHG", u(2026, 10, 9, 7, 0)),     # 15:00 CST
    "@000001.SZ":   ("XSHE", u(2026, 10, 9, 7, 0)),     # 15:00 CST
    "@0005.HK":     ("XHKG", u(2026, 10, 9, 8, 10)),    # 16:10 HKT (closing auction)
    "@RELIANCE.NS": ("XNSE", u(2026, 10, 9, 10, 0)),    # 15:30 IST
    "@500325.BO":   ("XBOM", u(2026, 10, 9, 10, 0)),    # 15:30 IST
    "@BARC.L":      ("XLON", u(2026, 10, 9, 15, 35)),   # 16:35 BST
    "@SAP.DE":      ("XETR", u(2026, 10, 9, 15, 35)),   # 17:35 CEST
    "@MC.PA":       ("XPAR", u(2026, 10, 9, 15, 35)),   # 17:35 CEST
    "@AAPL":        ("XNYS", u(2026, 10, 9, 20, 0)),    # 16:00 EDT
    "@RY.TO":       ("XTSE", u(2026, 10, 9, 20, 0)),    # 16:00 EDT
    "@ES=F":        ("CME_GLOBEX", u(2026, 10, 9, 21, 0)),   # Friday: 16:00 CDT, no evening
}


@pytest.fixture
def world(monkeypatch):
    from src.v5 import marketdata as md
    idx = pd.bdate_range("2026-09-14", "2026-12-31")
    frames = {s: pd.DataFrame({"Close": [float(i + 1) for i in range(len(idx))]}, index=idx)
              for s in MARKETS}
    monkeypatch.setattr(md, "_full_history", lambda symbol, interval: frames[symbol])
    return md, frames


def _seen(md, when):
    """symbol -> (last visible date, checksum of everything visible)."""
    out = {}
    with md.as_of(pd.Timestamp(when)):
        for sym in MARKETS:
            h = md.history(sym, period="10y")
            out[sym] = (h.index.max().date(), float(h["Close"].sum()))
    return out


def test_the_hand_written_closes_are_what_the_engine_reports():
    for sym, (venue, close) in MARKETS.items():
        assert S.resolve(sym[1:]).venue == venue, sym
        if venue != "CME_GLOBEX":
            assert S.bar_available_at(venue, FRI) == close, (sym, S.bar_available_at(venue, FRI))
    # Futures: the bar is not available before the contract's 16:00 CT close.
    assert S.bar_available_at("CME_GLOBEX", FRI) >= MARKETS["@ES=F"][1]


INSTANTS = sorted({c for _, c in MARKETS.values()} |
                  {c - timedelta(minutes=1) for _, c in MARKETS.values()} |
                  {u(2026, 10, 9, 0, 0), u(2026, 10, 9, 12, 0), u(2026, 10, 10, 6, 0)})


@pytest.mark.parametrize("when", INSTANTS, ids=[t.strftime("%H:%M") for t in INSTANTS])
def test_each_market_is_seen_exactly_when_its_session_has_closed(world, when):
    md, frames = world
    seen = _seen(md, when)
    for sym, (venue, close) in MARKETS.items():
        expect_fri = when >= close or (venue == "CME_GLOBEX" and
                                       when >= S.bar_available_at(venue, FRI))
        assert (seen[sym][0] == FRI) is expect_fri, (sym, when, seen[sym][0])
        assert seen[sym][0] <= FRI


@pytest.mark.parametrize("when", INSTANTS, ids=[t.strftime("%H:%M") for t in INSTANTS])
def test_rewriting_an_unclosed_session_changes_nothing_and_a_closed_one_does(world, when):
    md, frames = world
    before = _seen(md, when)
    open_syms = [s for s in MARKETS if before[s][0] < FRI]
    closed_syms = [s for s in MARKETS if before[s][0] == FRI]
    for sym in open_syms:                     # the future, as far as T is concerned
        frames[sym].loc[pd.Timestamp(FRI), "Close"] = 1e12
        frames[sym].loc[pd.Timestamp(FRI) + pd.offsets.BDay(1), "Close"] = -1e12
    assert _seen(md, when) == before, when
    for sym in closed_syms:                   # the past: the check must be able to fail
        frames[sym].loc[pd.Timestamp(FRI), "Close"] = 7e11
        assert _seen(md, when)[sym] != before[sym], (sym, when)


def test_cross_market_same_date_ordering(world):
    """At 07:30 UTC the Asia-Pacific closes of the day are knowable and the
    rest of the world's are not — a signal built then may use Tokyo's Friday
    but not New York's."""
    md, _ = world
    seen = _seen(md, u(2026, 10, 9, 7, 30))
    known = {s for s in MARKETS if seen[s][0] == FRI}
    assert known == {"@BHP.AX", "@7203.T", "@600519.SS", "@000001.SZ"}


def test_lunch_breaks_are_not_closes(world):
    md, frames = world
    for when, sym in ((u(2026, 10, 9, 3, 0), "@7203.T"),        # 12:00 JST
                      (u(2026, 10, 9, 4, 0), "@000001.SZ"),     # 12:00 CST
                      (u(2026, 10, 9, 4, 30), "@0005.HK")):     # 12:30 HKT
        before = _seen(md, when)
        assert before[sym][0] < FRI, (sym, when)
        frames[sym].loc[pd.Timestamp(FRI), "Close"] = 9e11      # the morning's partial bar
        assert _seen(md, when) == before


def test_dst_mismatch_weeks(world):
    """Autumn: the UK is on GMT from 25 Oct, New York on EDT until 1 Nov.
    London's Tuesday close is 16:35 UTC that week, not 15:35 — a replay that
    assumed summer time would see a bar that did not exist yet."""
    md, frames = world
    tue = date(2026, 10, 27)
    seen = _seen(md, u(2026, 10, 27, 16, 0))
    assert seen["@BARC.L"][0] < tue and seen["@SAP.DE"][0] < tue   # Europe on CET too
    frames["@BARC.L"].loc[pd.Timestamp(tue), "Close"] = 5e11
    assert _seen(md, u(2026, 10, 27, 16, 0)) == seen
    assert _seen(md, u(2026, 10, 27, 16, 35))["@BARC.L"][0] == tue
    assert _seen(md, u(2026, 10, 27, 19, 59))["@AAPL"][0] < tue
    assert _seen(md, u(2026, 10, 27, 20, 0))["@AAPL"][0] == tue
    # Spring: New York on EDT from 8 Mar, London still GMT until 29 Mar.
    assert S.bar_available_at("XLON", date(2026, 3, 17)) == u(2026, 3, 17, 16, 35)
    assert S.bar_available_at("XNYS", date(2026, 3, 17)) == u(2026, 3, 17, 20, 0)


def test_half_days_release_the_bar_at_the_early_close(world):
    md, _ = world
    black_friday = date(2026, 11, 27)                       # NYSE 13:00 EST = 18:00 UTC
    assert _seen(md, u(2026, 11, 27, 17, 59))["@AAPL"][0] < black_friday
    assert _seen(md, u(2026, 11, 27, 18, 0))["@AAPL"][0] == black_friday
    xmas_eve = date(2026, 12, 24)                           # HKEX 12:00 + CAS to 12:10 HKT
    assert S.bar_available_at("XHKG", xmas_eve) == u(2026, 12, 24, 4, 10)
    assert _seen(md, u(2026, 12, 24, 4, 9))["@0005.HK"][0] < xmas_eve
    assert _seen(md, u(2026, 12, 24, 4, 10))["@0005.HK"][0] == xmas_eve


def test_a_bar_dated_on_a_venue_holiday_is_never_seen_early(world):
    """Bad data — a row on Thanksgiving, or inside China's Golden Week — has
    no session close. It counts as known only once its date is over
    everywhere (00:00 UTC two days later), never at a guessed 16:00."""
    md, frames = world
    tg = date(2026, 11, 26)
    assert S.bar_available_at("XNYS", tg) is None
    frames["@AAPL"].loc[pd.Timestamp(tg), "Close"] = 3e11
    assert _seen(md, u(2026, 11, 27, 23, 59))["@AAPL"][1] < 3e11
    gw = date(2026, 10, 6)
    assert S.bar_available_at("XSHE", gw) is None
    assert _seen(md, u(2026, 10, 7, 23, 59))["@000001.SZ"][0] < gw
    assert _seen(md, u(2026, 10, 8, 0, 0))["@000001.SZ"][0] >= gw


def test_overnight_futures_are_not_seen_mid_session(world):
    """CME's Thursday bar is not knowable while Thursday's session trades,
    nor at the 16:00 CT settlement in this engine (it waits for the calendar
    date's last phase, 24:00 CT) — late, never early."""
    md, _ = world
    thu = date(2026, 10, 8)
    assert _seen(md, u(2026, 10, 8, 15, 0))["@ES=F"][0] < thu          # 10:00 CDT
    assert _seen(md, u(2026, 10, 8, 21, 30))["@ES=F"][0] < thu         # after settlement
    assert S.bar_available_at("CME_GLOBEX", thu) == u(2026, 10, 9, 5, 0)
    assert _seen(md, u(2026, 10, 9, 5, 0))["@ES=F"][0] == thu


def test_an_unknown_nse_day_is_never_seen_early(world):
    """Sunday 8 Nov 2026: NSE's Muhurat session has no hours yet, so a bar
    dated that day waits for the date to be over everywhere."""
    md, frames = world
    muhurat = date(2026, 11, 8)
    assert S.bar_available_at("XNSE", muhurat) is None
    frames["@RELIANCE.NS"].loc[pd.Timestamp(muhurat)] = 4e11
    frames["@RELIANCE.NS"].sort_index(inplace=True)
    assert _seen(md, u(2026, 11, 9, 23, 59))["@RELIANCE.NS"][1] < 4e11
    assert _seen(md, u(2026, 11, 10, 0, 0))["@RELIANCE.NS"][1] > 4e11
