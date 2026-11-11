"""
src/core/readiness.py
=====================
Is this process fit to serve — not merely alive?

LIVENESS VERSUS READINESS
-------------------------
`/health` answers "is something listening on this port". A process that has
started, cannot write its data directory and has no owner credential configured
passes that check, and a supervisor that only asks it will happily route traffic
to a server nobody can sign in to.

Readiness is the stricter question, and it is what the watchdog and the deploy
script ask:

  * an owner credential is configured      (otherwise: SERVER NOT INITIALIZED)
  * the data directory is writable         (otherwise every write fails later)
  * the session store is readable          (otherwise everyone is signed out)
  * there is disk left to write to         (otherwise the logs take the box down)

WHAT IS PUBLIC
--------------
`GET /ready` says true or false and nothing else. The reasons are logged on
every change of state and reported to signed-in callers through
`/api/system/health`. An anonymous caller learning WHICH subsystem is failing
learns the shape of the owner's machine; learning that "something is not ready"
tells them no more than a 503 from anywhere else would.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"

#: Below this, the box is treated as not ready. Chosen to leave room for a day of
#: logs and a backup archive before anything actually fails to write.
MIN_FREE_BYTES = 512 * 1024 * 1024

_last: dict = {"ready": None}
_lock = threading.Lock()


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".ready-probe-{uuid.uuid4().hex[:8]}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def check() -> dict:
    """{ready: bool, reasons: [str]}. Never raises."""
    reasons: list[str] = []

    try:
        from src.auth import session as sess
        if not sess.configured():
            reasons.append("SERVER NOT INITIALIZED: no owner credential "
                           "(ARIA_OWNER_TOKEN / ARIA_OWNER_TOKEN_HASH)")
        store = sess.SESSIONS.store
        if store.exists():
            try:
                json.loads(store.read_text(encoding="utf-8") or "{}")
            except (OSError, ValueError):
                reasons.append("session store is unreadable — every session "
                               "is being treated as signed out")
    except Exception as exc:                          # pragma: no cover
        reasons.append(f"auth layer failed to load: {type(exc).__name__}")

    if not _writable(DATA):
        reasons.append(f"data directory is not writable: {DATA}")

    try:
        free = shutil.disk_usage(DATA if DATA.exists() else ROOT).free
        if free < MIN_FREE_BYTES:
            reasons.append(f"low disk: {free // (1024 * 1024)} MB free "
                           f"(needs {MIN_FREE_BYTES // (1024 * 1024)} MB)")
    except OSError:
        reasons.append("disk usage unreadable")

    ready = not reasons
    with _lock:
        if _last["ready"] is not ready:
            (logger.info if ready else logger.warning)(
                "readiness: %s%s", "READY" if ready else "NOT READY",
                "" if ready else " — " + "; ".join(reasons))
            _last["ready"] = ready
    return {"ready": ready, "reasons": reasons}


def pid_info() -> dict:
    """For the private view: which process answered."""
    return {"pid": os.getpid()}
