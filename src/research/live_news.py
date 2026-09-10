"""
src/research/live_news.py
=========================
The live world feed — continuous, deduplicated, quality-filtered.

LIVE NEWS IS NOT THE DAILY REPORT
---------------------------------
They are two products over ONE observation store, and conflating them was the
complaint:

    DAILY REPORT   slow, curated, once a day, historical, analytical
    LIVE NEWS      continuous, event-driven, only what is genuinely NEW

So there is no second event store here. `src/research/eye.py` already writes
`data/research/observations.jsonl` with provenance and a quality assessment
attached; this module is a READER of that file with a `since` cursor, so the UI
can poll every few seconds and receive nothing at all when nothing happened.

"EVERY SECOND" MUST NOT MEAN "INVENT SOMETHING EVERY SECOND"
------------------------------------------------------------
A feed that must produce a row per tick will produce noise. This one returns an
empty list and an honest `next_since` cursor when the eye has seen nothing, and
the UI is expected to render that as silence rather than as a gap to fill.

QUALITY
-------
The store currently contains items like "story 0" and low-effort social
chatter. Those are real observations — the eye genuinely saw them — but putting
them next to a regulatory filing implies an equivalence that does not exist. So:

  * every item carries its SOURCE TIER from the evidence ladder and its CLAIM
    TYPE, and the two are separate axes (a rumour on a high-tier feed is still
    a rumour);
  * junk is filtered by structural tests — no title, no substance, placeholder
    text, pure hype with no claim — never by a blocklist of opinions;
  * filtering is REPORTED, not silent: the response says how many were held
    back and why, so a suspiciously quiet feed is distinguishable from a
    suspiciously aggressive filter.

DEDUPLICATION
-------------
One story reported by five aggregators is one development. Items are collapsed
on a normalised-title fingerprint, the highest-tier source wins the row, and the
rest are attached as corroboration — which is evidence, not clutter: five
independent outlets carrying a claim is worth more than one, and the row says so.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
OBSERVATIONS = ROOT / "data" / "research" / "observations.jsonl"

#: Below this the item is noise for a feed a human reads. Tunable, reported in
#: the response, and never applied silently.
MIN_SALIENCE = 0.0

#: Titles that carry no claim. Structural, not a topic blocklist — the test is
#: "does this sentence assert anything about the world?", which is why "story 0"
#: fails it and an unpopular opinion does not.
PLACEHOLDER = re.compile(
    r"^\s*(story|item|post|article|untitled|test|example|placeholder)\s*[\d#:_-]*\s*$",
    re.I)

#: Pure hype with no proposition. These are removed only when the ENTIRE title
#: is one of them; a headline that happens to contain "to the moon" alongside a
#: real claim keeps its row.
HYPE_ONLY = re.compile(
    r"^\s*[\W_]*(to the moon|moon|lfg|wagmi|hodl|buy the dip|yolo|"
    r"stonks|diamond hands|apes together strong|this is the way)"
    r"[\W_\s]*$", re.I)

MIN_TITLE_WORDS = 3

STOP = {"the", "a", "an", "of", "in", "on", "for", "to", "and", "is", "as",
        "at", "by", "with", "after", "from", "its", "it", "s", "new", "says"}


def _norm(title: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", (title or "").lower())
             if w not in STOP]
    return " ".join(sorted(set(words)))


#: Words that make a company name unusable as a match on their own. "Holdings"
#: matches nothing useful; "Bill Holdings" does.
_GENERIC_NAME_WORDS = {
    "inc", "inc.", "corp", "corporation", "co", "company", "plc", "ltd",
    "limited", "holdings", "holding", "group", "the", "fund", "trust", "etf",
    "shares", "class", "common", "stock", "n.v.", "sa", "ag", "&",
}

_NAME_CACHE: dict[str, Optional[str]] = {}


def _company_phrase(ticker: str) -> Optional[str]:
    """The company's distinctive multi-word phrase, lowercased.

    Multi-word ON PURPOSE. `BILL` is BILL Holdings, and matching the single
    word "bill" would pull in every story about Bill Ackman, Bill Gates and
    congressional bills — which is exactly what the feed was doing.
    """
    key = ticker.upper()
    if key in _NAME_CACHE:
        return _NAME_CACHE[key]
    name = None
    try:
        from src.core.identity import provider_identity
        name = provider_identity(key).name
    except Exception:
        name = None
    phrase = None
    if name:
        words = [w for w in re.findall(r"[A-Za-z0-9&.]+", name.lower())
                 if w not in _GENERIC_NAME_WORDS]
        if len(words) >= 2:
            phrase = " ".join(words)
    _NAME_CACHE[key] = phrase
    return phrase


def _association(row: dict) -> dict:
    """Does this headline actually concern the instrument it was filed under?

    The eye searches a news backend for a ticker, and a keyword search does not
    know what a ticker is. `ticker:bill` collected a story about a man arrested
    with a guillotine, one about Bill Gates's portfolio, and one about a
    congressional bill — all because the query string is also an English word.
    Presenting those beside a real filing is not a ranking problem, it is a
    claim that they are about the same company.

    Three verdicts, and the weak ones are REPORTED rather than silently binned:

      STRONG  the ticker appears as a ticker — `BNO`, `$BILL`, `(NET)`
      NAMED   the company's distinctive multi-word name appears
      WEAK    neither; the match was on the query string as ordinary language
    """
    watch = str(row.get("watch_id") or "")
    if not watch.lower().startswith("ticker:"):
        return {"state": "N/A", "ticker": None,
                "why": "not a ticker watch, so there is nothing to associate"}

    ticker = watch.split(":", 1)[1].strip().upper()
    title = row.get("title") or ""
    if not ticker:
        return {"state": "N/A", "ticker": None, "why": "watch names no ticker"}

    # Case-sensitive: `Bill` in prose is not the ticker `BILL`.
    if re.search(rf"(?<![A-Za-z0-9])\$?{re.escape(ticker)}(?![A-Za-z0-9])", title):
        return {"state": "STRONG", "ticker": ticker,
                "why": f"the headline names the ticker {ticker}"}

    phrase = _company_phrase(ticker)
    if phrase and phrase in title.lower():
        return {"state": "NAMED", "ticker": ticker,
                "why": f"the headline names the company ({phrase})"}

    return {"state": "WEAK", "ticker": ticker,
            "why": (f"filed under {ticker} but the headline names neither the "
                    f"ticker nor the company — the search matched '{ticker}' as "
                    f"ordinary language")}


def _quality_verdict(row: dict) -> tuple[bool, Optional[str]]:
    """(keep, reason_for_dropping). Structural tests only."""
    title = (row.get("title") or "").strip()
    if not title:
        return False, "no title"
    if PLACEHOLDER.match(title):
        return False, "placeholder title with no claim"
    if HYPE_ONLY.match(title):
        return False, "pure hype, asserts nothing"
    if len(re.findall(r"\w+", title)) < MIN_TITLE_WORDS:
        return False, f"fewer than {MIN_TITLE_WORDS} words"
    if (row.get("salience") or 0) < MIN_SALIENCE:
        return False, f"salience below {MIN_SALIENCE}"
    assoc = _association(row)
    if assoc["state"] == "WEAK":
        return False, f"not about {assoc['ticker']} — matched the ticker as a word"
    return True, None


def _read(hours: float, since: Optional[str]) -> list[dict]:
    if not OBSERVATIONS.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
        except Exception:
            since_dt = None

    out = []
    for line in OBSERVATIONS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            when = datetime.fromisoformat(row["seen_at"])
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except (ValueError, KeyError, TypeError):
            continue
        if when < cutoff:
            continue
        if since_dt and when <= since_dt:
            continue
        row["_when"] = when
        out.append(row)
    return out


def _quality_of(row: dict) -> dict:
    """The row's quality block, assessed on the fly when it predates the eye's
    own assessment.

    134 of the observations in the store were written before `_assess_quality`
    existed, so they carry no tier at all and would display as 'unknown' —
    which reads as "we could not place this source" when the truth is "nobody
    asked". The ladder is a pure function of source and headline, so it can be
    applied retroactively without re-fetching anything.
    """
    q = row.get("quality")
    if q:
        return q
    try:
        from src.research.quality import assess
        return assess(row.get("title") or "", row.get("source"),
                      row.get("url"), row.get("meta") or {})
    except Exception:
        return {}


def _shape(row: dict, corroboration: list[dict]) -> dict:
    q = _quality_of(row)
    tier = q.get("source_tier") or "unknown"
    rank = q.get("source_rank")
    claim = q.get("claim_type") or "UNCLASSIFIED"
    sources = sorted({(r.get("source") or "unknown") for r in corroboration}
                     | {row.get("source") or "unknown"})
    return {
        "id": row.get("content_id") or row.get("url"),
        "at": row.get("seen_at"),
        "headline": row.get("title"),
        "url": row.get("url"),
        "source": row.get("source"),
        "source_tier": tier,
        "source_rank": rank,
        # The two axes, kept apart on purpose. A high-tier source can carry a
        # rumour and a low-tier one can carry a filing.
        "claim_type": claim,
        "claim_confidence": q.get("claim_confidence"),
        # OBSERVATION vs VERIFIED FACT — a headline is not evidence. Only a
        # primary filing or an official data release is allowed to read as a
        # fact, and even then only when the classifier called it one.
        "evidence_state": (
            "VERIFIED" if tier in ("primary_filing", "official_data")
            and claim == "FACT" else "OBSERVATION"),
        "subject": row.get("watch_id"),
        "asset": _association(row).get("ticker"),
        "association": _association(row)["state"],
        "salience": row.get("salience"),
        "why": row.get("why"),
        "corroboration": len(sources),
        "corroborating_sources": sources,
        "repeated": len(corroboration) > 0,
    }


def stream(since: Optional[str] = None, *, hours: float = 6.0,
           limit: int = 40) -> dict:
    """New observations since `since`, ranked. The polling endpoint.

    Returns `next_since` so the caller can advance its cursor without keeping
    state, and `new_count == 0` is the ordinary, correct answer most of the time.
    """
    rows = _read(hours, since)

    kept, dropped = [], []
    for r in rows:
        ok, why = _quality_verdict(r)
        (kept if ok else dropped).append((r, why))

    groups: dict[str, list[dict]] = {}
    for r, _ in kept:
        groups.setdefault(_norm(r.get("title", "")) or (r.get("url") or ""),
                          []).append(r)

    items = []
    for fingerprint, grp in groups.items():
        grp.sort(key=lambda r: (_quality_of(r).get("source_rank") or 0,
                                r.get("salience") or 0), reverse=True)
        items.append(_shape(grp[0], grp[1:]))

    items.sort(key=lambda i: ((i["salience"] or 0)
                              + (i["source_rank"] or 0) * 0.5
                              + (i["corroboration"] - 1) * 0.5),
               reverse=True)

    newest = max((r["_when"] for r in rows), default=None)
    drop_reasons: dict[str, int] = {}
    for _, why in dropped:
        drop_reasons[why] = drop_reasons.get(why, 0) + 1

    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "since": since,
        "next_since": newest.isoformat() if newest else since,
        "window_hours": hours,
        "new_count": len(items),
        "items": items[:limit],
        "raw_observations": len(rows),
        "deduplicated_away": max(0, len(kept) - len(items)),
        "filtered_out": len(dropped),
        "filter_reasons": drop_reasons,
        "note": (
            "Empty is a real answer. This feed shows only observations the "
            "research eye actually recorded since the cursor — it does not "
            "manufacture a row per poll."
            if not items else
            "OBSERVATION means the eye saw a claim, not that the claim is true. "
            "Only a primary filing or official data release reads as VERIFIED."),
    }


def digest(hours: int = 24, limit: int = 12) -> dict:
    """The slow view over the same store — what the daily report embeds.

    Same source, same dedup, same quality tests; a day-wide window and no
    cursor. That is the entire difference between the two products, and it is
    deliberate that it is only a difference of window.
    """
    out = stream(since=None, hours=hours, limit=limit)
    quality_note = None
    if out["filtered_out"]:
        reasons = ", ".join(f"{n}× {why}"
                            for why, n in sorted(out["filter_reasons"].items(),
                                                 key=lambda kv: -kv[1]))
        quality_note = (
            f"{out['filtered_out']} observation(s) were held back as unusable "
            f"({reasons}). They remain in the store; they are not evidence.")
    return {
        "window_hours": hours,
        "items": out["items"],
        "captured": out["raw_observations"],
        "deduplicated_away": out["deduplicated_away"],
        "filtered_out": out["filtered_out"],
        "quality_note": quality_note,
        "note": out["note"],
    }


def sources_seen(hours: int = 24) -> dict:
    """Which sources are actually feeding the system, by evidence tier.

    A feed dominated by aggregators and social posts is a different thing from
    one carrying filings, and the reader is entitled to know which they have.
    """
    rows = _read(hours, None)
    tiers: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for r in rows:
        tier = _quality_of(r).get("source_tier") or "unknown"
        tiers[tier] = tiers.get(tier, 0) + 1
        src = r.get("source") or "unknown"
        by_source[src] = by_source.get(src, 0) + 1
    return {"window_hours": hours, "by_tier": tiers, "by_source": by_source,
            "total": len(rows)}
