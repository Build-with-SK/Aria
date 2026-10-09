"""
tests/test_research_evidence_thesis.py
======================================
Correlated evidence is not counted as independent; the thesis direction comes
from independent groups; there is no confidence number; analogues are point
in time or explicitly unavailable.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("exchange_calendars")

import research_fixtures as F  # noqa: E402
from src.intelligence.research import analogues as A  # noqa: E402
from src.intelligence.research import engine  # noqa: E402
from src.intelligence.research import evidence_graph as G  # noqa: E402
from src.intelligence.research import thesis as T  # noqa: E402
from src.intelligence.research.sources import GROUPS, MODEL_GROUP, SOURCES  # noqa: E402


def item(i, group, d, root=None):
    return G.EvidenceItem(f"i{i}", f"item {i}", group, root or f"ROOT:{group}", d, None, "",
                          None, "FRESH", G.UNVALIDATED, "test", "rule", "why")


# ── the audit catalogue ─────────────────────────────────────────────────────

def test_every_source_is_audited_with_a_group_and_a_pit_status():
    for name, s in SOURCES.items():
        for k in ("input", "timestamp", "pit", "provenance", "group", "root",
                  "independent", "used_by"):
            assert k in s, (name, k)
        assert s["group"] in GROUPS + ("—",), name
    assert SOURCES["technical_indicators"]["independent"].startswith("NO")
    assert SOURCES["fundamentals"]["pit"] == "LIVE_ONLY"


def test_price_derived_models_are_grouped_by_their_inputs():
    assert MODEL_GROUP["technical"] == "PRICE_DERIVED"
    assert MODEL_GROUP["ml-ensemble"] == "PRICE_DERIVED"
    assert MODEL_GROUP["v5"] == "MODEL"


# ── independence ────────────────────────────────────────────────────────────

def test_five_price_indicators_are_one_group_not_five_confirmations():
    items = [item(k, "PRICE_DERIVED", 1, "PRICE:AAPL") for k in range(5)]
    ag = G.agreement(items)
    assert ag["raw"]["bullish"] == 5
    assert ag["independent"]["bullish_groups"] == ["PRICE_DERIVED"]
    label, basis, _ = T.direction_of(ag)
    assert (label, basis) == ("BULLISH", "SINGLE_EVIDENCE_GROUP")


def test_independent_groups_that_disagree_are_conflicted_whatever_the_counts():
    items = [item(k, "PRICE_DERIVED", 1) for k in range(6)] + [item(9, "NEWS", -1),
                                                               item(10, "FUNDAMENTAL", -1)]
    ag = G.agreement(items)
    assert T.direction_of(ag) == ("NEUTRAL", "CONFLICTED", 0)


def test_two_independent_groups_agreeing_is_multiple_group_support():
    items = [item(1, "PRICE_DERIVED", -1), item(2, "FUNDAMENTAL", -1), item(3, "EVENT", 0)]
    assert T.direction_of(G.agreement(items))[:2] == ("BEARISH", "MULTIPLE_INDEPENDENT_GROUPS")


def test_a_split_group_is_mixed_and_does_not_vote():
    g = G.group_direction([item(1, "NEWS", 1), item(2, "NEWS", -1)])
    assert g["state"] == "MIXED" and g["direction"] == 0


def test_price_indicator_dependence_is_measured_not_assumed():
    rng = np.random.default_rng(3)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.012, 600))),
                      index=pd.bdate_range("2024-01-01", periods=600))
    dep = G.price_dependence(close)
    assert dep["state"] == "MEASURED"
    assert 1.0 <= dep["effective_independent_signals"] < len(dep["indicators"])
    assert dep["mean_pairwise_corr"] > 0.2


def test_dependence_needs_history():
    close = pd.Series(np.linspace(100, 110, 100), index=pd.bdate_range("2024-01-01", periods=100))
    assert G.price_dependence(close)["state"] == "INSUFFICIENT_DATA"


def test_cross_asset_spy_is_market_not_counted_again():
    items = G.cross_asset_items({"pairs": {"SPY": {"state": "CORRELATED", "pair_move": 0.05,
                                                   "beta_long": 1.0, "role": "m"},
                                           "TLT": {"state": "CORRELATED", "pair_move": -0.04,
                                                   "beta_long": 0.5, "role": "bonds"}}},
                                as_of=None, freshness="FRESH")
    assert [i.root for i in items] == ["PRICE:TLT"]
    assert items[0].direction == -1


def test_news_evidence_is_per_story():
    news = {"story_clusters": [
        {"cluster_id": 0, "relevance": "HIGH", "direction": "POSITIVE", "event_type": "GUIDANCE",
         "items": 16, "first_known_at": "x", "freshness": "FRESH", "publishers": 15,
         "best_source": {"tier": "aggregator"}, "headline": "h", "story_novelty": "ORIGINAL_TOPIC"},
        {"cluster_id": 1, "relevance": "LOW", "direction": "NEGATIVE", "event_type": "OTHER",
         "items": 1, "first_known_at": "x", "freshness": "FRESH", "publishers": 1,
         "best_source": {"tier": "unknown"}, "headline": "h2", "story_novelty": "ORIGINAL_TOPIC"}]}
    items = G.news_items(news, as_of=None)
    assert len(items) == 1 and items[0].detail["syndicated_copies"] == 15


# ── the thesis ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def report():
    return engine.research("AAPL", at=F.T, data=F.world(), horizon_days=21)


def test_the_thesis_has_no_confidence_number_and_is_not_executable(report):
    th = report["thesis"]
    assert th["confidence"] is None and th["executable"] is False and th["may_size"] is False
    assert th["direction"] in T.DIRECTIONS
    assert set(th["quality_scorecard"]) == {"DATA_QUALITY", "EVIDENCE_DIVERSITY",
                                            "EVIDENCE_INDEPENDENCE", "HISTORICAL_SUPPORT",
                                            "REGIME_MATCH", "EVENT_RISK", "FRESHNESS",
                                            "CONTRADICTION", "PIT_INTEGRITY"}
    assert "%" not in th["summary"]


def test_the_thesis_explains_support_contradiction_and_invalidation(report):
    th = report["thesis"]
    assert th["supporting_evidence"] and "explanation" in th["supporting_evidence"][0]
    kinds = {v["kind"] for v in th["invalidators"]}
    assert {"PRICE_LEVEL", "TREND", "EVENT", "REGIME"} <= kinds
    assert any("2026-10-01" in v["condition"] for v in th["invalidators"])
    assert th["unexplained_residual"]["label"] == "UNEXPLAINED_RESIDUAL"
    assert th["quality_scorecard"]["EVENT_RISK"]["state"] == "HIGH"
    assert th["quality_scorecard"]["PIT_INTEGRITY"]["state"] == "VALID"


def test_recorded_model_calls_are_listed_with_their_types_and_routing(report):
    models = {m["model_id"]: m for m in report["model_predictions"]}
    assert set(models) == {"technical", "v5"}
    assert models["technical"]["probability_type"] == "NONE"
    assert report["routing"]["reason_code"] == "ROUTING_INSUFFICIENT_EVIDENCE"
    groups = {i["item_id"]: i["group"] for i in report["evidence"]["items"]}
    assert groups["model.technical"] == "PRICE_DERIVED"


def test_the_pipeline_stops_before_conviction(report):
    p = report["pipeline"]
    assert p["never_called"] == ["CONVICTION", "PORTFOLIO_RISK", "SESSION", "EXECUTION"]


def test_no_bars_gives_a_neutral_thesis_with_missing_data():
    rep = engine.research("ZZZQ", at=F.T, data=F.world(), horizon_days=21)
    th = rep["thesis"]
    assert th["quality_scorecard"]["DATA_QUALITY"]["state"] == "MISSING"
    assert rep["analogues"]["status"] == A.INSUFFICIENT


# ── analogues ───────────────────────────────────────────────────────────────

def _bars(frames, at=F.T):
    from src.risk.market_data import FrameMarketData
    md = FrameMarketData(frames)
    a, b = md.history("AAPL", at), md.history("SPY", at)
    return a["Close"], b["Close"], a["Volume"]


def test_analogues_only_use_outcomes_known_at_t_and_do_not_overlap():
    c, b, v = _bars(F.build_frames())
    out = A.find(c, b, v, horizon=21)
    assert out["status"] == "MEASURED" and out["analogue_count"] >= A.MIN_ANALOGUES
    dates = pd.to_datetime(out["sample_quality"]["dates"])
    pos = sorted(c.index.get_indexer(dates))
    assert all(b2 - a2 >= 21 for a2, b2 in zip(pos, pos[1:]))
    assert max(pos) + 21 <= len(c) - 1
    assert out["predictive_value"] in ("NOT_DEMONSTRATED",
                                       "DIFFERS_FROM_BASE_RATE (in-sample, not validated "
                                       "out of sample)")


def test_short_history_is_insufficient():
    c, b, v = _bars(F.build_frames())
    assert A.find(c.iloc[-300:], b.iloc[-300:], v.iloc[-300:])["status"] == A.INSUFFICIENT


def test_future_bars_cannot_change_the_analogues():
    f = F.build_frames()
    base = A.find(*_bars(f))
    g = {k: v.copy() for k, v in f.items()}
    for v in g.values():
        v.loc[v.index > "2026-09-15", "Close"] *= 0.1
    assert A.find(*_bars(g)) == base


def test_past_bars_can_change_the_analogues():
    f = F.build_frames()
    base = A.find(*_bars(f))
    g = {k: v.copy() for k, v in f.items()}
    g["AAPL"].loc[g["AAPL"].index < "2025-01-01", "Close"] *= 1.5
    assert A.find(*_bars(g)) != base
