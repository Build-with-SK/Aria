"""
src/intelligence/research/news.py
=================================
News as structured evidence, not a sentiment number.

Every visible item (known at T: max(published, seen) <= T, within the
lookback) becomes a record with:

    headline, publisher, published_at, known_at, instrument, relevance
    topic / event_type     KEYWORD_RULES — deterministic patterns, labelled so
    direction              LEXICON — positive/negative word hits on the
                           headline; MIXED or NONE are real outcomes
    claim_type             src/research/quality.classify_claim (FACT, CLAIM,
                           RUMOUR, INFERENCE, ...)
    source_quality         src/research/quality.tier_for (evidence ladder)
    duplicate_cluster      the story it belongs to
    novelty                ORIGINAL (first in its story), SYNDICATED (same
                           story, another publisher), REPEATED (same story,
                           same publisher)

No language model is used here. Every classification says which rule made it,
and the raw headline always travels with it.

CLUSTERING
----------
Two items are the same story when their normalised headline token sets
overlap (Jaccard >= 0.5 with at least 3 shared words) or they share a URL,
and they became known within 72 hours of each other; clusters are the
connected components. A headline carried by fifteen outlets is ONE cluster,
and only clusters become evidence. A cluster is FRESH if its first item was
known within 48 hours of T, otherwise STALE; a fresh cluster on a topic an
older cluster already covered is a NEW_DEVELOPMENT, otherwise an
ORIGINAL_TOPIC.

Items whose headline names neither the ticker nor the company (association
WEAK — a keyword search matching an English word) are kept in the report and
excluded from evidence.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

from src.intelligence.research.pit_data import NewsItem

FRESH_HOURS = 48
SAME_STORY_HOURS = 72
JACCARD = 0.5
MIN_SHARED = 3

EVENT_TYPES: tuple[tuple[str, str], ...] = (
    ("FILING", r"\b(8-k|10-q|10-k|13f|13d|form 4|s-1|6-k|20-f)\b"),
    ("EARNINGS", r"\b(earnings|eps|quarterly results|q[1-4] results|results beat|"
                 r"results miss|revenue (beat|miss)|profit (rose|fell|jumps|slumps))\b"),
    ("GUIDANCE", r"\b(guidance|outlook|raises (its )?(forecast|outlook)|"
                 r"cuts (its )?(forecast|outlook)|reaffirms)\b"),
    ("M_AND_A", r"\b(acquires?|acquisition|merger|takeover|buyout|to buy)\b"),
    ("REGULATORY", r"\b(sec|ftc|doj|antitrust|regulator|fda|approval|probe|investigation)\b"),
    ("LEGAL", r"\b(lawsuit|sues|sued|court|settlement|verdict)\b"),
    ("ANALYST", r"\b(upgrade[sd]?|downgrade[sd]?|price target|initiates? coverage|"
                r"rating|overweight|underweight)\b"),
    ("MANAGEMENT", r"\b(ceo|cfo|chairman|resigns|appoints?|steps down)\b"),
    ("CAPITAL", r"\b(buyback|repurchase|dividend|share offering|bond sale|debt offering)\b"),
    ("PRODUCT", r"\b(launch(es)?|unveils?|new product|rollout)\b"),
    ("MACRO", r"\b(fed|inflation|interest rates?|tariffs?|cpi|jobs report|recession)\b"),
    ("SOCIAL_SENTIMENT", r"\b(bullish|bearish)\b.*\b(bullish|bearish)\b"),
)


def _lexicon():
    from src.data.enrichment import _NEG, _POS
    return _POS, _NEG


def classify_type(headline: str) -> tuple[str, str]:
    h = headline.lower()
    for name, pat in EVENT_TYPES:
        if re.search(pat, h):
            return name, f"KEYWORD_RULES: {pat}"
    return "OTHER", "KEYWORD_RULES: no pattern matched"


def classify_direction(headline: str) -> dict:
    pos, neg = _lexicon()
    words = set(re.findall(r"[a-z]+", headline.lower()))
    p, n = sorted(words & pos), sorted(words & neg)
    d = ("POSITIVE" if p and not n else "NEGATIVE" if n and not p else
         "MIXED" if p and n else "NONE")
    return {"direction": d, "positive_hits": p, "negative_hits": n,
            "method": "LEXICON (keyword match on the headline; not semantic, "
                      "not model-derived)"}


def _association(item: NewsItem) -> dict:
    from src.research.live_news import _association
    ticker = (item.instrument_hint or "").upper()
    row = {"watch_id": f"ticker:{ticker.lower()}" if ticker else "", "title": item.headline}
    return _association(row)


def _tokens(headline: str) -> set:
    from src.research.live_news import _norm
    return set(_norm(headline).split())


def cluster(items: list[NewsItem]) -> list[list[int]]:
    """Connected components of 'same story' (indices into items, by known_at)."""
    n = len(items)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    toks = [_tokens(it.headline) for it in items]
    known = [it.known_at() for it in items]
    window = timedelta(hours=SAME_STORY_HOURS)
    for i in range(n):
        for j in range(i + 1, n):
            if abs(known[i] - known[j]) > window:
                continue
            same_url = bool(items[i].url) and items[i].url == items[j].url
            inter = len(toks[i] & toks[j])
            union = len(toks[i] | toks[j]) or 1
            if same_url or (inter >= MIN_SHARED and inter / union >= JACCARD):
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: min(known[i] for i in g))


def analyse(visible: list[NewsItem], at: datetime, *, provider_status: dict) -> dict:
    """Structured items, story clusters, and the clusters fit to be evidence."""
    from src.research.quality import classify_claim, tier_for
    items = sorted(visible, key=lambda n: (n.known_at(), n.item_id))
    recs = []
    for it in items:
        etype, ebasis = classify_type(it.headline)
        assoc = _association(it)
        tier = tier_for(it.backend, it.url)
        recs.append({
            "item_id": it.item_id, "headline": it.headline, "publisher": it.publisher,
            "url": it.url, "backend": it.backend,
            "published_at": it.published_at.isoformat() if it.published_at else None,
            "known_at": it.known_at().isoformat(),
            "instrument": assoc.get("ticker") or it.instrument_hint,
            "association": assoc.get("state"),
            "relevance": {"STRONG": "HIGH", "NAMED": "MEDIUM", "WEAK": "LOW"}.get(
                assoc.get("state"), "MEDIUM" if it.instrument_hint else "LOW"),
            "event_type": etype, "event_type_basis": ebasis,
            "direction": classify_direction(it.headline),
            "claim_type": classify_claim(it.headline, it.backend, it.url).get("claim_type"),
            "source_quality": {"tier": tier["tier"], "rank": tier["rank"]},
        })
    clusters_out = []
    for cid, idxs in enumerate(cluster(items)):
        members = [recs[i] for i in idxs]
        first = members[0]
        pubs = Counter(m["publisher"] for m in members)
        seen_pub: set = set()
        for k, m in enumerate(members):
            m["duplicate_cluster"] = cid
            m["novelty"] = ("ORIGINAL" if k == 0 else
                            "REPEATED" if m["publisher"] in seen_pub else "SYNDICATED")
            seen_pub.add(m["publisher"])
        first_known = datetime.fromisoformat(first["known_at"])
        relevant = any(m["relevance"] in ("HIGH", "MEDIUM") for m in members)
        types = Counter(m["event_type"] for m in members)
        clusters_out.append({
            "cluster_id": cid, "headline": first["headline"],
            "first_known_at": first["known_at"], "last_known_at": members[-1]["known_at"],
            "items": len(members), "publishers": len(pubs),
            "event_type": types.most_common(1)[0][0],
            "direction": first["direction"]["direction"],
            "direction_method": first["direction"]["method"],
            "best_source": max(members, key=lambda m: m["source_quality"]["rank"])["source_quality"],
            "relevance": "HIGH" if any(m["relevance"] == "HIGH" for m in members) else
                         "MEDIUM" if relevant else "LOW",
            "freshness": ("FRESH" if at - first_known <= timedelta(hours=FRESH_HOURS)
                          else "STALE"),
            "age_hours": round((at - first_known).total_seconds() / 3600, 1),
        })
    # novelty of a STORY relative to earlier coverage of the same topic
    for c in clusters_out:
        prior = [p for p in clusters_out if p["first_known_at"] < c["first_known_at"]
                 and p["event_type"] == c["event_type"] and c["event_type"] != "OTHER"]
        c["story_novelty"] = "NEW_DEVELOPMENT" if prior else "ORIGINAL_TOPIC"
    evidence = [c for c in clusters_out if c["relevance"] in ("HIGH", "MEDIUM")]
    fresh = [c for c in evidence if c["freshness"] == "FRESH"]
    # A provider that answered and found nothing is NO_DATA (nothing was
    # published); one that failed is MISSING (we do not know).
    statuses = list((provider_status or {}).values())
    provider_ok = not statuses or any(str(v).startswith("OK") for v in statuses)
    status = "MEASURED" if items else ("NO_DATA" if provider_ok else "MISSING")
    return {
        "status": status,
        "provider_status": provider_status or {},
        "items": len(recs), "clusters": len(clusters_out),
        "independent_stories": len(evidence),
        "syndicated_copies_collapsed": len(recs) - len(clusters_out),
        "excluded_weak_association": sum(1 for c in clusters_out if c["relevance"] == "LOW"),
        "fresh_stories": len(fresh),
        "newest_known_at": recs[-1]["known_at"] if recs else None,
        "story_clusters": clusters_out, "item_records": recs,
        "method": {"clustering": f"Jaccard >= {JACCARD} on normalised headline words "
                                 f"(>= {MIN_SHARED} shared) or same URL, within "
                                 f"{SAME_STORY_HOURS} h",
                   "direction": "LEXICON", "event_type": "KEYWORD_RULES",
                   "model_derived": False},
    }
