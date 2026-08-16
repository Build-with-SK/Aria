"""
src/auth/users.py
=================
Who has ever signed in, and when they were last here.

WHY THIS IS SMALL ON PURPOSE
----------------------------
The owner asked for "data of the people using ARIA". This records the least
that answers that question: the provider identity that signed in, when they
first did, when they were last seen, and how many times. Nothing about what
they looked at, nothing they did not hand over by signing in.

That restraint is not squeamishness. The moment real people sign in, this file
holds personal data under UK GDPR, and the owner becomes a controller with
obligations: a lawful basis, a privacy notice telling people what is kept, and
the ability to delete someone on request. A small, boring, documented record is
one you can honour those obligations against. A behavioural log accumulated
"in case it is useful later" is one you cannot.

`delete(sub)` exists for exactly that reason and is not decoration.

STORAGE
-------
JSON Lines at data/auth/users.jsonl, last-write-wins per subject, guarded by a
process lock. Same shape as the rest of this codebase's small records — no
database to run, readable with a text editor, and trivially exportable.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
USERS_FILE = ROOT / "data" / "auth" / "users.jsonl"

_LOCK = threading.Lock()


def _load() -> dict[str, dict]:
    """{key: record}. Later lines win, so the file can be appended to."""
    out: dict[str, dict] = {}
    if not USERS_FILE.exists():
        return out
    try:
        for line in USERS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = rec.get("key")
            if key:
                out[key] = rec
    except OSError as e:
        logger.warning("users file unreadable: %s", e)
    return out


def _rewrite(records: dict[str, dict]) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = USERS_FILE.with_suffix(".jsonl.tmp")
    tmp.write_text(
        "\n".join(json.dumps(r, default=str) for r in records.values()) + "\n",
        encoding="utf-8")
    tmp.replace(USERS_FILE)


def _key(identity: dict) -> str:
    """Provider + subject. Email is not the key — people change theirs, and two
    providers can report the same address for different accounts."""
    return f"{identity.get('provider') or 'unknown'}:{identity.get('sub') or ''}"


def record_sign_in(identity: dict, *, owner: bool = False) -> dict:
    """Upsert on sign-in. Never raises — a bookkeeping failure must not stop
    somebody logging in."""
    try:
        key = _key(identity)
        if not identity.get("sub"):
            return {}
        now = datetime.now().isoformat(timespec="seconds")
        with _LOCK:
            records = _load()
            rec = records.get(key) or {
                "key": key,
                "provider": identity.get("provider"),
                "sub": identity.get("sub"),
                "first_seen": now,
                "sign_ins": 0,
            }
            rec.update({
                "email": (identity.get("email") or "").lower() or None,
                "name": identity.get("name") or None,
                "owner": bool(owner),
                "last_seen": now,
                "sign_ins": int(rec.get("sign_ins", 0)) + 1,
            })
            records[key] = rec
            _rewrite(records)
        return rec
    except Exception as e:                                  # pragma: no cover
        logger.warning("could not record sign-in: %s", e)
        return {}


def list_users() -> list[dict]:
    """Everyone who has signed in, most recently seen first."""
    records = list(_load().values())
    records.sort(key=lambda r: str(r.get("last_seen") or ""), reverse=True)
    return records


def stats() -> dict:
    users = list_users()
    return {
        "total": len(users),
        "owners": sum(1 for u in users if u.get("owner")),
        "by_provider": {
            p: sum(1 for u in users if u.get("provider") == p)
            for p in sorted({u.get("provider") or "unknown" for u in users})
        },
        "most_recent": users[0].get("last_seen") if users else None,
    }


def delete(key: str) -> bool:
    """Erase one person's record. Required to honour a deletion request, and
    the reason this module keeps little enough that erasing is meaningful."""
    with _LOCK:
        records = _load()
        if key not in records:
            return False
        records.pop(key)
        _rewrite(records)
    logger.info("auth: erased user record %s", key)
    return True
