"""
src/core/workers.py
===================
The registry of continuous processes — §29 and §30 of the brief.

WHAT WAS WRONG
--------------
The backend started six daemon threads and then forgot about them:

    threading.Thread(target=_start_brain_if_ollama, daemon=True).start()
    threading.Thread(target=_start_desk, daemon=True).start()
    threading.Thread(target=_discover_models, daemon=True).start()
    threading.Thread(target=_fx_monitor_loop, daemon=True).start()
    threading.Thread(target=_start_quant_lab, daemon=True).start()
    threading.Thread(target=_outcome_resolver_loop, daemon=True).start()

Every one of them catches its own exceptions and keeps going, which is correct
for uptime and catastrophic for observability: a worker whose vendor died in
July is indistinguishable from one that ran a second ago. This was not
hypothetical. The V5 prediction leg last fired on 2026-08-17 while its resolver
ran hourly, and nothing anywhere went red — the failure was found by reading a
heartbeat file by hand.

WHAT THIS PROVIDES
------------------
A worker declares itself once, then reports each tick. The registry keeps the
last run, the next expected run, the last error, a rolling tick count, and a
derived state:

    ok        ran within its expected interval
    late      overdue but not yet alarming (interval exceeded, under 3×)
    stalled   more than 3× its interval since the last tick
    failing   last tick raised
    unknown   registered but has never reported

`heartbeat()` is a context manager, so a worker cannot forget to report a
failure — the exception is recorded and re-raised.

The state lives in SQLite rather than in memory because the API process and the
daemon threads must agree on it, and because "when did this last run?" has to
survive a restart. A registry that forgets on reboot cannot detect the failure
mode it exists for.
"""
from __future__ import annotations

import json
import logging
import threading
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional

from src.core.bus import connect as _connect, publish

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workers (
    name            TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    loop            TEXT NOT NULL DEFAULT 'medium',  -- fast | medium | slow
    interval_s      INTEGER,
    registered_at   TEXT NOT NULL,
    last_start      TEXT,
    last_ok         TEXT,
    last_error_at   TEXT,
    last_error      TEXT,
    ticks           INTEGER NOT NULL DEFAULT 0,
    failures        INTEGER NOT NULL DEFAULT 0,
    last_result     TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1
);
"""

_lock = threading.RLock()
_ready = False

# The three cadences of §19. A worker declares which one it belongs to, and the
# UI can then show that ARIA's fast senses are alive even when the slow
# strategy evaluation last ran on Sunday — which is correct, not a fault.
LOOPS = ("fast", "medium", "slow")


@contextmanager
def _db():
    global _ready
    with _connect() as conn:
        if not _ready:
            with _lock:
                if not _ready:
                    conn.executescript(_SCHEMA)
                    conn.commit()
                    _ready = True
        yield conn


def register(name: str, label: str, *, loop: str = "medium",
             interval_s: Optional[int] = None, enabled: bool = True) -> None:
    """Declare a worker. Idempotent — re-registering updates the description
    but keeps the accumulated history."""
    if loop not in LOOPS:
        loop = "medium"
    try:
        with _db() as conn:
            conn.execute("""
                INSERT INTO workers (name, label, loop, interval_s, registered_at, enabled)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET
                    label=excluded.label, loop=excluded.loop,
                    interval_s=excluded.interval_s, enabled=excluded.enabled""",
                (name, label, loop, interval_s,
                 datetime.now().isoformat(timespec="seconds"), int(enabled)))
    except Exception as e:
        logger.warning("worker register failed for %s: %s", name, e)


def disable(name: str, why: str = "") -> None:
    """Mark a worker as deliberately not running.

    The distinction between "off on purpose" and "dead" is the whole value of
    the registry: ARIA_RUN_BRAIN=false on the Mac mini is a configuration, not
    an outage, and must not show red.
    """
    try:
        with _db() as conn:
            conn.execute("UPDATE workers SET enabled=0, last_error=? WHERE name=?",
                         (why or "disabled", name))
    except Exception as e:
        logger.warning("worker disable failed for %s: %s", name, e)


def tick_ok(name: str, result: Optional[dict] = None) -> None:
    """Record a successful run.

    Full microsecond precision, deliberately. These timestamps are COMPARED to
    each other in `_state_of` to decide whether the last thing that happened
    was a success or a failure; truncating to seconds made a failure that
    landed in the same second as the previous success compare equal, so the
    worker kept reporting `ok`. Minute-cadence loops would rarely hit it, but
    the reflex lane polls every three seconds — exactly where a swallowed
    failure matters most.
    """
    now = datetime.now().isoformat()
    try:
        with _db() as conn:
            conn.execute("""
                UPDATE workers SET last_ok=?, ticks=ticks+1, last_result=?
                WHERE name=?""",
                (now, json.dumps(result, default=str)[:2000] if result else None, name))
    except Exception as e:
        logger.warning("worker tick_ok failed for %s: %s", name, e)


def tick_failed(name: str, err: BaseException) -> None:
    """Record a failed run, and announce it once it is more than a blip."""
    now = datetime.now().isoformat()          # see tick_ok on precision
    detail = f"{type(err).__name__}: {err}"[:500]
    try:
        with _db() as conn:
            conn.execute("""
                UPDATE workers SET last_error_at=?, last_error=?, failures=failures+1
                WHERE name=?""", (now, detail, name))
            row = conn.execute("SELECT label, failures FROM workers WHERE name=?",
                               (name,)).fetchone()
    except Exception as e:
        logger.warning("worker tick_failed failed for %s: %s", name, e)
        return

    # One transient vendor timeout is noise; three in a row is a story. The bus
    # is for things worth a human's attention, so only the latter goes on it.
    if row and (row["failures"] or 0) % 3 == 1:
        publish("WORKER_FAILED", f"{row['label']} failed: {detail}"[:200],
                source=name, severity="warning",
                payload={"worker": name, "failures": row["failures"]})
    logger.debug("worker %s failed: %s\n%s", name, detail, traceback.format_exc())


@contextmanager
def heartbeat(name: str):
    """Wrap one tick of a worker.

        with workers.heartbeat("desk"):
            do_the_work()

    Success and failure are both recorded; the exception is re-raised so the
    caller's own error handling is unchanged. A worker cannot silently stop
    being counted, which is the failure this whole module exists to catch.
    """
    now = datetime.now().isoformat()
    try:
        with _db() as conn:
            conn.execute("UPDATE workers SET last_start=? WHERE name=?", (now, name))
    except Exception:
        pass
    try:
        yield
    except BaseException as e:      # noqa: BLE001 — recorded, then re-raised
        tick_failed(name, e)
        raise
    else:
        tick_ok(name)


def _state_of(row: dict, now: datetime) -> tuple[str, Optional[float]]:
    """Derive health from the timestamps. Returns (state, age_seconds)."""
    if not row.get("enabled"):
        return "disabled", None
    last = row.get("last_ok")
    if not last:
        # Never reported. If it failed on its first attempt, say so.
        return ("failing" if row.get("last_error_at") else "unknown"), None
    try:
        age = (now - datetime.fromisoformat(last)).total_seconds()
    except Exception:
        return "unknown", None

    err_at = row.get("last_error_at")
    if err_at:
        try:
            if datetime.fromisoformat(err_at) > datetime.fromisoformat(last):
                return "failing", age
        except Exception:
            pass

    iv = row.get("interval_s")
    if not iv:
        return "ok", age
    if age > iv * 3:
        return "stalled", age
    if age > iv * 1.5:
        return "late", age
    return "ok", age


def status() -> dict:
    """Every worker, its health, and one honest headline.

    `healthy` is false when anything is stalled or failing. It is NOT false
    merely because a slow-loop worker has not run today — that is what a slow
    loop means.
    """
    now = datetime.now()
    try:
        with _db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM workers ORDER BY loop, name").fetchall()]
    except Exception as e:
        logger.warning("worker status failed: %s", e)
        return {"workers": [], "healthy": None, "note": f"registry unreadable: {e}"}

    out = []
    for r in rows:
        state, age = _state_of(r, now)
        nxt = None
        if r.get("last_ok") and r.get("interval_s"):
            try:
                nxt = (datetime.fromisoformat(r["last_ok"])
                       + timedelta(seconds=r["interval_s"])).isoformat(timespec="seconds")
            except Exception:
                pass
        if r.get("last_result"):
            try:
                r["last_result"] = json.loads(r["last_result"])
            except Exception:
                pass
        out.append({**r, "state": state,
                    "age_seconds": None if age is None else round(age),
                    "next_expected": nxt})

    bad = [w for w in out if w["state"] in ("stalled", "failing")]
    unknown = [w for w in out if w["state"] == "unknown"]
    return {
        "workers": out,
        "counts": {s: sum(1 for w in out if w["state"] == s)
                   for s in ("ok", "late", "stalled", "failing", "unknown", "disabled")},
        "healthy": (None if not out else not bad),
        "degraded": [{"name": w["name"], "label": w["label"], "state": w["state"],
                      "last_error": w.get("last_error"),
                      "age_seconds": w.get("age_seconds")} for w in bad],
        "never_reported": [w["name"] for w in unknown],
    }


def sweep() -> dict:
    """Announce workers that have gone quiet.

    Called on a slow cadence. Publishing a WORKER_FAILED for a stall is what
    turns "the loop stopped in July and nobody noticed" into a line in the
    activity stream on the day it happens.
    """
    st = status()
    announced = []
    for w in st.get("degraded", []):
        if w["state"] == "stalled":
            publish("WORKER_FAILED",
                    f"{w['label']} has not reported for "
                    f"{round((w['age_seconds'] or 0) / 3600, 1)}h",
                    source=w["name"], severity="warning",
                    payload={"worker": w["name"], "state": "stalled"})
            announced.append(w["name"])
    return {"announced": announced, "counts": st.get("counts", {})}
