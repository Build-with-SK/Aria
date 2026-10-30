"""
src/core/ledger_restamp.py
==========================
Re-stamp the ML ledger rows that carry the London listing on a US price.

WHAT WENT WRONG (fixed 2026-10-27, c2ffa8b)
-------------------------------------------
The ML pass trains on signals.json keys, which are provider symbols fetched
verbatim: `AAL` there is American Airlines. Until the fix, the ledger stamped
those rows through the symbol master, where display `AAL` is Anglo American
(AAL.L). So 83 rows written between 2026-10-17 and 2026-10-25 say "AAL.L,
XLON" while their price is American Airlines' dollar close. The grader priced
AAL.L, found ~4,000p against ~$12, and skipped them: none was ever graded
right or wrong, but none can be graded either while the stamp is wrong.

WHAT THIS DOES
--------------
For each such row it writes what the fixed recorder would have written: the
stated signals identity (`AAL`), basis SOURCE_STATED, and the venue and
session state derived from that identity at the row's creation time. A row
the grader already skipped for this reason is reset to pending, so the grader
grades it on the right listing with the closes of its own dates. Nothing else
on a row changes. The claim, direction, price, horizon, information session
and creation time are what the model actually said.

A row is written only when the grader's own reference check, run on the
corrected stamp, finds the recorded price: the stated listing's close on the
row's anchor day must sit within the grader's tolerance. A row graded right
or wrong is never touched.

The dry run deliberately does not say whether a reset call would come out
right or wrong. The decision to repair the record must not depend on how the
repair scores.

USAGE
-----
    python -m src.core.ledger_restamp --db data/aria_core.db
        dry run: prints the plan and its hash, writes nothing
    python -m src.core.ledger_restamp --db data/aria_core.db --csv plan.csv
        dry run, plus the row-by-row plan as CSV
    python -m src.core.ledger_restamp --db data/aria_core.db --apply --plan-hash <hash>
        the owner-approved write; refuses unless a fresh plan has that hash

THE LEDGER IS LIVE
------------------
The backend's ledger loop grades every 30 minutes, and it reads the due rows
before it grades them. A pass that read a row before the re-stamp could write
a stale "skipped" over it afterwards. So --apply refuses while a grading pass
is running or could start within minutes (from the `workers` heartbeat). It
also refuses unless a fresh plan, rebuilt at apply time, matches the hash the
owner approved. The writes are compare-and-set inside one transaction, after
a WAL-safe backup.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

SOURCE = "ml-ensemble"
#: The fixed recorder went live when the backend restarted onto c2ffa8b.
#: Rows written from then on carry the stated identity; a disagreement there
#: would be a new defect to investigate, not something to re-stamp.
FIXED_AT = "2026-10-27T13:32:40"
BASIS = "SOURCE_STATED"
SKIP_NOTE = "GRADING_SKIPPED / IDENTITY_UNRESOLVED"

RESTAMP = "RESTAMP"
RESTAMP_AND_RESET = "RESTAMP_AND_RESET"
LEAVE = "LEAVE"
WRITES = (RESTAMP, RESTAMP_AND_RESET)

#: The columns a decision depends on. Grading a candidate, or anything else
#: that changes one of these, changes the plan, so --apply must refuse.
_FINGERPRINT_COLS = ("id", "subject", "source", "created_at", "price_at",
                     "provider_symbol", "identity_basis", "venue", "market_session",
                     "information_session", "resolved", "correct", "outcome_note")

#: How close to the ledger loop's next pass an apply may run.
GRADER_MARGIN = timedelta(minutes=5)

PriceOn = Callable[[str, str], Optional[float]]


def _connect_ro(db: Path) -> "closing[sqlite3.Connection]":
    """Read-only, and closed on exit (a `with` on a bare connection is not)."""
    conn = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return closing(conn)


def stated_symbol(subject: str) -> Optional[str]:
    """What the fixed ML recorder states for `subject` (recorders.py)."""
    try:
        from src.core import identity
        return identity.signal_identity(subject).provider_symbol or subject
    except Exception:
        return subject


def _candidates(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM predictions WHERE source=? AND created_at < ? "
        "AND provider_symbol IS NOT NULL AND provider_symbol != '' "
        "ORDER BY created_at, id", (SOURCE, FIXED_AT)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        want = stated_symbol(d["subject"])
        if want and d["provider_symbol"] != want:
            d["_stated"] = want
            out.append(d)
    return out


def _fingerprint(rows: list[dict]) -> str:
    body = [[r.get(c) for c in _FINGERPRINT_COLS] for r in sorted(rows, key=lambda r: r["id"])]
    return hashlib.sha256(json.dumps(body, default=str).encode()).hexdigest()


def _drift(got: Optional[float], ref: Optional[float]) -> Optional[float]:
    if got is None or not ref:
        return None
    return abs(got - ref) / ref


def plan_row(row: dict, price_on: PriceOn) -> dict:
    """The decision for one candidate row, with the evidence behind it."""
    from src.core import ledger
    from src.intelligence.provenance import derive_market_context

    new = row["_stated"]
    old = row["provider_symbol"]
    state = ("pending" if not row["resolved"] else
             "skipped" if row["correct"] is None else "graded")
    try:
        at = datetime.fromisoformat(str(row["created_at"]))
    except ValueError:
        at = None
    venue, session = derive_market_context(new, at)
    # The anchor exactly as the grader will compute it on the corrected row.
    anchor, _why = ledger._grading_anchor({**row, "subject": new, "provider_symbol": new})
    ref_day = anchor or str(row["created_at"])[:10]
    close_new, close_old = price_on(new, ref_day), price_on(old, ref_day)
    drift_new, drift_old = _drift(close_new, row["price_at"]), _drift(close_old, row["price_at"])
    tol = ledger.REFERENCE_TOLERANCE
    reset = (state == "skipped" and str(row["outcome_note"] or "").startswith(SKIP_NOTE))

    if state == "graded":
        action, reason = LEAVE, "graded right or wrong; this tool never rewrites an outcome"
    elif drift_new is None:
        action, reason = LEAVE, (f"UNVERIFIED: no {new} close for {ref_day}"
                                 if row["price_at"] else "UNVERIFIED: no recorded price")
    elif drift_new > tol:
        action, reason = LEAVE, (f"UNVERIFIED: {new} closed {close_new:.4g} on {ref_day}, "
                                 f"recorded {row['price_at']:.4g}")
    elif drift_old is not None and drift_old <= tol:
        action, reason = LEAVE, f"AMBIGUOUS: {old} also matches the recorded price"
    else:
        action = RESTAMP_AND_RESET if reset else RESTAMP
        reason = (f"{new} closed {close_new:.4g} on {ref_day} against {row['price_at']:.4g} "
                  f"recorded; {old} " + (f"closed {close_old:.4g}" if close_old is not None
                                         else "has no close that day"))
    return {
        "id": row["id"], "subject": row["subject"], "created_at": row["created_at"],
        "horizon_days": row["horizon_days"], "resolve_after": row["resolve_after"],
        "state": state, "action": action, "reset": action == RESTAMP_AND_RESET,
        "old": {"provider_symbol": old, "identity_basis": row["identity_basis"],
                "venue": row["venue"], "market_session": row["market_session"]},
        "new": {"provider_symbol": new, "identity_basis": BASIS,
                "venue": venue, "market_session": session},
        "price_at": row["price_at"], "ref_day": ref_day,
        "close_new": close_new, "drift_new": drift_new,
        "close_old": close_old, "drift_old": drift_old,
        "reason": reason,
    }


def plan_hash(plans: list[dict], fingerprint: str) -> str:
    decisions = sorted((p["id"], p["action"], p["new"]["provider_symbol"], p["new"]["venue"],
                        p["new"]["market_session"], p["reset"]) for p in plans)
    return hashlib.sha256(json.dumps([decisions, fingerprint], default=str).encode()).hexdigest()


def build_plan(db: Path, price_on: Optional[PriceOn] = None) -> dict:
    """Read-only. Every candidate row, its decision, and the plan's hash."""
    if price_on is None:
        from src.core import ledger
        price_on = ledger._price_on
    db = Path(db)
    with _connect_ro(db) as conn:
        total = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        cands = _candidates(conn)
    plans = [plan_row(r, price_on) for r in cands]
    fp = _fingerprint(cands)
    counts: dict[str, int] = {}
    for p in plans:
        counts[p["action"]] = counts.get(p["action"], 0) + 1
    return {"database": str(db), "rows_examined": total, "candidates": len(cands),
            "counts": counts, "fingerprint": fp, "plan_hash": plan_hash(plans, fp),
            "built_at": datetime.now().isoformat(timespec="seconds"), "plans": plans}


def grader_clear(db: Path, now: Optional[datetime] = None) -> tuple[bool, str]:
    """Is it safe to write without racing the backend's grading pass?"""
    now = now or datetime.now()
    try:
        with _connect_ro(db) as conn:
            w = conn.execute("SELECT last_start, last_ok, interval_s FROM workers "
                             "WHERE name='ledger'").fetchone()
    except sqlite3.Error as e:
        return False, f"cannot read the ledger worker's heartbeat: {e}"
    if w is None or not w["last_ok"]:
        return False, "the ledger worker has no recorded pass"
    last_ok = datetime.fromisoformat(w["last_ok"])
    if w["last_start"] and datetime.fromisoformat(w["last_start"]) > last_ok:
        return False, f"a grading pass is running (started {w['last_start'][:19]})"
    next_due = last_ok + timedelta(seconds=w["interval_s"] or 1800)
    if now >= next_due - GRADER_MARGIN:
        return False, (f"the next grading pass is due about {next_due:%H:%M:%S} (last ended "
                       f"{last_ok:%H:%M:%S}); re-run right after the next pass ends")
    return True, f"last pass ended {last_ok:%H:%M:%S}, next due about {next_due:%H:%M:%S}"


def apply(db: Path, expected_hash: str, *, price_on: Optional[PriceOn] = None,
          backup_root: Optional[Path] = None, grader_stopped: bool = False,
          now: Optional[datetime] = None) -> dict:
    """The owner-approved write. Refuses (SystemExit) rather than guess."""
    db = Path(db)
    plan = build_plan(db, price_on)
    if plan["plan_hash"] != expected_hash:
        raise SystemExit(
            "REFUSING: the plan built now does not match the approved hash.\n"
            f"   approved : {expected_hash[:16]}\n   now      : {plan['plan_hash'][:16]}\n"
            "   A row changed, or a price check came out differently. Run the dry run "
            "again and review it.")
    writes = [p for p in plan["plans"] if p["action"] in WRITES]
    if not writes:
        raise SystemExit("nothing to write")
    if not grader_stopped:
        ok, why = grader_clear(db, now)
        if not ok:
            raise SystemExit(f"REFUSING: {why}")

    with _connect_ro(db) as conn:
        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if check != "ok":
        raise SystemExit(f"REFUSING: sqlite integrity_check = {check}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = Path(backup_root or db.parent / "backups") / f"ledger-restamp-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / db.name
    src_conn, dst_conn = sqlite3.connect(str(db)), sqlite3.connect(str(backup))
    try:
        src_conn.backup(dst_conn)                # WAL-safe, unlike a file copy
    finally:
        dst_conn.close()
        src_conn.close()
    (backup_dir / "plan.json").write_text(
        json.dumps(plan, indent=1, default=str), encoding="utf-8")

    ids = [p["id"] for p in plan["plans"]]
    conn = sqlite3.connect(str(db), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")          # no other writer from here to COMMIT
        marks = ",".join("?" * len(ids))
        current = [dict(r) for r in conn.execute(
            f"SELECT * FROM predictions WHERE id IN ({marks})", ids)]
        if _fingerprint(current) != plan["fingerprint"]:
            conn.execute("ROLLBACK")
            raise SystemExit("REFUSING: a planned row changed after the plan was built")
        reset_sql = (", resolved=0, resolved_at=NULL, price_end=NULL, actual_return=NULL, "
                     "correct=NULL, error=NULL, outcome_note=NULL")
        for p in writes:
            n, o = p["new"], p["old"]
            cur = conn.execute(
                "UPDATE predictions SET provider_symbol=?, identity_basis=?, venue=?, "
                "market_session=?" + (reset_sql if p["reset"] else "") +
                " WHERE id=? AND provider_symbol=? AND correct IS NULL AND resolved=?",
                (n["provider_symbol"], n["identity_basis"], n["venue"], n["market_session"],
                 p["id"], o["provider_symbol"], 0 if p["state"] == "pending" else 1))
            if cur.rowcount != 1:
                conn.execute("ROLLBACK")
                raise SystemExit(f"REFUSING: row {p['id']} was not in its planned state; "
                                 "nothing was written")
        conn.execute("COMMIT")
    finally:
        conn.close()

    # Read back: every planned row as planned; nothing outside the plan touched.
    planned = {p["id"]: p for p in writes}
    wrong, drift = [], []
    with _connect_ro(db) as live, _connect_ro(backup) as before:
        for r in live.execute(f"SELECT * FROM predictions WHERE id IN "
                              f"({','.join('?' * len(planned))})", list(planned)):
            p = planned[r["id"]]
            got = (r["provider_symbol"], r["identity_basis"], r["venue"], r["market_session"])
            want = tuple(p["new"][k] for k in ("provider_symbol", "identity_basis", "venue",
                                               "market_session"))
            if got != want or (p["reset"] and r["resolved"] != 0):
                wrong.append(r["id"])
        old_rows = {r["id"]: tuple(r) for r in before.execute(
            "SELECT * FROM predictions ORDER BY id")}
        for r in live.execute("SELECT * FROM predictions ORDER BY id"):
            if r["id"] in old_rows and r["id"] not in planned and tuple(r) != old_rows[r["id"]]:
                drift.append(r["id"])
        check_after = live.execute("PRAGMA integrity_check").fetchone()[0]
    result = {
        "written": len(writes), "reset": sum(1 for p in writes if p["reset"]),
        "left": len(plan["plans"]) - len(writes), "plan_hash": plan["plan_hash"],
        "backup": str(backup), "integrity_after": check_after,
        "rows_not_as_planned": wrong,
        "other_rows_changed_meanwhile": drift,    # another writer, never this tool
        "restore": (f"stop the ARIA Backend task, delete {db.name}-wal and {db.name}-shm "
                    f"beside {db}, then copy {backup} over {db}"),
    }
    (backup_dir / "result.json").write_text(json.dumps(result, indent=1, default=str),
                                            encoding="utf-8")
    return result


CSV_COLS = ("id", "subject", "created_at", "horizon_days", "resolve_after", "state", "action",
            "old_provider_symbol", "new_provider_symbol", "old_identity_basis",
            "new_identity_basis", "old_venue", "new_venue", "old_market_session",
            "new_market_session", "reset", "price_at", "ref_day", "close_new", "drift_new",
            "close_old", "drift_old", "reason")


def write_csv(plan: dict, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_COLS)
        for p in plan["plans"]:
            flat = {**p, **{f"old_{k}": v for k, v in p["old"].items()},
                    **{f"new_{k}": v for k, v in p["new"].items()}}
            w.writerow([flat.get(c) for c in CSV_COLS])


def print_plan(plan: dict) -> None:
    print("=" * 74)
    print("ML LEDGER RE-STAMP: DRY RUN (nothing written)")
    print("=" * 74)
    print(f"database        : {plan['database']}")
    print(f"rows examined   : {plan['rows_examined']}")
    print(f"candidates      : {plan['candidates']} ({SOURCE}, written before {FIXED_AT}, "
          "stamp != stated identity)")
    for k in (RESTAMP, RESTAMP_AND_RESET, LEAVE):
        print(f"   {k:<18} {plan['counts'].get(k, 0):>4}")
    by: dict[tuple, int] = {}
    for p in plan["plans"]:
        key = (p["old"]["provider_symbol"], p["new"]["provider_symbol"],
               p["old"]["venue"], p["new"]["venue"], p["action"])
        by[key] = by.get(key, 0) + 1
    print("\nby change:")
    for (o, n, ov, nv, a), c in sorted(by.items()):
        print(f"   {o:<8} -> {n:<6} {str(ov):<5} -> {str(nv):<5} {a:<18} {c:>3}")
    left = [p for p in plan["plans"] if p["action"] == LEAVE]
    if left:
        print("\nLEFT AS THEY ARE:")
        for p in left:
            print(f"   {p['id']} {p['subject']:<6} {p['reason']}")
    worst = max((p["drift_new"] for p in plan["plans"] if p["drift_new"] is not None),
                default=None)
    if worst is not None:
        print(f"\nlargest gap between a recorded price and the stated listing's close: "
              f"{worst:.2%}")
    print(f"\nplan hash       : {plan['plan_hash']}")
    print("NO DATA WAS MODIFIED.")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--csv", type=Path, help="write the row-by-row plan here")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--plan-hash", help="the full hash printed by the dry run")
    ap.add_argument("--grader-stopped", action="store_true",
                    help="the backend is stopped, so no grading pass can race the write")
    args = ap.parse_args(argv)
    if not args.db.exists():
        raise SystemExit(f"no database at {args.db}")
    if args.apply:
        if not args.plan_hash:
            raise SystemExit("--apply needs --plan-hash from a dry run")
        result = apply(args.db, args.plan_hash, grader_stopped=args.grader_stopped)
        print(json.dumps(result, indent=1, default=str))
        return 0 if not result["rows_not_as_planned"] and result["integrity_after"] == "ok" else 1
    plan = build_plan(args.db)
    print_plan(plan)
    if args.csv:
        write_csv(plan, args.csv)
        print(f"row-by-row plan : {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
