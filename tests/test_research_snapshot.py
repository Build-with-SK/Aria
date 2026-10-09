"""
tests/test_research_snapshot.py
===============================
The canonical point-in-time research snapshot: every component carries its
own as_of, source, freshness and PIT status; nothing from after T is used;
missing, stale, failed and conflicting inputs are explicit, never filled.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("exchange_calendars")

import research_fixtures as F  # noqa: E402
from src.intelligence.research import components as C  # noqa: E402
from src.intelligence.research import snapshot as S  # noqa: E402
from src.intelligence.research.pit_data import FundamentalSnapshot  # noqa: E402

NY = ZoneInfo("America/New_York")


@pytest.fixture(scope="module")
def frames():
    return F.build_frames()


def snap(frames, at=F.T, symbol="AAPL", **kw):
    return S.build(symbol, at, F.world(frames=frames, **kw), cross_asset=False)


def test_every_component_says_when_where_and_whether_it_is_point_in_time(frames):
    s = snap(frames)
    expected = {"market_session", "price", "returns", "volume", "volatility", "liquidity",
                "technical", "fundamentals", "macro", "regime", "factors", "event_risk",
                "news", "sentiment"}
    assert expected <= set(s.components)
    for name, c in s.components.items():
        assert c.freshness in C.FRESHNESS, name
        assert c.pit in (C.PIT_BY_CONSTRUCTION, C.PIT_VERIFIED, C.LIVE_ONLY, C.UNAVAILABLE), name
        if c.value is not None and name != "market_session":
            assert c.as_of and c.source is not None, name
    d = s.to_dict()
    assert d["information_time"] == F.T.isoformat() and len(d["digest"]) == 16


def test_the_price_is_the_last_bar_known_at_t(frames):
    s = snap(frames)
    assert s.get("price").as_of == "2026-09-15"
    assert s.get("price").freshness == C.FRESH


def test_inside_the_session_todays_bar_is_not_used(frames):
    inside = datetime(2026, 9, 15, 14, 0, tzinfo=NY)
    s = snap(frames, at=inside)
    assert s.get("price").as_of == "2026-09-14"
    assert s.get("price").freshness == C.FRESH        # yesterday IS the latest completed


def test_a_holiday_expects_the_previous_session(frames):
    thanksgiving = datetime(2026, 11, 26, 18, 0, tzinfo=NY)
    s = snap(frames, at=thanksgiving)
    assert s.get("price").as_of == "2026-11-25" and s.get("price").freshness == C.FRESH


def test_a_feed_that_stopped_is_stale_not_current(frames):
    cut = {k: v[v.index <= "2026-09-08"] for k, v in frames.items()}
    s = snap(cut)
    assert s.get("price").as_of == "2026-09-08" and s.get("price").freshness == C.STALE


def test_no_bars_means_missing_price_with_the_reason(frames):
    s = snap(frames, symbol="ZZZQ")
    for name in ("price", "returns", "volatility", "technical"):
        assert s.get(name).freshness == C.MISSING and s.get(name).missing


def test_fundamentals_before_the_first_snapshot_are_missing_not_todays(frames):
    s = snap(frames, at=datetime(2026, 5, 1, 21, tzinfo=timezone.utc))
    f = s.get("fundamentals")
    assert f.value is None and f.freshness == C.MISSING
    assert "today's numbers are not used" in f.missing


def test_fundamentals_use_the_latest_snapshot_taken_by_t_only(frames):
    f = snap(frames).get("fundamentals")
    assert f.value["revenueGrowth"] == 0.05          # 1 Sep, not the 20 Sep -0.40
    assert f.as_of.startswith("2026-09-01") and f.pit == C.PIT_VERIFIED
    assert f.freshness == C.STALE                    # 14 days old


def test_conflicting_fundamental_sources_are_not_resolved_silently(frames):
    extra = FundamentalSnapshot("AAPL", {"revenueGrowth": 0.20, "trailingPE": 30.0},
                                F.at_utc(2026, 9, 2), "vendor-b")
    f = snap(frames, fundamentals=F.fundamentals() + [extra]).get("fundamentals")
    assert f.freshness == C.CONFLICTED
    assert f.value["revenueGrowth"] is None
    assert set(f.provenance["conflicts"]["revenueGrowth"]) == {"vendor-a", "vendor-b"}


def test_crypto_has_no_accounts(frames):
    f = snap(frames, symbol="BTC-USD").get("fundamentals")
    assert f.freshness == C.NO_DATA and f.value is None


def test_macro_uses_only_releases_known_at_t_and_says_how_they_were_dated(frames):
    m = snap(frames).get("macro")
    assert m.value["series"]["cpi_yoy"]["value"] == 3.1      # not the October 4.9
    assert m.value["series"]["cpi_yoy"]["known_at_basis"].startswith("retrieval time")
    assert set(m.value["regime"]["assumed_inputs"]) == {"10Y-2Y spread", "VIX"}


@pytest.mark.parametrize("which,component", [("news", "news"), ("events", "event_risk"),
                                             ("fundamentals", "fundamentals"),
                                             ("macro", "macro")])
def test_a_failed_provider_is_missing_with_its_reason(frames, which, component):
    s = snap(frames, failures={which: "vendor timeout"})
    c = s.get(component)
    assert c.freshness == C.MISSING
    assert "failed" in c.missing
    assert "vendor timeout" in s.provenance["provider_failures"][which]
    if which == "events":
        assert c.value["event_risk"]["state"] == "UNKNOWN"


def test_a_working_provider_with_nothing_to_say_is_no_data_not_missing(frames):
    n = snap(frames, news=[]).get("news")
    assert n.freshness == C.NO_DATA


def test_history_failure_is_explicit(frames):
    s = snap(frames, failures={"history": "vendor down"})
    assert s.get("price").freshness == C.MISSING and "vendor down" in s.get("price").missing


def test_sentiment_is_declared_dependent_on_news(frames):
    s = snap(frames).get("sentiment")
    assert s.provenance["independent_of_news"] is False
