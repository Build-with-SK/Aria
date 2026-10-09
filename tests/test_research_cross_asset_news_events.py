"""
tests/test_research_cross_asset_news_events.py
==============================================
Cross-asset states (association, never cause), news as one item per STORY,
and events that are visible only once knowable.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("exchange_calendars")

import research_fixtures as F  # noqa: E402
from src.intelligence.research import cross_asset as X  # noqa: E402
from src.intelligence.research import events as E  # noqa: E402
from src.intelligence.research import news as N  # noqa: E402
from src.intelligence.research.pit_data import (Event, NewsItem, visible_events,  # noqa: E402
                                                visible_news)

IDX = pd.bdate_range("2024-01-01", periods=400)


def series(x):
    return pd.Series(x, index=IDX[:len(x)])


# ── cross-asset ─────────────────────────────────────────────────────────────

def test_a_tightly_related_pair_is_correlated():
    rng = np.random.default_rng(1)
    x = rng.normal(0, 0.01, 400)
    out = X.pair_state(series(0.9 * x + rng.normal(0, 0.003, 400)), series(x),
                       frequency="daily", same_clock=True)
    assert out["state"] == "CORRELATED" and out["corr_recent"] > 0.8


def test_a_flipped_relationship_is_a_breakdown():
    rng = np.random.default_rng(2)
    x = rng.normal(0, 0.01, 400)
    y = np.where(np.arange(400) < 337, 0.9 * x, -0.9 * x) + rng.normal(0, 0.002, 400)
    out = X.pair_state(series(y), series(x), frequency="daily", same_clock=True)
    assert out["state"] == "BREAKDOWN" and out["p_change"] < 0.01


def test_a_move_away_from_the_implied_one_is_diverging():
    rng = np.random.default_rng(3)
    x = rng.normal(0, 0.01, 400)
    y = 0.9 * x + rng.normal(0, 0.003, 400)
    y[-21:] += 0.01                        # a 21% gap the pair does not explain
    out = X.pair_state(series(y), series(x), frequency="daily", same_clock=True)
    assert out["state"] == "DIVERGING" and out["divergence_z"] > 2


def test_independent_series_are_uncorrelated_and_show_no_lead_lag():
    rng = np.random.default_rng(4)
    out = X.pair_state(series(rng.normal(0, 0.01, 400)), series(rng.normal(0, 0.01, 400)),
                       frequency="daily", same_clock=True)
    assert out["state"] == "UNCORRELATED"
    assert out["lead_lag"]["state"] == "NO_DEFENSIBLE_LEAD_LAG"


def test_a_real_lag_is_found_and_still_not_called_a_cause():
    rng = np.random.default_rng(5)
    x = rng.normal(0, 0.01, 400)
    y = np.r_[0.0, 0.8 * x[:-1]] + rng.normal(0, 0.004, 400)
    out = X.pair_state(series(y), series(x), frequency="daily", same_clock=True)
    ll = out["lead_lag"]
    assert ll["state"] == "LEAD_LAG_OBSERVED"
    assert any(l["lag"] == 1 and l["direction"] == "pair leads" for l in ll["lags"])
    assert "not a cause" in ll["caveat"]


def test_different_clocks_get_no_lead_lag():
    rng = np.random.default_rng(6)
    x = rng.normal(0, 0.01, 400)
    out = X.pair_state(series(x), series(x), frequency="weekly", same_clock=False)
    assert out["lead_lag"]["state"] == "NOT_APPLICABLE"


def test_too_short_a_history_is_insufficient():
    out = X.pair_state(series(np.zeros(50) + 0.001), series(np.ones(50) * 0.001),
                       frequency="daily", same_clock=True)
    assert out["state"] == "INSUFFICIENT_DATA"


def test_pairs_follow_asset_class_and_sector_and_exclude_the_instrument():
    assert ("USO", "crude oil") in X.pairs_for("XOM", "equity", "Energy")
    assert all(p != "SPY" for p, _ in X.pairs_for("SPY", "equity", None))
    assert X.pairs_for("GLD", "equity", None)[0][0] == "TIP"
    assert X.pairs_for("ZZZ", "options", None) == []


def test_the_engine_reports_states_on_the_fixture_market():
    from src.risk.market_data import FrameMarketData
    out = X.analyse("AAPL", F.T, FrameMarketData(F.build_frames()), asset_class="equity",
                    sector="Technology")
    # The fixture plants an idiosyncratic jump in AAPL's last 21 sessions: its
    # move departs from what its beta to SPY implies, and that is detected.
    assert out["pairs"]["SPY"]["state"] == "DIVERGING"
    assert out["pairs"]["SPY"]["divergence_z"] > 2
    assert out["pairs"]["^VIX"]["state"] in ("CORRELATED", "DIVERGING", "BREAKDOWN",
                                              "UNCORRELATED")
    assert "nothing here asserts a cause" in out["caveat"]


# ── news ────────────────────────────────────────────────────────────────────

def analyse(items, at=F.T):
    return N.analyse(visible_news(items, at), at, provider_status={})


def test_fifteen_outlets_carrying_one_story_are_one_item_of_evidence():
    out = analyse(F.news())
    big = max(out["story_clusters"], key=lambda c: c["items"])
    assert big["items"] == 16 and big["publishers"] == 15
    assert out["syndicated_copies_collapsed"] == out["items"] - out["clusters"]
    recs = [r for r in out["item_records"] if r["duplicate_cluster"] == big["cluster_id"]]
    novelty = [r["novelty"] for r in recs]
    assert novelty.count("ORIGINAL") == 1 and novelty.count("REPEATED") == 1
    assert novelty.count("SYNDICATED") == 14


def test_news_from_after_t_or_seen_after_t_is_invisible():
    out = analyse(F.news())
    heads = {r["headline"] for r in out["item_records"]}
    assert "Apple (AAPL) shares plunge after supply warning" not in heads
    assert "Apple (AAPL) wins approval in EU court" not in heads


def test_a_headline_about_something_else_is_kept_but_not_evidence():
    out = analyse(F.news())
    weak = [c for c in out["story_clusters"] if c["relevance"] == "LOW"]
    assert weak and out["excluded_weak_association"] == len(weak)
    assert out["independent_stories"] == out["clusters"] - len(weak)


def test_freshness_and_new_developments():
    out = analyse(F.news())
    by = {c["headline"][:20]: c for c in out["story_clusters"]}
    assert by["Apple (AAPL) faces a"]["freshness"] == "STALE"
    assert by["Apple (AAPL) raises "]["freshness"] == "FRESH"
    later = NewsItem("n900", "Apple (AAPL) regulators open second antitrust probe", "wire",
                     "https://x/900", "googlenews", F.at_utc(2026, 9, 15, 13),
                     F.at_utc(2026, 9, 15, 13, 1), "AAPL")
    out2 = analyse(F.news() + [later])
    new = [c for c in out2["story_clusters"] if c["headline"].startswith("Apple (AAPL) regulators")]
    assert new[0]["story_novelty"] == "NEW_DEVELOPMENT"


def test_classifications_say_they_are_rules_not_a_model():
    out = analyse(F.news())
    assert out["method"]["model_derived"] is False
    r = out["item_records"][0]
    assert r["direction"]["method"].startswith("LEXICON")
    assert r["event_type_basis"].startswith("KEYWORD_RULES")
    assert N.classify_type("Apple raises its outlook")[0] == "GUIDANCE"
    assert N.classify_direction("Shares surge to a record")["direction"] == "POSITIVE"
    assert N.classify_direction("Quarter results")["direction"] == "NONE"


# ── events ──────────────────────────────────────────────────────────────────

def test_event_visibility_rules():
    vis = {e.event_id for e in visible_events(F.events(), F.T)}
    assert "EARNINGS:AAPL:2026-07-30" in vis                  # occurred: public
    assert "EARNINGS:AAPL:2026-10-01" in vis                  # schedule observed before T
    assert "EARNINGS:AAPL:2026-09-30-late" not in vis         # observed after T
    assert "SPLIT:AAPL:2026-09-25" not in vis                 # future, no publication time
    assert "EARNINGS:AAPL:2026-09-22-withdrawn" not in vis    # withdrawn before T


def test_outcomes_are_hidden_until_the_event_happens():
    ev = next(e for e in F.events() if e.event_id == "EARNINGS:AAPL:2026-07-30")
    before = ev.view_at(F.at_utc(2026, 7, 29))
    after = ev.view_at(F.T)
    assert before["status"] == "SCHEDULED" and before["outcome"] == {}
    assert after["outcome"]["surprise_pct"] == 6.9


def test_a_relisted_schedule_is_invisible_in_the_gap_and_visible_after():
    e = Event("E", "EARNINGS", "AAPL", F.at_utc(2026, 12, 1), "v", "HIGH",
              observed_at=F.at_utc(2026, 9, 1), withdrawn_at=F.at_utc(2026, 9, 10),
              relisted_at=F.at_utc(2026, 9, 20))
    assert e.visible_at(F.at_utc(2026, 9, 5))
    assert not e.visible_at(F.at_utc(2026, 9, 15))
    assert e.visible_at(F.at_utc(2026, 9, 25))


def test_event_risk_states():
    vis = visible_events(F.events(), F.T)
    ctx = E.context(vis, F.T, horizon_days=21, provider_ok=True)
    assert ctx["event_risk"]["state"] == "HIGH"
    assert ctx["last_earnings_surprise"]["outcome"]["surprise_pct"] == 6.9
    quiet = E.context([e for e in vis if e.kind != "EARNINGS"], F.T, horizon_days=21,
                      provider_ok=True)
    assert quiet["event_risk"]["state"] == "LOW"
    failed = E.context([], F.T, horizon_days=21, provider_ok=False)
    assert failed["event_risk"]["state"] == "UNKNOWN"


def test_a_corporate_action_inside_the_window_is_flagged():
    vis = visible_events(F.events(), F.T)
    ctx = E.context(vis, F.T, horizon_days=21, provider_ok=True,
                    window_start=F.at_utc(2026, 8, 1))
    assert [e["kind"] for e in ctx["corporate_actions_in_window"]] == ["DIVIDEND"]


def test_macro_releases_become_events_at_their_known_time():
    evs = E.macro_release_events(F.macro())
    later = next(e for e in evs if e.outcome["observation_date"] == "2026-09-30")
    assert not later.visible_at(F.T)
    assert later.outcome["known_at_basis"].startswith("retrieval time")
