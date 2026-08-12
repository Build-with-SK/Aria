"""
src/research/eye.py
===================
ARIA's eye on the internet.

reach.py is a hand: it fetches what it is told to fetch. This is the eye —
it stays open, it looks on its own schedule, and it reports what CHANGED.
That difference is the whole design:

  Attention.   An eye that looks everywhere sees nothing. Attention comes
               from open positions first, then explicit watches. What ARIA
               owns is what she watches.

  Habituation. The same story arriving for the fourth time is not news. Every
               observation is remembered by content id, and a watch's first
               look reports nothing at all — it primes the baseline. An eye
               with no baseline cannot tell "new" from "first time I looked",
               and a system that confuses those two floods you on day one and
               is never trusted again.

  Salience.    What survives habituation is ranked by corroboration across
               independent sources, by the weight of the source (an 8-K is
               not a StockTwits post), and by engagement. Salience is a
               triage order for a human. It is not a signal and nothing
               downstream may treat it as one.

The eye does not trade, size, or propose. It observes, and hands what it saw
to the brain's REASON step and to the alerts feed. The human still approves
everything, exactly as before — nothing here calls approve_and_execute.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .leads import hunt
from .store import citation

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
STATE_FILE = ROOT / "data" / "research" / "eye_state.json"
OBSERVATIONS = ROOT / "data" / "research" / "observations.jsonl"

# How much a source's word is worth when ranking what to surface. A filing is
# primary evidence; a StockTwits post is a crowd noise floor that is included
# because positioning matters, not because it is true.
SOURCE_WEIGHT = {
    "edgar": 5.0,
    "github": 2.0,
    "googlenews": 2.0,
    "hackernews": 1.5,
    "reddit": 1.2,
    "rss": 2.0,
    "web": 1.5,
    "youtube": 1.5,
    "x": 1.2,
    "stocktwits": 0.6,
}

DEFAULT_CADENCE_MIN = 60
SEEN_CAP = 4000          # per watch; oldest forgotten first
_WORD = re.compile(r"[a-z0-9']+")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "as",
    "at", "by", "with", "from", "that", "this", "it", "its", "be", "are",
    "was", "will", "has", "have", "after", "over", "into", "says", "said",
    "new", "up", "down", "amid", "but", "not", "you", "your", "his", "her",
}


@dataclass
class Watch:
    """One standing thing ARIA keeps an eye on."""
    id: str
    kind: str                       # "ticker" | "topic" | "feed"
    query: str
    cadence_minutes: int = DEFAULT_CADENCE_MIN
    enabled: bool = True
    source: str = "manual"          # "manual" | "portfolio"
    added_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_looked: str = ""
    looks: int = 0

    def due(self, now: datetime) -> bool:
        if not self.enabled:
            return False
        if not self.last_looked:
            return True
        try:
            last = datetime.fromisoformat(self.last_looked)
        except ValueError:
            return True
        return now - last >= timedelta(minutes=self.cadence_minutes)


@dataclass
class Observation:
    """Something the eye saw that it had not seen before."""
    watch_id: str
    url: str
    title: str
    source: str
    salience: float
    why: str                        # plain-language reason it surfaced
    seen_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    content_id: str = ""
    meta: dict = field(default_factory=dict)

    def cite(self) -> str:
        return citation({
            "title": self.title, "url": self.url, "fetched_at": self.seen_at,
            "backend": self.source, "content_id": self.content_id,
        })


# --------------------------------------------------------------- state I/O

def _load() -> dict:
    if not STATE_FILE.exists():
        return {"watches": {}, "seen": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        # A corrupt state file must not blind the eye permanently; losing the
        # baseline costs one noisy cycle, which is recoverable. Refusing to
        # start is not.
        logger.warning("eye state unreadable (%s) — starting a fresh baseline", exc)
        return {"watches": {}, "seen": {}}


def _save(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)          # atomic: a killed process never truncates


# ------------------------------------------------------------- attention

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48]


def watches() -> list[Watch]:
    return [Watch(**w) for w in _load()["watches"].values()]


def watch(query: str, kind: str = "topic", cadence_minutes: int = DEFAULT_CADENCE_MIN,
          source: str = "manual") -> Watch:
    """Add or update a standing watch."""
    state = _load()
    wid = f"{kind}:{_slug(query)}"
    existing = state["watches"].get(wid)
    entry = Watch(id=wid, kind=kind, query=query,
                  cadence_minutes=cadence_minutes, source=source)
    if existing:
        # Preserve the baseline and history — re-adding a watch must not
        # re-flood you with everything it has already reported.
        entry.added_at = existing.get("added_at", entry.added_at)
        entry.last_looked = existing.get("last_looked", "")
        entry.looks = existing.get("looks", 0)
    state["watches"][wid] = asdict(entry)
    _save(state)
    return entry


def unwatch(watch_id: str) -> bool:
    state = _load()
    if watch_id not in state["watches"]:
        return False
    del state["watches"][watch_id]
    state["seen"].pop(watch_id, None)
    _save(state)
    return True


def attend_to_portfolio() -> list[Watch]:
    """Point the eye at whatever is currently held or proposed.

    Attention should follow exposure without anyone remembering to update a
    list — the position you forgot to watch is the one that gaps.
    """
    tickers: set[str] = set()
    try:
        # The desk's own position file, not the broker: attention is cheap and
        # must never depend on a broker round-trip being up.
        from src.desk.position_manager import PositionManager
        for key, pos in (PositionManager().load_positions() or {}).items():
            ticker = ""
            if isinstance(pos, dict):
                ticker = (pos.get("ticker") or pos.get("symbol") or "").strip()
            tickers.add((ticker or str(key)).upper())
    except Exception as exc:              # noqa: BLE001 — desk optional
        logger.info("no desk positions available for attention: %s", exc)
    tickers.discard("")

    out = []
    for ticker in sorted(tickers):
        out.append(watch(ticker, kind="ticker", cadence_minutes=30, source="portfolio"))
    return out


# -------------------------------------------------------------- salience

def _tokens(title: str) -> set[str]:
    return {w for w in _WORD.findall(title.lower()) if w not in _STOP and len(w) > 2}


def _corroboration(doc, others) -> int:
    """How many DISTINCT sources are telling the same story.

    Overlapping headline tokens is a crude clustering rule, and it is the
    right amount of clever here: two outlets rewriting one wire story share
    most of their nouns.
    """
    mine = _tokens(doc.title)
    if len(mine) < 3:
        return 1
    sources = {doc.source}
    for other in others:
        if other.url == doc.url:
            continue
        overlap = mine & _tokens(other.title)
        if len(overlap) >= max(3, len(mine) // 3):
            sources.add(other.source)
    return len(sources)


def _score(doc, others) -> tuple[float, str]:
    weight = SOURCE_WEIGHT.get(doc.source, 1.0)
    corroboration = _corroboration(doc, others)
    meta = doc.meta or {}
    engagement = (meta.get("score") or 0) + (meta.get("points") or 0) + \
                 (meta.get("num_comments") or 0) + (meta.get("like_count") or 0)

    # Engagement is logarithmic on purpose: the difference between 10 and 100
    # upvotes matters, between 5,000 and 50,000 barely does.
    from math import log10
    engagement_term = log10(1 + max(0, engagement))
    score = weight * (1.0 + 0.8 * (corroboration - 1)) + engagement_term

    if corroboration > 1:
        why = f"{corroboration} independent sources carrying it"
    elif doc.source == "edgar":
        why = f"primary filing ({meta.get('form', 'SEC')})"
    elif engagement > 100:
        why = f"unusual engagement ({engagement})"
    else:
        why = f"new on {doc.source}"
    return round(score, 3), why


# ----------------------------------------------------------------- the eye

def blink(limit_per_source: int = 10, max_observations: int = 12,
          force: bool = False) -> dict:
    """One perception cycle: look at every due watch, report what is new.

    Returns a summary — how many watches were looked at, what was seen, and
    which sources failed. A watch's FIRST look always returns zero
    observations and only records the baseline; there is no such thing as
    news on the first glance.
    """
    state = _load()
    now = datetime.now(timezone.utc)
    due = [Watch(**w) for w in state["watches"].values()]
    due = [w for w in due if force or w.due(now)]

    summary = {
        "at": now.isoformat(),
        "watches_total": len(state["watches"]),
        "watches_looked": 0,
        "observations": [],
        "primed": [],
        "failed": {},
    }
    if not due:
        return summary

    fresh: list[Observation] = []
    for entry in due:
        ticker = entry.query.upper() if entry.kind == "ticker" else None
        try:
            sweep = hunt(entry.query, ticker=ticker,
                         limit_per_source=limit_per_source, store=True)
        except Exception as exc:              # noqa: BLE001 — one watch must not blind the eye
            logger.warning("eye: watch %s failed — %s", entry.id, exc)
            summary["failed"][entry.id] = f"{type(exc).__name__}: {exc}"
            continue

        summary["watches_looked"] += 1
        summary["failed"].update(
            {f"{entry.id}/{k}": v for k, v in sweep.failed.items()}
        )

        seen = set(state["seen"].get(entry.id, []))
        first_look = entry.looks == 0

        novel = [d for d in sweep.leads if (d.meta.get("content_id") or d.url) not in seen]
        for doc in novel:
            seen.add(doc.meta.get("content_id") or doc.url)

        if first_look:
            summary["primed"].append({"watch": entry.id, "baseline": len(novel)})
        else:
            for doc in novel:
                score, why = _score(doc, sweep.leads)
                fresh.append(Observation(
                    watch_id=entry.id, url=doc.url, title=doc.title,
                    source=doc.source, salience=score, why=why,
                    content_id=doc.meta.get("content_id", ""),
                    meta={k: v for k, v in (doc.meta or {}).items()
                          if k in ("form", "filed", "company", "score", "points",
                                   "published", "publisher", "subreddit",
                                   "bull_ratio", "num_comments")},
                ))

        # Forget the oldest ids rather than growing without bound.
        state["seen"][entry.id] = list(seen)[-SEEN_CAP:]
        entry.last_looked = now.isoformat()
        entry.looks += 1
        state["watches"][entry.id] = asdict(entry)

    fresh.sort(key=lambda o: o.salience, reverse=True)
    kept = fresh[:max_observations]

    if kept:
        OBSERVATIONS.parent.mkdir(parents=True, exist_ok=True)
        with OBSERVATIONS.open("a", encoding="utf-8") as fh:
            for obs in kept:
                fh.write(json.dumps(asdict(obs), ensure_ascii=False) + "\n")

    _save(state)
    summary["observations"] = [asdict(o) for o in kept]
    summary["suppressed"] = max(0, len(fresh) - len(kept))
    logger.info("eye blink: %d watches, %d new, %d surfaced",
                summary["watches_looked"], len(fresh), len(kept))
    return summary


def recent(hours: int = 24, limit: int = 50, min_salience: float = 0.0) -> list[dict]:
    """What the eye has noticed lately, most salient first."""
    if not OBSERVATIONS.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for line in OBSERVATIONS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            when = datetime.fromisoformat(row["seen_at"])
        except (ValueError, KeyError):
            continue
        if when >= cutoff and row.get("salience", 0) >= min_salience:
            out.append(row)
    out.sort(key=lambda r: r.get("salience", 0), reverse=True)
    return out[:limit]


def briefing(hours: int = 12, limit: int = 8) -> str:
    """What the eye saw, as text for the brain's REASON step or a spoken brief.

    Empty string when nothing salient happened — silence is a valid report,
    and a perception layer that always has something to say is not perceiving,
    it is filling airtime.
    """
    rows = recent(hours=hours, limit=limit)
    if not rows:
        return ""
    lines = [f"What I noticed on the internet in the last {hours}h:"]
    for row in rows:
        lines.append(
            f"- [{row['source']}] {row['title']} — {row['why']} ({row['url']})"
        )
    return "\n".join(lines)
