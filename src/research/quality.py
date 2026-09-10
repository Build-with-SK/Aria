"""
src/research/quality.py
=======================
Source quality and claim type — Phases 2, 11, 14 and 46.

§14 is the one that matters: ARIA must not treat an internet claim as true
because it exists. §46 asks for an explicit evidence ladder, and §2 asks that
FACT, CLAIM, RUMOUR, INFERENCE and HYPOTHESIS stay distinguishable rather than
collapsing into "information".

The eye already scores SALIENCE — how much attention something deserves — via
SOURCE_WEIGHT and corroboration in eye.py. That is a different axis from what
this module adds, and conflating them would be a mistake worth naming:

    salience   how much this deserves attention right now
    tier       how much weight the SOURCE carries as evidence
    claim type what KIND of statement is being made

A rumour on a high-salience feed is still a rumour. A dull regulatory filing is
low salience and the highest tier there is. Keeping the axes apart is the whole
point: an item can be worth looking at and worth disbelieving at the same time.

HONESTY ABOUT THE CLASSIFIER
----------------------------
This is deterministic pattern matching over a headline and a source. It is NOT
semantic understanding, and pretending otherwise would be the same sin as a
green worker light that cannot go red. So:

  * every classification returns a confidence and the SIGNAL that produced it;
  * "unclassified" is a real, common outcome, not a failure;
  * a headline with no marker is UNCLASSIFIED, never defaulted to FACT.

Downstream, an unclassified item is treated as a CLAIM at best. The cost of
being wrong in that direction is scepticism about something true; the cost in
the other direction is trading on a rumour.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlsplit

# ── the evidence ladder (§46) ───────────────────────────────────────────────
# Rank is ordinal: higher means it carries more weight as evidence. These are
# not probabilities and must not be multiplied into scores as if they were.
TIERS: dict[str, tuple[int, str]] = {
    "primary_filing":  (7, "a document filed with a regulator by the party itself"),
    "official_data":   (6, "a statistical agency or central bank publishing its own series"),
    "company_release": (5, "the company speaking for itself"),
    "academic":        (5, "peer-reviewed or preprint research"),
    "quality_press":   (4, "financial journalism with named editorial accountability"),
    "aggregator":      (3, "a feed that republishes others' reporting"),
    "secondary":       (3, "analysis of somebody else's reporting"),
    "social":          (2, "an identifiable person or account, unverified"),
    "anonymous":       (1, "an unattributed post"),
    "unknown":         (0, "the source could not be placed on the ladder"),
}

#: Backend name → tier. Keyed off eye.SOURCE_WEIGHT's vocabulary deliberately,
#: so the two tables cannot drift apart on source names.
SOURCE_TIER: dict[str, str] = {
    "edgar": "primary_filing",
    "rss": "aggregator",
    "googlenews": "aggregator",
    "web": "secondary",
    "github": "secondary",
    "hackernews": "social",
    "reddit": "anonymous",
    "x": "social",
    "stocktwits": "anonymous",
    "youtube": "social",
}

#: Domains that outrank whatever backend delivered them. Google News is an
#: aggregator, but a Reuters story arriving through it is still Reuters.
DOMAIN_TIER: tuple[tuple[str, str], ...] = (
    ("sec.gov", "primary_filing"),
    ("federalreserve.gov", "official_data"),
    ("newyorkfed.org", "official_data"),
    ("treasury.gov", "official_data"),
    ("bls.gov", "official_data"),
    ("ecb.europa.eu", "official_data"),
    ("bankofengland.co.uk", "official_data"),
    ("imf.org", "official_data"),
    ("worldbank.org", "official_data"),
    ("nseindia.com", "official_data"),
    ("bseindia.com", "official_data"),
    ("londonstockexchange.com", "official_data"),
    ("arxiv.org", "academic"),
    ("ssrn.com", "academic"),
    ("nber.org", "academic"),
    ("reuters.com", "quality_press"),
    ("apnews.com", "quality_press"),
    ("bloomberg.com", "quality_press"),
    ("ft.com", "quality_press"),
    ("wsj.com", "quality_press"),
    ("economist.com", "quality_press"),
    ("bbc.co.uk", "quality_press"),
    ("cnbc.com", "quality_press"),
    ("prnewswire.com", "company_release"),
    ("businesswire.com", "company_release"),
    ("globenewswire.com", "company_release"),
)


def tier_for(source: Optional[str], url: Optional[str] = None) -> dict:
    """Where a source sits on the evidence ladder, and why.

    The domain wins over the backend when it is more specific: a Reuters story
    delivered by Google News is quality press, not an aggregator.
    """
    backend_tier = SOURCE_TIER.get((source or "").lower(), "unknown")
    chosen, why = backend_tier, f"backend '{source}'"

    host = ""
    if url:
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            host = ""
    if host:
        for domain, dt in DOMAIN_TIER:
            if host == domain or host.endswith("." + domain):
                if TIERS[dt][0] >= TIERS[backend_tier][0]:
                    chosen, why = dt, f"domain '{domain}'"
                break

    rank, description = TIERS[chosen]
    return {"tier": chosen, "rank": rank, "description": description,
            "basis": why, "host": host or None}


# ── claim type (§2, §14) ────────────────────────────────────────────────────

CLAIM_TYPES = ("FACT", "CLAIM", "RUMOUR", "INFERENCE", "HYPOTHESIS", "UNCLASSIFIED")

#: Ordered: the first pattern to match wins, so the most epistemically
#: cautious markers are checked before the confident ones. A headline that
#: says "reportedly could beat forecasts" is a rumour about an inference, and
#: rumour is the safer of the two readings.
_MARKERS: tuple[tuple[str, str, str], ...] = (
    ("RUMOUR", r"\b(rumou?r(ed|s)?|unconfirmed|allegedly|speculation|"
               r"sources? (say|said|told)|people familiar|insider(s)? claim|"
               r"leak(ed|s)?)\b", "hedged attribution or an unnamed source"),
    ("INFERENCE", r"\b(forecast(s|ed)?|expect(s|ed|ations)?|projec(t|ts|ted|tion)|"
                  r"estimate(s|d)?|outlook|price target|analysts? (say|see|expect)|"
                  r"could|might|may|likely|set to|poised to|on track to)\b",
     "a statement about the future or an analyst view"),
    ("HYPOTHESIS", r"\b(we (propose|hypothesi[sz]e)|this paper|study (finds|suggests)|"
                   r"research (shows|suggests)|preprint|abstract)\b",
     "a research proposition"),
    ("FACT", r"\b(reported (revenue|earnings|results)|filed|announces?d?|declares?d?|"
             r"completed|closed the acquisition|has (acquired|appointed|resigned)|"
             r"posts? (q[1-4]|quarterly|annual)|dividend declared)\b",
     "a completed, attributable event"),
)


def classify_claim(title: str, source: Optional[str] = None,
                   url: Optional[str] = None, meta: Optional[dict] = None) -> dict:
    """What KIND of statement this headline makes.

    Deterministic and shallow by construction — it reads a headline, not an
    article. The returned `confidence` says how much to trust the label, and
    `signal` names what produced it so a human can disagree with the evidence
    rather than with a verdict.
    """
    text = (title or "").strip()
    t = tier_for(source, url)
    meta = meta or {}

    # A regulatory filing is a fact about a filing having been made. That is
    # the strongest thing this module can assert, and it is asserted from the
    # SOURCE, not from language — which is why it is checked first.
    if t["tier"] == "primary_filing":
        return {"claim_type": "FACT", "confidence": 0.9,
                "signal": f"primary filing via {t['basis']}"
                          + (f" (form {meta['form']})" if meta.get("form") else ""),
                "caveat": ("A filing is evidence that a document was filed and "
                           "what it says — not that its forward-looking content "
                           "is true."),
                **t}

    if t["tier"] == "official_data":
        return {"claim_type": "FACT", "confidence": 0.85,
                "signal": f"statistical agency or central bank via {t['basis']}",
                "caveat": "Official statistics are revised; check the vintage.",
                **t}

    low = text.lower()
    for claim_type, pattern, why in _MARKERS:
        if re.search(pattern, low):
            # A marker in a headline is weak evidence about the article. Social
            # and anonymous sources get a further discount: the same words mean
            # less from an unattributed post.
            confidence = 0.6 if t["rank"] >= 4 else 0.45
            if t["tier"] in ("anonymous", "social"):
                confidence = 0.35
            return {"claim_type": claim_type, "confidence": round(confidence, 2),
                    "signal": f"{why} in the headline",
                    "caveat": ("Classified from the headline alone; the article "
                               "may be more or less hedged than its title."),
                    **t}

    # No marker. An anonymous post asserting something is a CLAIM by an
    # unverified party; anything else is genuinely unclassified.
    if t["tier"] in ("anonymous", "social"):
        return {"claim_type": "CLAIM", "confidence": 0.4,
                "signal": "an assertion from an unverified account",
                "caveat": "Treat as one person's opinion until corroborated.",
                **t}

    return {"claim_type": "UNCLASSIFIED", "confidence": 0.0,
            "signal": "no epistemic marker found in the headline",
            "caveat": ("Unclassified is not the same as true. Downstream this is "
                       "treated as a CLAIM at best — the cost of scepticism about "
                       "something true is far lower than the cost of trading on a "
                       "rumour."),
            **t}


def assess(title: str, source: Optional[str] = None, url: Optional[str] = None,
           meta: Optional[dict] = None) -> dict:
    """Tier plus claim type, in the shape stored on an observation."""
    c = classify_claim(title, source, url, meta)
    return {
        "claim_type": c["claim_type"],
        "claim_confidence": c["confidence"],
        "claim_signal": c["signal"],
        "claim_caveat": c["caveat"],
        "source_tier": c["tier"],
        "source_rank": c["rank"],
        "tier_basis": c["basis"],
    }


def corroboration_note(items: list[dict]) -> Optional[str]:
    """Does a set of observations about one subject agree, and at what tier?

    §14 asks whether there is independent confirmation. Two aggregators
    carrying one wire story is not two sources; the tier ladder is what makes
    that visible, so this reports the BEST tier and the number of DISTINCT
    tiers rather than a raw count.
    """
    if not items:
        return None
    tiers = {i.get("source_tier") for i in items if i.get("source_tier")}
    tiers.discard(None)
    if not tiers:
        return None
    best = max(tiers, key=lambda t: TIERS.get(t, (0, ""))[0])
    if len(items) == 1:
        return f"single source, tier '{best}' — no independent confirmation"
    if len(tiers) == 1:
        return (f"{len(items)} items but all at tier '{best}' — repetition, "
                f"not independent corroboration")
    return (f"{len(items)} items across {len(tiers)} tiers, best '{best}' — "
            f"independently corroborated")
