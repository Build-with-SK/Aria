"""
src/core/bus.py
===============
The event bus — one append-only log of things that actually happened.

WHY THIS EXISTS
---------------
ARIA had eleven subsystems and no way for any of them to tell another one that
something had occurred. The desk closed a trade; the learning engine never
heard. The research eye found an anomaly; no strategy was re-evaluated. The
brain changed its mind about the regime; the portfolio kept its old assumption.
Each one wrote a file, and the coupling was "somebody will open it later".

An event bus is the smallest thing that fixes that without turning every module
into an import of every other module. A component announces; whoever cares
subscribes. Nobody has to know who is listening.

WHAT AN EVENT IS
----------------
An event is a claim that something happened, with enough context to act on and
enough provenance to audit. It is NOT a log line. The distinction matters and
is enforced by the vocabulary below: if you cannot name your event from KINDS,
what you have is a log line, and `logging` already handles those.

The activity stream in the UI reads this table and nothing else. That is the
whole point of §27 of the brief: an activity feed that renders real events
cannot drift into theatre, because the only way to make a line appear is for a
subsystem to have genuinely done something.

DURABILITY
----------
Events are written to SQLite before subscribers run, so a subscriber that
throws cannot lose the record. Subscribers are called synchronously in the
publishing thread but every exception is swallowed and logged: a broken
listener must never take down the component that emitted the event.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "data" / "aria_core.db"


def _db_path() -> Path:
    """Where the event log lives, resolved at call time.

    ARIA_CORE_DB redirects it. tests/conftest.py sets that to a temp file for
    the whole session, because the moment the daemons started publishing, any
    test that exercised one of them wrote its FIXTURES into the real log — the
    production activity stream picked up rows reading "Nvidia cuts guidance"
    and "story 2" from tests/test_research_eye.py. An activity feed whose whole
    claim is that every line really happened cannot be allowed to ingest test
    data, so the redirection is a correctness requirement, not tidiness.

    Read at call time rather than at import so setting the variable after the
    module is imported still takes effect.
    """
    override = os.environ.get("ARIA_CORE_DB")
    return Path(override) if override else DB_PATH

# How long the log is kept. Events are cheap but not free, and an activity
# stream nobody will ever scroll to is just disk. Ninety days covers every
# horizon ARIA predicts on.
RETENTION_DAYS = 90

# ── the vocabulary ──────────────────────────────────────────────────────────
# A closed set on purpose. An open string field would let every module invent
# its own spelling of "research finished" and the stream would become
# unfilterable within a month.
KINDS = {
    # world / market
    "MARKET_UPDATE",        # a fresh market state was computed
    "REGIME_CHANGED",       # the regime classification moved
    "ANOMALY_DETECTED",     # something is behaving unlike itself
    "NEWS_EVENT",           # an external event was ingested
    # research
    "RESEARCH_STARTED",
    "RESEARCH_COMPLETED",
    "HYPOTHESIS_CREATED",
    "OBSERVATION_RECORDED",
    # predictions and learning
    "PREDICTION_CREATED",
    "PREDICTION_RESOLVED",
    "CALIBRATION_UPDATED",
    "LESSON_RECORDED",
    # strategy
    "STRATEGY_TEST_COMPLETED",
    "STRATEGY_UPDATED",
    "STRATEGY_PROMOTED",
    "STRATEGY_RETIRED",
    "MODEL_RETRAINED",
    # portfolio and risk
    "PORTFOLIO_RISK_CHANGED",
    "POSITION_OPENED",
    "POSITION_CLOSED",
    "STRESS_TEST_COMPLETED",
    # decisions
    "APPROVAL_REQUIRED",
    "APPROVAL_COMPLETED",
    "DECISION_RECORDED",
    # the system talking about itself
    "WORKER_STARTED",
    "WORKER_HEARTBEAT",
    "WORKER_FAILED",
    "DATA_STALE",
    "SYSTEM_NOTE",
    # consultation with SENTINEL — a SEPARATE intelligence, reached over HTTP.
    # These are here because a consultation nobody can see having happened is
    # unauditable: the whole value of a second opinion is that the record shows
    # it was sought, what it said, and whether ARIA took it. SENTINEL_UNAVAILABLE
    # matters most — an absent consultant must leave a trace, or its silence
    # becomes indistinguishable from agreement.
    "SENTINEL_CONSULTED",
    "SENTINEL_UNAVAILABLE",
    "SENTINEL_OUTCOME",
}

# Severity is about attention, not about whether something is bad. "info" is
# the overwhelming majority; "action" means a human has something to do.
SEVERITIES = ("info", "notable", "warning", "action")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    source      TEXT NOT NULL,
    subject     TEXT,
    severity    TEXT NOT NULL DEFAULT 'info',
    summary     TEXT NOT NULL,
    payload     TEXT,
    provenance  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts      ON events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_kind    ON events(kind, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_subject ON events(subject, ts DESC);
"""

_lock = threading.RLock()
_subscribers: list[tuple[Optional[frozenset], Callable]] = []
_initialised = False


@contextmanager
def connect():
    """A connection to the core DB, with the schema guaranteed present.

    WAL because several daemon threads publish concurrently and a reader
    holding the activity stream open must not block the desk from recording a
    fill.
    """
    global _initialised
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        if not _initialised:
            with _lock:
                if not _initialised:
                    conn.executescript(_SCHEMA)
                    conn.commit()
                    _initialised = True
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def publish(kind: str,
            summary: str,
            *,
            source: str = "aria",
            subject: Optional[str] = None,
            severity: str = "info",
            payload: Optional[dict] = None,
            provenance: Optional[dict] = None) -> Optional[str]:
    """Record that something happened, then tell anyone listening.

    Returns the event id, or None if the event could not be persisted — the
    caller is a subsystem doing real work and must never crash because the
    bus is unavailable, so failures here are logged and swallowed.

    `provenance` answers §45: where did this come from, when was it collected,
    how reliable is it. Anything derived from an outside source should carry
    it; anything computed from ARIA's own state need not.
    """
    if kind not in KINDS:
        # Loud, because a typo here means the event silently never matches a
        # subscriber or a UI filter and the bug is invisible at the call site.
        logger.warning("bus.publish: unknown kind %r (event dropped)", kind)
        return None
    if severity not in SEVERITIES:
        severity = "info"

    ev_id = uuid.uuid4().hex[:16]
    ts = datetime.now().isoformat(timespec="seconds")
    row = {
        "id": ev_id, "ts": ts, "kind": kind, "source": source,
        "subject": subject, "severity": severity, "summary": summary[:600],
        "payload": json.dumps(payload, default=str) if payload else None,
        "provenance": json.dumps(provenance, default=str) if provenance else None,
    }

    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO events (id, ts, kind, source, subject, severity, summary,"
                " payload, provenance) VALUES (:id,:ts,:kind,:source,:subject,:severity,"
                ":summary,:payload,:provenance)", row)
    except Exception as e:
        logger.warning("bus.publish failed for %s: %s", kind, e)
        return None

    for kinds, fn in list(_subscribers):
        if kinds is not None and kind not in kinds:
            continue
        try:
            fn(dict(row))
        except Exception:
            logger.exception("bus subscriber %r failed on %s",
                             getattr(fn, "__name__", fn), kind)
    return ev_id


def subscribe(fn: Callable[[dict], Any],
              kinds: Optional[Iterable[str]] = None) -> Callable:
    """Call `fn(event_dict)` whenever a matching event is published.

    `kinds=None` means everything. Subscribers run in the publisher's thread,
    so they must be fast — anything slow should hand off to its own thread.
    """
    _subscribers.append((frozenset(kinds) if kinds else None, fn))
    return fn


def recent(limit: int = 60,
           kinds: Optional[Iterable[str]] = None,
           subject: Optional[str] = None,
           since: Optional[str] = None,
           min_severity: Optional[str] = None) -> list[dict]:
    """The activity stream. Newest first."""
    sql = "SELECT * FROM events WHERE 1=1"
    args: list[Any] = []
    if kinds:
        ks = list(kinds)
        sql += f" AND kind IN ({','.join('?' * len(ks))})"
        args += ks
    if subject:
        sql += " AND subject = ?"
        args.append(subject)
    if since:
        sql += " AND ts >= ?"
        args.append(since)
    if min_severity and min_severity in SEVERITIES:
        allowed = SEVERITIES[SEVERITIES.index(min_severity):]
        sql += f" AND severity IN ({','.join('?' * len(allowed))})"
        args += list(allowed)
    sql += " ORDER BY ts DESC, rowid DESC LIMIT ?"
    args.append(int(max(1, min(limit, 500))))

    try:
        with connect() as conn:
            rows = conn.execute(sql, args).fetchall()
    except Exception as e:
        logger.warning("bus.recent failed: %s", e)
        return []

    out = []
    for r in rows:
        d = dict(r)
        for k in ("payload", "provenance"):
            if d.get(k):
                try:
                    d[k] = json.loads(d[k])
                except Exception:
                    pass
        out.append(d)
    return out


def counts_since(hours: int = 24) -> dict:
    """How many of each kind of event in the window — the shape of the last day.

    This is what makes "has ARIA actually done anything today?" answerable
    without reading the stream line by line.
    """
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    try:
        with connect() as conn:
            rows = conn.execute(
                "SELECT kind, COUNT(*) n FROM events WHERE ts >= ? GROUP BY kind"
                " ORDER BY n DESC", (since,)).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) n FROM events WHERE ts >= ?", (since,)).fetchone()
    except Exception as e:
        logger.warning("bus.counts_since failed: %s", e)
        return {"window_hours": hours, "total": 0, "by_kind": {}}
    return {"window_hours": hours,
            "total": int(total["n"]) if total else 0,
            "by_kind": {r["kind"]: int(r["n"]) for r in rows}}


def prune(days: int = RETENTION_DAYS) -> int:
    """Drop events past the retention horizon. Returns rows removed."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    try:
        with connect() as conn:
            cur = conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            return cur.rowcount or 0
    except Exception as e:
        logger.warning("bus.prune failed: %s", e)
        return 0


def last_of(kind: str) -> Optional[dict]:
    """Most recent event of one kind, or None. Used for freshness checks."""
    got = recent(limit=1, kinds=[kind])
    return got[0] if got else None
