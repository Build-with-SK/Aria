"""
src/research/store.py
=====================
The citable archive. Nothing fetched may reach a signal without landing here
first.

The rule this enforces: a claim in a report must be traceable to bytes that
were really retrieved, at a stated time, from a stated URL, by a stated
backend. Scrapers rot and pages get edited, so "go re-fetch it" is not a
citation. Content is written once, addressed by SHA-256, and never rewritten;
the daily index is append-only JSONL.

    data/research/docs/<sha256>.md      immutable content
    data/research/index/YYYY-MM-DD.jsonl  append-only provenance records
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .base import Document

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
STORE = ROOT / "data" / "research"
DOCS = STORE / "docs"
INDEX = STORE / "index"


def content_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def archive(doc: Document) -> str:
    """Persist *doc* and return its content id.

    Identical content fetched twice writes the body once but records both
    retrievals — that the same bytes came back an hour later is itself
    evidence, and losing it would hide when a source went stale.
    """
    DOCS.mkdir(parents=True, exist_ok=True)
    INDEX.mkdir(parents=True, exist_ok=True)

    cid = content_id(doc.text)
    body = DOCS / f"{cid}.md"
    if not body.exists():
        body.write_text(doc.text, encoding="utf-8")

    record = {
        "content_id": cid,
        "url": doc.url,
        "title": doc.title,
        "source": doc.source,
        "backend": doc.backend,
        "fetched_at": doc.fetched_at,
        "chars": len(doc.text),
        "meta": doc.meta,
    }
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with (INDEX / f"{day}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    logger.info("archived %s (%s, %d chars) as %s", doc.url, doc.backend, len(doc.text), cid[:12])
    return cid


def load(content_id_: str) -> str | None:
    """Return archived content by id, or None if it was never stored."""
    body = DOCS / f"{content_id_}.md"
    return body.read_text(encoding="utf-8") if body.exists() else None


def records(day: str | None = None) -> list[dict]:
    """Provenance records for one UTC day (default today), oldest first."""
    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = INDEX / f"{day}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            logger.warning("skipping malformed index line in %s", path.name)
    return out


def citation(record: dict) -> str:
    """One-line citation for a report footnote."""
    when = str(record.get("fetched_at", ""))[:19].replace("T", " ")
    return (
        f"{record.get('title') or record.get('url')} — {record.get('url')} "
        f"(retrieved {when} UTC via {record.get('backend')}, "
        f"doc {str(record.get('content_id', ''))[:12]})"
    )
