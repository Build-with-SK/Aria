"""
tests/test_research_quality.py
==============================
Source quality and claim type — Phases 2, 11, 14 and 46.

§14 is the requirement being defended: ARIA must not treat an internet claim as
true because it exists. The tests below pin the three properties that make that
real rather than decorative —

  1. the three axes stay separate (salience, source tier, claim type);
  2. the cautious reading wins when a headline carries two markers;
  3. UNCLASSIFIED is a real outcome and never silently becomes FACT.

The classifier is deterministic pattern matching over a headline. These tests
also pin that it ADMITS that, by requiring a confidence and a named signal on
every verdict.
"""
from __future__ import annotations

import pytest

from src.research import quality as q


# ── the evidence ladder (§46) ───────────────────────────────────────────────

def test_a_regulatory_filing_outranks_everything():
    t = q.tier_for("edgar", "https://www.sec.gov/Archives/edgar/x.htm")
    assert t["tier"] == "primary_filing"
    assert t["rank"] == max(v[0] for v in q.TIERS.values())


def test_a_reuters_story_via_google_news_is_not_an_aggregator():
    """Google News is an aggregator; a Reuters story arriving through it is
    still Reuters, and flattening that loses the distinction §46 asks for."""
    t = q.tier_for("googlenews", "https://www.reuters.com/markets/x")
    assert t["tier"] == "quality_press"
    assert "reuters.com" in t["basis"]


def test_the_domain_does_not_downgrade_a_stronger_backend():
    """A filing delivered from an odd host must not be demoted below the
    backend that fetched it."""
    t = q.tier_for("edgar", "https://someproxy.example.com/filing")
    assert t["tier"] == "primary_filing"


def test_reddit_is_anonymous_not_merely_social():
    assert q.tier_for("reddit", "https://www.reddit.com/r/options/x")["tier"] == "anonymous"


def test_an_unknown_source_is_unknown_not_assumed_good():
    t = q.tier_for("some-new-scraper", None)
    assert t["tier"] == "unknown" and t["rank"] == 0


# ── claim type (§2, §14) ────────────────────────────────────────────────────

def test_a_filing_is_a_fact_about_the_filing_not_about_the_future():
    c = q.classify_claim("Apple Inc. files Form 8-K", "edgar",
                         "https://www.sec.gov/x", {"form": "8-K"})
    assert c["claim_type"] == "FACT"
    assert "not that its forward-looking content is true" in c["caveat"]


def test_hedged_attribution_is_a_rumour():
    c = q.classify_claim("Sources say Nvidia is preparing a recall", "googlenews",
                         "https://www.reuters.com/x")
    assert c["claim_type"] == "RUMOUR"


def test_the_cautious_reading_wins_when_markers_compete():
    """'Sources say X could miss forecasts' carries both a rumour marker and a
    forecast marker. Rumour is the safer of the two readings, so rumour wins —
    the cost of scepticism about something true is far lower than the cost of
    trading on a rumour."""
    c = q.classify_claim("Sources say Nvidia could miss Q4 forecasts",
                         "googlenews", "https://www.reuters.com/x")
    assert c["claim_type"] == "RUMOUR"


def test_an_analyst_view_is_an_inference_not_a_fact():
    c = q.classify_claim("Analysts expect Tesla to beat estimates", "googlenews",
                         "https://www.cnbc.com/x")
    assert c["claim_type"] == "INFERENCE"


def test_research_language_is_a_hypothesis():
    c = q.classify_claim("This paper proposes a new volatility estimator",
                         "web", "https://arxiv.org/abs/1234")
    assert c["claim_type"] == "HYPOTHESIS"


def test_an_unmarked_anonymous_post_is_a_claim_not_unclassified():
    c = q.classify_claim("$TSLA is ridiculously overvalued.", "reddit",
                         "https://www.reddit.com/r/x")
    assert c["claim_type"] == "CLAIM"
    assert "one person's opinion" in c["caveat"]


def test_an_unmarked_headline_is_unclassified_never_fact():
    """The single most important refusal in this module."""
    c = q.classify_claim("Some headline with no markers at all", "googlenews",
                         "https://example.com/x")
    assert c["claim_type"] == "UNCLASSIFIED"
    assert c["confidence"] == 0.0
    assert "not the same as true" in c["caveat"]


def test_every_verdict_carries_a_confidence_and_a_named_signal():
    """A classifier that will not show its working cannot be argued with."""
    for title, src, url in [
        ("Apple files Form 8-K", "edgar", "https://www.sec.gov/x"),
        ("Rumoured merger talks", "googlenews", "https://www.ft.com/x"),
        ("Nothing notable here", "web", "https://example.com/x"),
    ]:
        c = q.classify_claim(title, src, url)
        assert c["claim_type"] in q.CLAIM_TYPES
        assert isinstance(c["confidence"], float)
        assert c["signal"]


def test_the_same_words_are_trusted_less_from_an_anonymous_source():
    """A marker in a headline means less from an unattributed post."""
    press = q.classify_claim("Rumoured buyout of the company", "googlenews",
                             "https://www.reuters.com/x")
    anon = q.classify_claim("Rumoured buyout of the company", "reddit",
                            "https://www.reddit.com/r/x")
    assert anon["confidence"] < press["confidence"]
    assert anon["claim_type"] == press["claim_type"] == "RUMOUR"


# ── the axes stay separate ──────────────────────────────────────────────────

def test_tier_and_claim_type_are_independent():
    """A rumour in the FT is high tier and still a rumour; a filing is low
    salience and the strongest evidence there is. Merging the axes would lose
    exactly this."""
    c = q.classify_claim("Sources say a deal is near", "googlenews",
                         "https://www.ft.com/x")
    assert c["tier"] == "quality_press" and c["rank"] >= 4
    assert c["claim_type"] == "RUMOUR"


# ── corroboration (§14) ─────────────────────────────────────────────────────

def test_two_aggregators_are_repetition_not_corroboration():
    """Two outlets rewriting one wire story is one source, and saying otherwise
    is how a single rumour acquires the authority of consensus."""
    note = q.corroboration_note([{"source_tier": "aggregator"},
                                 {"source_tier": "aggregator"}])
    assert "repetition" in note and "not independent" in note


def test_different_tiers_do_corroborate():
    note = q.corroboration_note([{"source_tier": "primary_filing"},
                                 {"source_tier": "quality_press"}])
    assert "independently corroborated" in note


def test_a_lone_item_says_so():
    note = q.corroboration_note([{"source_tier": "anonymous"}])
    assert "no independent confirmation" in note


def test_no_items_gives_no_note():
    assert q.corroboration_note([]) is None


# ── the shape stored on an observation ──────────────────────────────────────

def test_assess_returns_the_storage_shape():
    a = q.assess("Apple files Form 8-K", "edgar", "https://www.sec.gov/x", {"form": "8-K"})
    for key in ("claim_type", "claim_confidence", "claim_signal", "claim_caveat",
                "source_tier", "source_rank", "tier_basis"):
        assert key in a
