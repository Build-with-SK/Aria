"""
src/compression/store.py
========================
SQLite store behind the compression engine: retrievable stubs (compacted
tool results), checkpoints (full transcripts), and the typed audit log.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
DEFAULT_DB = ROOT / "data" / "compression_store.db"

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"      # Crockford base32


def ulid() -> str:
    """Minimal ULID: 48-bit ms timestamp + 80 random bits, base32."""
    ts = int(time.time() * 1000)
    chars = []
    for i in range(10):
        chars.append(_B32[(ts >> (45 - i * 5)) & 31])
    rand = int.from_bytes(os.urandom(10), "big")
    for i in range(16):
        chars.append(_B32[(rand >> (75 - i * 5)) & 31])
    return "".join(chars)


class CompressionStore:
    def __init__(self, db_path: Path | str = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS stubs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                content TEXT NOT NULL, created_at TEXT NOT NULL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL, action TEXT NOT NULL,
                turn_ids TEXT, tokens_before INTEGER, tokens_after INTEGER,
                tokens_saved INTEGER, detail TEXT)""")

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    # ── stubs & checkpoints ──────────────────────────────────────────────

    def put(self, kind: str, content: str) -> str:
        sid = ulid()
        with self._conn() as c:
            c.execute("INSERT INTO stubs (id, kind, content, created_at) "
                      "VALUES (?, ?, ?, ?)",
                      (sid, kind, content, datetime.now().isoformat()))
        return sid

    def get(self, stub_id: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT content FROM stubs WHERE id = ?",
                            (stub_id,)).fetchone()
        return row[0] if row else None

    # ── audit log ────────────────────────────────────────────────────────

    def audit(self, action: str, turn_ids: list, tokens_before: int,
              tokens_after: int, detail: str = ""):
        with self._conn() as c:
            c.execute("""INSERT INTO audit (at, action, turn_ids, tokens_before,
                         tokens_after, tokens_saved, detail)
                         VALUES (?, ?, ?, ?, ?, ?, ?)""",
                      (datetime.now().isoformat(), action,
                       json.dumps(turn_ids), tokens_before, tokens_after,
                       tokens_before - tokens_after, detail))

    def audit_log(self, n: int = 100) -> list[dict]:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?",
                             (n,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["turn_ids"] = json.loads(d.get("turn_ids") or "[]")
            except Exception:
                pass
            out.append(d)
        return out
