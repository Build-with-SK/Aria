"""
src/research/leads.py
=====================
One query, every source that can answer it, in parallel.

    from src.research import hunt
    hits = hunt("NVDA supply constraint", ticker="NVDA")

Sources are queried concurrently because the slowest one should not set the
latency of the whole sweep, and a source that is down or throttled must not
take the others with it — every failure is caught, recorded per-source, and
reported alongside the results. A partial sweep that says which legs failed
is useful; a sweep that silently returns fewer leads is a trap.

Ranking is deliberately crude: recency and corroboration across independent
sources. It is a triage order for a human or for the brain's RECALL step,
not a score, and nothing downstream should treat it as one.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .base import Document
from .store import archive

logger = logging.getLogger(__name__)


@dataclass
class Sweep:
    """Result of one fan-out: the leads, plus what each leg actually did."""
    query: str
    leads: list[Document] = field(default_factory=list)
    ran: dict[str, int] = field(default_factory=dict)       # source -> hit count
    failed: dict[str, str] = field(default_factory=dict)    # source -> error
    skipped: dict[str, str] = field(default_factory=dict)   # source -> why
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def as_dict(self) -> dict:
        return {
            "query": self.query,
            "started_at": self.started_at,
            "lead_count": len(self.leads),
            "ran": self.ran,
            "failed": self.failed,
            "skipped": self.skipped,
            "leads": [
                {
                    "url": d.url, "title": d.title, "source": d.source,
                    "backend": d.backend, "excerpt": d.text[:400],
                    "meta": d.meta,
                }
                for d in self.leads
            ],
        }


def _searchable():
    """Every registered source exposing search(), with its availability."""
    from .reach import SOURCES
    for source in SOURCES:
        if callable(getattr(source, "search", None)):
            yield source


def _domain(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().lstrip("www.")
    except ValueError:
        return ""


def _rank(docs: list[Document]) -> list[Document]:
    """Corroboration first, then engagement. Both are weak signals — the point
    is only to put the most-likely-worth-reading item at the top."""
    by_domain: dict[str, int] = {}
    for doc in docs:
        by_domain[_domain(doc.url)] = by_domain.get(_domain(doc.url), 0) + 1

    def key(doc: Document):
        meta = doc.meta or {}
        engagement = (
            (meta.get("score") or 0)
            + (meta.get("points") or 0)
            + (meta.get("like_count") or 0)
            + (meta.get("num_comments") or 0)
        )
        distinct = len({d.source for d in docs if _domain(d.url) == _domain(doc.url)})
        return (distinct, engagement)

    return sorted(docs, key=key, reverse=True)


def hunt(query: str, ticker: str | None = None, limit_per_source: int = 15,
         store: bool = True, timeout: int = 45) -> Sweep:
    """Search every available source for *query* at once.

    `ticker` additionally pulls the StockTwits stream for that symbol, which
    is a lookup rather than a search and so is not covered by the query.
    """
    sweep = Sweep(query=query)
    jobs = {}

    with ThreadPoolExecutor(max_workers=8) as pool:
        for source in _searchable():
            ok, detail = source.available()
            if not ok:
                sweep.skipped[source.name] = detail
                continue
            jobs[pool.submit(source.search, query, limit=limit_per_source)] = source.name

        if ticker:
            from .sources.markets import StockTwitsSource
            jobs[pool.submit(StockTwitsSource().symbol, ticker.upper())] = "stocktwits"

        seen: set[str] = set()
        for future in as_completed(jobs, timeout=timeout):
            name = jobs[future]
            try:
                result = future.result()
            except Exception as exc:                    # noqa: BLE001 — one leg must not kill the sweep
                sweep.failed[name] = f"{type(exc).__name__}: {exc}"
                logger.warning("lead sweep: %s failed — %s", name, exc)
                continue

            docs = result if isinstance(result, list) else [result]
            fresh = [d for d in docs if d.url not in seen]
            seen.update(d.url for d in fresh)
            sweep.ran[name] = len(fresh)
            sweep.leads.extend(fresh)

    sweep.leads = _rank(sweep.leads)
    if store:
        for doc in sweep.leads:
            try:
                doc.meta["content_id"] = archive(doc)
            except OSError as exc:
                logger.warning("could not archive %s: %s", doc.url, exc)
    return sweep
