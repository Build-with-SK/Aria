"""
scripts/backfill_ledger_provenance.py
=====================================
Fill the ledger's provenance columns (added 2026-10-06) on existing rows —
ONLY with values that can be proven from what existed when each prediction
was made. Everything else stays NULL, which the ledger reads as "unknown".

    venv\\Scripts\\python.exe scripts\\backfill_ledger_provenance.py           # dry run
    venv\\Scripts\\python.exe scripts\\backfill_ledger_provenance.py --apply   # write

What is filled, and from what:

  probability_kind / p_correct
      v5        from data/v5/predictions.jsonl: a directional call's stated
                `confidence` (V5's P(direction correct)); neutral → "none"
      desk      "mapped" — conviction squashed onto 0.5–0.95, never a probability
      technical "none"   — an indicator vote count
  model_version   v5 rows: "v5|weights=<n>" from the log (the code that ran is
                  not recoverable, so no code hash is invented)
  information_session
                  v5 rows: from the log, or derived from the creation time and
                  the NYSE calendar; only for calls made outside the session
                  window (an intraday call has no session close)
  price_end / actual_return
                  v5 grades carried from the learning loop were written under
                  the wrong key names and lost; restored from the log's
                  price_then / realised_return
  regime_at / regime_basis
                  from the latest world snapshot taken AT OR BEFORE the
                  prediction and within 24 h of it — information the call
                  could have had. The snapshot's own staleness is recorded.

Never overwrites a non-NULL value. Idempotent. Backs the database up first.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WINDOW = timedelta(hours=24)


def _v5_rows(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("id"):
            out[f"v5:{r['id']}"] = r
    return out


def _snapshots(conn) -> list[tuple[datetime, dict]]:
    snaps = []
    try:
        rows = conn.execute("SELECT at, snapshot FROM world_snapshots ORDER BY at").fetchall()
    except sqlite3.Error:
        return snaps
    for at, body in rows:
        try:
            reg = (json.loads(body).get("market") or {}).get("regime") or {}
            snaps.append((datetime.fromisoformat(at), reg))
        except Exception:
            continue
    return snaps


def _regime_before(snaps, created: datetime):
    best = None
    for at, reg in snaps:
        if at <= created:
            best = (at, reg)
        else:
            break
    if not best or created - best[0] > WINDOW or not best[1].get("macro_model"):
        return None, None
    at, reg = best
    basis = (f"world_snapshot {at.isoformat(timespec='seconds')} (taken before the call); "
             f"regime inputs as of {reg.get('as_of') or 'unknown'}")
    if reg.get("stale"):
        basis += "; STALE inputs"
    return reg["macro_model"], basis


def plan(conn, v5_log: dict) -> list[tuple[str, dict]]:
    """[(prediction id, {column: value})] — only NULL columns, only proven values."""
    from src.core.ingest import v5_stated_probability
    from src.v5.learning import information_key

    snaps = _snapshots(conn)
    conn.row_factory = sqlite3.Row
    updates = []
    for r in conn.execute("SELECT * FROM predictions").fetchall():
        upd: dict = {}
        src = r["source"]
        if r["probability_kind"] is None:
            if src == "v5":
                row = v5_log.get(r["external_ref"] or "")
                if row is not None:
                    p = v5_stated_probability(row)
                    upd["probability_kind"] = "stated" if p is not None else "none"
                    if p is not None:
                        upd["p_correct"] = p
            elif src == "desk":
                upd["probability_kind"] = "mapped" if r["probability"] is not None else "none"
            elif src == "technical":
                upd["probability_kind"] = "none"

        if src == "v5":
            row = v5_log.get(r["external_ref"] or "")
            if row is not None:
                if r["model_version"] is None and row.get("weights_version") is not None:
                    upd["model_version"] = row.get("model_version") or f"v5|weights={row['weights_version']}"
                if r["information_session"] is None:
                    sess, window = row.get("information_session"), row.get("in_session_window")
                    if sess is None:
                        try:
                            sess, window = information_key(datetime.fromisoformat(row["at"]))
                        except Exception:
                            sess, window = None, None
                    if sess and window is False and not str(sess).startswith("day:"):
                        upd["information_session"] = sess
                if r["resolved"] and r["price_end"] is None and row.get("price_then") is not None:
                    upd["price_end"] = row["price_then"]
                if r["resolved"] and r["actual_return"] is None and row.get("realised_return") is not None:
                    upd["actual_return"] = row["realised_return"]

        if r["regime_at"] is None and r["created_at"]:
            try:
                created = datetime.fromisoformat(r["created_at"])
            except ValueError:
                created = None
            if created is not None:
                reg, basis = _regime_before(snaps, created)
                if reg:
                    upd["regime_at"], upd["regime_basis"] = reg, basis

        if upd:
            updates.append((r["id"], upd))
    return updates


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--db", default=None, help="ledger database (default: ARIA's)")
    args = ap.parse_args()

    if args.db:
        import os
        os.environ["ARIA_CORE_DB"] = args.db
    from src.core import ledger
    from src.core.bus import _db_path
    db = Path(_db_path())
    with ledger.connect():                       # runs the additive migration
        pass

    v5_log = _v5_rows(ROOT / "data" / "v5" / "predictions.jsonl")
    conn = sqlite3.connect(db, timeout=30)
    updates = plan(conn, v5_log)

    counts: dict = {}
    for _, upd in updates:
        for k in upd:
            counts[k] = counts.get(k, 0) + 1
    print(json.dumps({"database": str(db), "rows_touched": len(updates),
                      "columns_filled": counts, "apply": args.apply}, indent=2))
    if not args.apply:
        print("dry run — no values written (the additive column migration, "
              "which only adds NULL columns, has been applied). Re-run with --apply.")
        return 0

    backup_dir = ROOT / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"aria_core_{datetime.now():%Y%m%d-%H%M%S}_pre_provenance.db"
    with sqlite3.connect(backup) as dst:
        conn.backup(dst)
    print(f"backup: {backup}")

    with conn:
        for pid, upd in updates:
            cols = ", ".join(f"{k}=COALESCE({k}, ?)" for k in upd)
            conn.execute(f"UPDATE predictions SET {cols} WHERE id=?", [*upd.values(), pid])
    conn.close()

    from src.core.bus import publish
    publish("SYSTEM_NOTE",
            f"ledger provenance backfill: {len(updates)} rows, columns {counts}"[:200],
            source="ledger", severity="info",
            payload={"columns_filled": counts, "backup": str(backup)})
    print("applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
