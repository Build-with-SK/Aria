"""
src/risk/decisions.py
=====================
Every portfolio-risk decision, kept so it can be asked about afterwards:

    Why did ARIA approve this?        verdict, checks, binding limit
    Why did it reduce or reject it?   reasons, caps (largest size per limit)
    What exposure caused it?          before / after / incremental
    What factor exposure?             factors
    What market session?              session, plan
    What data, as of when?            data: portfolio as-of, price time,
                                      FX pair/rate/date, last bars used

Append-only JSONL in data/trading/ (runtime state, gitignored). Nothing
secret is in a decision — no account numbers, keys or tokens are ever passed
to `assess()` — and `record()` keeps only the decision's own fields.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "data" / "trading" / "risk_decisions.jsonl"
_lock = threading.Lock()

FIELDS = ("id", "at", "verdict", "symbol", "side", "requested_qty", "approved_qty",
          "binding", "reasons", "checks", "caps", "before", "after", "incremental",
          "liquidity", "factors", "session", "plan", "data", "limits", "timings_ms")


def record(verdict, *, path: str, context: Optional[dict] = None) -> dict:
    """Append one decision. Never raises: a full disk must not turn into a
    trading decision."""
    d = verdict.to_dict() if hasattr(verdict, "to_dict") else dict(verdict)
    row = {k: d.get(k) for k in FIELDS}
    row["path"] = path
    row["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row["context"] = {k: v for k, v in (context or {}).items()
                      if isinstance(v, (str, int, float, bool)) or v is None}
    try:
        with _lock:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")
    except Exception as exc:                          # pragma: no cover
        logger.error("risk decision %s not recorded: %s", row.get("id"), exc)
    return row


def query(*, symbol: Optional[str] = None, verdict: Optional[str] = None,
          path: Optional[str] = None, decision_id: Optional[str] = None,
          limit: int = 100) -> list[dict]:
    """Most recent first."""
    if not LOG.exists():
        return []
    out = []
    for line in reversed(LOG.read_text(encoding="utf-8").splitlines()):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if symbol and r.get("symbol") != symbol:
            continue
        if verdict and r.get("verdict") != verdict:
            continue
        if path and r.get("path") != path:
            continue
        if decision_id and r.get("id") != decision_id:
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out
