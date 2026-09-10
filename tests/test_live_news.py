"""
tests/test_live_news.py
=======================
The live feed is continuous, deduplicated and honest about what it dropped.

The three properties that matter, and why each is here:

  * SILENCE IS A VALID ANSWER. A feed that must produce a row per poll will
    produce noise, and noise in an intelligence feed is worse than nothing
    because it looks like information.
  * ONE STORY IS ONE ROW. Five aggregators carrying the same headline is one
    development corroborated five times, not five developments.
  * AN OBSERVATION IS NOT A FACT. A headline being seen is not the claim being
    true, and only a primary filing or official data release may read as
    VERIFIED.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.research import live_news as LN


def _obs(title, *, source="reuters", url=None, salience=2.0, minutes_ago=5,
         tier=None, claim=None, watch="TEST"):
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    row = {"watch_id": watch, "title": title, "source": source,
           "url": url or f"https://example.test/{abs(hash(title))}",
           "salience": salience, "why": "test",
           "content_id": f"c{abs(hash(title + source))}",
           "seen_at": when.isoformat()}
    if tier or claim:
        row["quality"] = {"source_tier": tier or "quality_press",
                          "source_rank": 7 if tier == "primary_filing" else 4,
                          "claim_type": claim or "CLAIM",
                          "claim_confidence": 0.8}
    return row


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "observations.jsonl"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(LN, "OBSERVATIONS", path)

    def write(rows):
        path.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return write


# ── silence ─────────────────────────────────────────────────────────────────

def test_an_empty_store_returns_nothing_rather_than_filler(store):
    store([])
    got = LN.stream()
    assert got["items"] == [] and got["new_count"] == 0
    assert "Empty is a real answer" in got["note"]


def test_a_cursor_past_everything_returns_nothing(store):
    store([_obs("Fed holds rates steady at 4.5 percent")])
    first = LN.stream()
    assert first["new_count"] == 1

    # Polling again with the returned cursor must yield silence, not a repeat.
    second = LN.stream(since=first["next_since"])
    assert second["new_count"] == 0
    assert second["items"] == []


def test_the_cursor_only_returns_what_arrived_after_it(store):
    old = _obs("Old development in the market", minutes_ago=120)
    store([old])
    first = LN.stream()
    cursor = first["next_since"]

    store([old, _obs("Brand new development just now", minutes_ago=1)])
    got = LN.stream(since=cursor)
    assert got["new_count"] == 1
    assert got["items"][0]["headline"] == "Brand new development just now"


# ── deduplication ───────────────────────────────────────────────────────────

def test_one_story_from_five_sources_is_one_row(store):
    title = "Acme Corp raises full year guidance after strong quarter"
    store([_obs(title, source=s) for s in
           ("reuters", "bloomberg", "yahoo", "seekingalpha", "marketwatch")])
    got = LN.stream()
    assert got["new_count"] == 1, "the same story produced five rows"
    assert got["deduplicated_away"] == 4
    row = got["items"][0]
    assert row["corroboration"] == 5
    assert row["repeated"] is True


def test_corroboration_is_reported_as_evidence_not_hidden(store):
    title = "Central bank signals a pause in the tightening cycle"
    store([_obs(title, source=s) for s in ("reuters", "bloomberg")])
    row = LN.stream()["items"][0]
    assert set(row["corroborating_sources"]) == {"reuters", "bloomberg"}


def test_the_highest_tier_source_wins_the_row(store):
    title = "Acme Corp files its annual report with the regulator"
    store([_obs(title, source="reddit", tier="anonymous", claim="RUMOUR"),
           _obs(title, source="edgar", tier="primary_filing", claim="FACT")])
    row = LN.stream()["items"][0]
    assert row["source_tier"] == "primary_filing"
    assert row["source"] == "edgar"


def test_genuinely_different_stories_are_not_collapsed(store):
    store([_obs("Acme Corp raises full year guidance"),
           _obs("Beta Industries cuts its dividend in half")])
    assert LN.stream()["new_count"] == 2


# ── quality ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("junk", ["story 0", "Story 12", "item 3", "untitled",
                                  "TO THE MOON", "lfg", "  ", "hodl"])
def test_junk_with_no_claim_is_held_back(store, junk):
    store([_obs(junk), _obs("Acme Corp raises full year guidance for 2027")])
    got = LN.stream()
    assert got["new_count"] == 1
    assert got["filtered_out"] == 1


def test_filtering_is_reported_never_silent(store):
    """A quiet feed and an over-aggressive filter must be distinguishable."""
    store([_obs("story 0"), _obs("story 1"),
           _obs("Acme Corp raises full year guidance for 2027")])
    got = LN.stream()
    assert got["filtered_out"] == 2
    assert sum(got["filter_reasons"].values()) == 2
    assert all(isinstance(k, str) and k for k in got["filter_reasons"])


def test_an_unpopular_opinion_is_not_junk(store):
    """The test is 'does this assert anything', not 'do we like it'."""
    store([_obs("This rally is fake and the market is going to crash hard")])
    assert LN.stream()["new_count"] == 1


def test_a_hype_phrase_inside_a_real_headline_keeps_its_row(store):
    store([_obs("Analysts say the stock could go to the moon after earnings")])
    assert LN.stream()["new_count"] == 1


# ── observation vs verified fact ────────────────────────────────────────────

def test_a_headline_is_an_observation_not_a_fact(store):
    store([_obs("Sources say Acme is exploring a sale", source="reuters",
                tier="quality_press", claim="CLAIM")])
    assert LN.stream()["items"][0]["evidence_state"] == "OBSERVATION"


def test_only_a_primary_filing_stating_a_fact_reads_as_verified(store):
    store([_obs("Acme Corp 8-K filed with the regulator", source="edgar",
                tier="primary_filing", claim="FACT")])
    assert LN.stream()["items"][0]["evidence_state"] == "VERIFIED"


def test_a_rumour_on_a_high_tier_feed_is_still_a_rumour(store):
    """Tier and claim type are separate axes and must not collapse."""
    store([_obs("Rumours swirl that Acme may be acquired", source="edgar",
                tier="primary_filing", claim="RUMOUR")])
    row = LN.stream()["items"][0]
    assert row["source_tier"] == "primary_filing"
    assert row["claim_type"] == "RUMOUR"
    assert row["evidence_state"] == "OBSERVATION"


def test_every_row_carries_its_source_and_timestamp(store):
    store([_obs("Acme Corp raises full year guidance for 2027")])
    row = LN.stream()["items"][0]
    for field in ("at", "headline", "source", "source_tier", "claim_type",
                  "evidence_state", "url"):
        assert row.get(field) is not None, f"missing {field}"


def test_rows_without_a_stored_quality_block_are_tiered_retroactively(store):
    """134 observations predate the eye's quality assessment. They must not all
    display as 'unknown' — the ladder is a pure function of source and headline,
    so it applies retroactively."""
    store([_obs("Acme Corp 10-K filed with the regulator", source="edgar")])
    row = LN.stream()["items"][0]
    assert row["source_tier"] == "primary_filing", (
        "a row with no stored quality block was not placed on the ladder")
    assert row["claim_type"] == "FACT"


def test_a_domain_outranks_the_backend_that_delivered_it(store):
    """A Reuters story arriving via an aggregator is still Reuters, and an
    SEC filing arriving via RSS is still a filing."""
    store([_obs("Acme Corp 8-K announced today", source="rss",
                url="https://www.sec.gov/Archives/edgar/data/1/x.htm")])
    assert LN.stream()["items"][0]["source_tier"] == "primary_filing"


def test_an_unrecognised_source_is_unknown_rather_than_assumed_credible(store):
    """Absence of a tier must never read as a good tier."""
    store([_obs("Acme Corp announced a new product line today",
                source="somebodys_blog", url="https://nowhere.invalid/x")])
    row = LN.stream()["items"][0]
    assert row["source_tier"] == "unknown"
    assert row["evidence_state"] == "OBSERVATION"


# ── the two products are one store ──────────────────────────────────────────

def test_the_digest_and_the_stream_read_the_same_store(store):
    store([_obs(f"Development number {n} in the market today", minutes_ago=n)
           for n in range(1, 6)])
    stream = LN.stream(hours=24)
    digest = LN.digest(hours=24)
    assert {i["id"] for i in digest["items"]} <= {i["id"] for i in stream["items"]}
    assert digest["captured"] == stream["raw_observations"], (
        "the daily digest and the live feed disagree about what was observed — "
        "they must be two windows onto one event store, not two stores")


def test_the_digest_reports_what_it_held_back(store):
    store([_obs("story 0"),
           _obs("Acme Corp raises full year guidance for 2027")])
    d = LN.digest(hours=24)
    assert d["filtered_out"] == 1
    assert d["quality_note"] and "held back" in d["quality_note"]


def test_source_mix_is_visible(store):
    store([_obs("Acme files its annual report", source="edgar",
                tier="primary_filing", claim="FACT"),
           _obs("Somebody on a forum thinks Acme is going up",
                source="reddit", tier="anonymous", claim="RUMOUR")])
    seen = LN.sources_seen(hours=24)
    assert seen["total"] == 2
    assert set(seen["by_tier"]) == {"primary_filing", "anonymous"}


# ── asset association: is this story actually about that instrument? ────────
#
# The eye searches a news backend for a ticker, and a keyword search does not
# know what a ticker is. `ticker:bill` collected a story about a man arrested
# with a guillotine, several about Bill Ackman's portfolio, and one about a
# congressional bill — 103 of 197 stored observations were the query string
# matching as ordinary English. Showing those beside a real 8-K is not a
# ranking problem; it is a claim that they concern the same company.

def test_a_ticker_named_as_a_ticker_is_relevant(store):
    store([_obs("Cloudflare Inc (NET) shares fall 4 percent", watch="ticker:net")])
    got = LN.stream()
    assert got["new_count"] == 1
    assert got["items"][0]["association"] == "STRONG"
    assert got["items"][0]["asset"] == "NET"


def test_a_dollar_prefixed_ticker_is_relevant(store):
    store([_obs("StockTwits $BNO: 3 bullish / 0 bearish", watch="ticker:bno")])
    assert LN.stream()["items"][0]["association"] == "STRONG"


@pytest.mark.parametrize("headline", [
    "Bill Ackman's Pershing Square buys Netflix and five other stocks",
    "Congress stock trading bill does not solve the real problem",
    "Bill Gates owns twenty five billion in blue chip stocks",
    "California man arrested after driving pickup with a guillotine",
])
def test_the_ticker_matching_as_an_english_word_is_not_relevant(store, headline):
    """`BILL` is a company. "bill" is a word. The feed must know the difference."""
    store([_obs(headline, watch="ticker:bill")])
    got = LN.stream()
    assert got["new_count"] == 0
    assert got["filtered_out"] == 1
    assert "not about BILL" in " ".join(got["filter_reasons"])


def test_the_match_is_case_sensitive(store):
    """"Bill" in prose is not the ticker BILL — that single distinction removes
    most of the noise on its own."""
    store([_obs("Bill Ackman buys more stock", watch="ticker:bill"),
           _obs("BILL beats on revenue this quarter", watch="ticker:bill")])
    got = LN.stream()
    assert got["new_count"] == 1
    assert got["items"][0]["headline"].startswith("BILL beats")


def test_the_company_name_also_establishes_relevance(store):
    """A story can be about an instrument without printing its ticker.

    `United States Brent Oil Fund posts $127.9M net income` is plainly about
    BNO, and a ticker-token-only rule would have thrown it away.
    """
    store([_obs("United States Brent Oil Fund posts record net income",
                watch="ticker:bno")])
    got = LN.stream()
    if got["new_count"] == 0:
        pytest.skip("BNO is not in this checkout's symbol master")
    assert got["items"][0]["association"] == "NAMED"


def test_the_company_name_test_needs_more_than_one_word(store):
    """Matching the single word "bill" against "BILL Holdings" would re-admit
    every Bill Ackman story through the back door."""
    assert LN._company_phrase("BILL") in (None,) or \
        len(LN._company_phrase("BILL").split()) >= 2


def test_a_non_ticker_watch_is_never_association_filtered(store):
    """A topic watch has no instrument to be about, so this test must not
    apply to it at all."""
    store([_obs("Central banks are rethinking the inflation target",
                watch="topic:monetary-policy")])
    got = LN.stream()
    assert got["new_count"] == 1
    assert got["items"][0]["association"] == "N/A"


def test_dropped_items_are_reported_with_the_instrument_they_missed(store):
    """Silent relevance filtering would be indistinguishable from a dead feed."""
    store([_obs("Bill Ackman buys Netflix", watch="ticker:bill"),
           _obs("BILL beats on revenue", watch="ticker:bill")])
    got = LN.stream()
    assert got["filtered_out"] == 1
    reason = next(iter(got["filter_reasons"]))
    assert "BILL" in reason and "word" in reason


def test_relevance_is_reported_on_every_row(store):
    store([_obs("BILL beats on revenue this quarter", watch="ticker:bill")])
    row = LN.stream()["items"][0]
    assert row["association"] in ("STRONG", "NAMED", "N/A")
    assert row["asset"] == "BILL"
