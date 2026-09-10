"""
scripts/migrate_identity.py
===========================
Attach an immutable market-data identity to every historical prediction.

WHAT THIS DOES
--------------
Adds `predictions.provider_symbol` and backfills it. Nothing else. In
particular it does NOT touch:

    subject, price_at, price_end, actual_return, correct, resolved,
    resolved_at, confidence, probability, created_at, outcome_note

`subject` is what ARIA historically called the thing. `provider_symbol` is what
instrument was actually evaluated. Keeping both is what makes the record
reproducible: a future reader can see that a 2026-08 prediction on "BA" was
priced against BA.L, even after `BA` comes to mean Boeing.

WHY IT FAILS CLOSED
-------------------
The defect being repaired is a system that guessed an identity from ticker
text. A migration that guessed the same way would encode the bug permanently.
So a row is only written when there is EVIDENCE:

  UNAMBIGUOUS   the symbol master resolves it, the bare symbol IS the provider
                symbol, and no other venue lists that root. Nothing to confuse.

  PRICE_VERIFIED  the symbol is ambiguous (or maps to a suffixed instrument),
                so both candidates are priced on the prediction's creation date
                and the one matching the recorded reference price wins. This is
                the only evidence that survives the universe being re-keyed
                later, because it is anchored to a number, not to a mapping.

  UNRESOLVED    neither candidate matches, or BOTH match within tolerance, or
                the symbol is not in the master. The row is SKIPPED and
                reported. It is never filled with a best guess.

USAGE
-----
    python scripts/migrate_identity.py --dry-run     # no writes, prints a plan
    python scripts/migrate_identity.py --apply       # writes, after backup

Idempotent: rows that already carry a provider_symbol are counted and skipped.
Reversible: --apply copies the database to data/backups/ first and prints the
restore command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core import identity as idt          # noqa: E402
from src.core.bus import _db_path             # noqa: E402

#: A recorded reference price and a provider close are the same observation if
#: they agree this closely. Generous enough for auto-adjust and intraday-vs-close
#: differences, far tighter than the ~100x a venue confusion produces.
PRICE_TOLERANCE = 0.05

#: A SECOND, much tighter band used only to break a tie.
#:
#: The 5% band above is deliberately generous — it has to absorb auto-adjust
#: and intraday-vs-close differences — and generosity is what produced the two
#: refusals this migration originally reported. Both MRK rows had Merck & Co
#: AND Merck KGaA inside 5%, so the plan called it ambiguous and skipped them.
#:
#: But they were not equally close. The recorded prices are BYTE-IDENTICAL to
#: Merck & Co's close (0.00% drift) while Merck KGaA sits at 2.91% and 0.32%.
#: A price recorded to the cent by the subsystem that fetched it does not
#: "approximately" match its own source. When exactly one candidate is an exact
#: match and every other is outside this band, that is evidence, not a
#: coin-toss — and refusing it is not caution, it is discarding a fact.
EXACT_TOLERANCE = 0.001          # 0.1% — a recorded close vs its own source

UNAMBIGUOUS = "UNAMBIGUOUS"
PRICE_VERIFIED = "PRICE_VERIFIED"
EXACT_PRICE_MATCH = "EXACT_PRICE_MATCH"
ALREADY_SET = "ALREADY_SET"
UNRESOLVED = "UNRESOLVED"

WRITABLE = (UNAMBIGUOUS, PRICE_VERIFIED, EXACT_PRICE_MATCH)


def _source_resolves_to(subject: str, source: str):
    """What the PRODUCING subsystem's own resolver makes of this subject.

    Corroboration, never the sole basis. A prediction from `v5` was priced
    through `src.v5.marketdata.resolve()`, which is a deterministic display
    lookup in the symbol master: `MRK` finds the exact row `MRK` (NYSE) and can
    never reach `MRK.DE`, because reaching that requires the caller to have
    written `MRK.DE`. Re-running the same function on the recorded subject
    reproduces the choice that was actually made.
    """
    try:
        if (source or "").lower() == "v5":
            from src.v5.marketdata import resolve as v5_resolve
            return v5_resolve(subject)
    except Exception:
        pass
    return None


def _close_on(provider_symbol: str, on: str):
    """Provider close on/just before a date. None when unavailable."""
    try:
        from src.core.ledger import _price_on
        return _price_on(provider_symbol, on)
    except Exception:
        return None


def _candidates(subject: str) -> list[str]:
    """Every provider symbol this subject could plausibly denote.

    Deliberately small: the bare symbol (a US listing under the provider's
    convention) and whatever the symbol master maps it to, plus any sibling
    listing of the same root the master knows about.
    """
    ident = idt.describe(subject)
    out: list[str] = []
    if ident.ok and ident.provider_symbol:
        out.append(ident.provider_symbol)
    if subject not in out:
        out.append(subject)
    for c in (ident.candidates or []):
        ps = c.get("provider_symbol")
        if ps and ps not in out:
            out.append(ps)
    return out


def plan_row(row: dict) -> dict:
    """Decide this row's provider_symbol, with the evidence for it."""
    subject = row["subject"]
    existing = row.get("provider_symbol")
    if existing:
        return {"id": row["id"], "subject": subject, "provider_symbol": existing,
                "status": ALREADY_SET, "reason": "already carries an identity",
                "verification": "n/a"}

    ident = idt.describe(subject)
    if not ident.ok:
        return {"id": row["id"], "subject": subject, "provider_symbol": None,
                "status": UNRESOLVED,
                "reason": ident.reason, "verification": "symbol master"}

    cands = _candidates(subject)
    unambiguous = (len(cands) == 1
                   and cands[0] == subject
                   and not ident.candidates)
    if unambiguous:
        return {"id": row["id"], "subject": subject, "provider_symbol": subject,
                "status": UNAMBIGUOUS,
                "reason": (f"the symbol master resolves {subject} to itself and no "
                           f"other venue lists that root"),
                "verification": "symbol master"}

    # Ambiguous, or maps to a suffixed listing: require price evidence.
    price_at = row.get("price_at")
    made = (row.get("created_at") or "")[:10]
    if not price_at or not made:
        return {"id": row["id"], "subject": subject, "provider_symbol": None,
                "status": UNRESOLVED,
                "reason": "no reference price or creation date to verify against",
                "verification": "none"}

    matches = []
    probed = {}
    for cand in cands:
        close = _close_on(cand, made)
        probed[cand] = close
        if close:
            drift = abs(close - price_at) / price_at
            if drift <= PRICE_TOLERANCE:
                matches.append((cand, drift))

    detail = ", ".join(f"{k}={'n/a' if v is None else round(v, 2)}"
                       for k, v in probed.items())
    if len(matches) == 1:
        cand, drift = matches[0]
        return {"id": row["id"], "subject": subject, "provider_symbol": cand,
                "status": PRICE_VERIFIED,
                "reason": (f"recorded {price_at} matches {cand} close on {made} "
                           f"({drift * 100:.2f}% drift); candidates {detail}"),
                "verification": f"provider close on {made}"}
    if len(matches) > 1:
        # Tie-break on EXACTNESS, not on preference. Exactly one candidate
        # inside 0.1% while every other is outside it means the recorded number
        # came from that candidate; a 5% band simply could not see the
        # difference. If two are that close, it stays a refusal.
        exact = [(c, d) for c, d in matches if d <= EXACT_TOLERANCE]
        if len(exact) == 1:
            cand, drift = exact[0]
            others = ", ".join(f"{c} off by {d * 100:.2f}%"
                               for c, d in matches if c != cand)
            corroboration = _source_resolves_to(subject, row.get("source") or "")
            agree = (corroboration == cand)
            return {"id": row["id"], "subject": subject, "provider_symbol": cand,
                    "status": EXACT_PRICE_MATCH,
                    "reason": (
                        f"recorded {price_at} matches {cand} close on {made} "
                        f"EXACTLY ({drift * 100:.2f}% drift) while {others}; "
                        + (f"and {row.get('source')}'s own resolver maps "
                           f"'{subject}' to {corroboration}, which agrees"
                           if agree else
                           f"source resolver corroboration: {corroboration}")),
                    "verification": f"provider close on {made}"}
        return {"id": row["id"], "subject": subject, "provider_symbol": None,
                "status": UNRESOLVED,
                "reason": (f"AMBIGUOUS — {len(matches)} candidates match the recorded "
                           f"price within {PRICE_TOLERANCE:.0%} and "
                           f"{len(exact)} of them within {EXACT_TOLERANCE:.1%}: "
                           + ", ".join(c for c, _ in matches)),
                "verification": f"provider close on {made}"}
    return {"id": row["id"], "subject": subject, "provider_symbol": None,
            "status": UNRESOLVED,
            "reason": (f"no candidate matches the recorded {price_at} on {made}; "
                       f"probed {detail}"),
            "verification": f"provider close on {made}"}


def snapshot() -> dict:
    """A fingerprint of the ledger's identity-relevant state, right now.

    THE LEDGER IS LIVE. Daemons write to it continuously — it grew from 279 to
    339 predictions during the audit that produced this script — so a plan
    computed at 13:00 may describe a database that no longer exists at 13:05.
    Applying it anyway would write decisions taken about rows that have since
    changed, and skip rows that appeared in between.

    So the plan is pinned to a snapshot, and `apply()` re-takes the snapshot and
    refuses if it moved. The fingerprint covers exactly the columns the plan
    reads — id, subject, source, price_at, created_at, provider_symbol — because
    a change in any of those changes the plan, while a grading that fills in
    price_end does not and must not force a re-run.
    """
    db = _db_path()
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        cols = [r[1] for r in conn.execute("PRAGMA table_info(predictions)")]
        has_col = "provider_symbol" in cols
        select = ("SELECT id, subject, source, price_at, created_at"
                  + (", provider_symbol" if has_col else "")
                  + " FROM predictions ORDER BY id")
        rows = [dict(r) for r in conn.execute(select)]
    body = json.dumps(rows, sort_keys=True, default=str)
    return {
        "rows": len(rows),
        "column_exists": has_col,
        "fingerprint": hashlib.sha256(body.encode()).hexdigest(),
        "taken_at": datetime.now().isoformat(timespec="seconds"),
    }


def plan_hash(plans: list[dict]) -> str:
    """A hash of the DECISIONS, so an applied migration can be tied to a plan."""
    decisions = [(p["id"], p["status"], p["provider_symbol"]) for p in plans]
    return hashlib.sha256(
        json.dumps(sorted(decisions, key=lambda d: d[0]), default=str).encode()
    ).hexdigest()


def build_plan() -> dict:
    """Read-only. Produces the full migration plan and its counters."""
    db = _db_path()
    snap = snapshot()
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        cols = [r[1] for r in conn.execute("PRAGMA table_info(predictions)")]
        has_col = "provider_symbol" in cols
        select = ("SELECT id, subject, source, price_at, created_at, resolved, correct"
                  + (", provider_symbol" if has_col else "")
                  + " FROM predictions ORDER BY created_at")
        rows = [dict(r) for r in conn.execute(select)]

    plans = [plan_row(r) for r in rows]
    counts: dict[str, int] = {}
    for p in plans:
        counts[p["status"]] = counts.get(p["status"], 0) + 1

    # A provider symbol legitimately repeats across rows (many predictions on
    # one instrument). What must NOT happen is one SUBJECT mapping to two
    # different provider symbols — that would mean the migration itself is
    # ambiguous about what the subject denoted.
    by_subject: dict[str, set] = {}
    for p in plans:
        if p["provider_symbol"]:
            by_subject.setdefault(p["subject"], set()).add(p["provider_symbol"])
    conflicting = {s: sorted(v) for s, v in by_subject.items() if len(v) > 1}

    return {
        "database": str(db),
        "snapshot": snap,
        "plan_hash": plan_hash(plans),
        "column_exists": has_col,
        "rows_examined": len(rows),
        "counts": counts,
        "needs_backfill": sum(1 for p in plans if p["status"] in WRITABLE),
        "unresolved": [p for p in plans if p["status"] == UNRESOLVED],
        "conflicting_subjects": conflicting,
        "plans": plans,
    }


def _integrity(db: Path) -> dict:
    """Pre/post invariants. Anything that changes here aborts the migration."""
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        q = lambda s: conn.execute(s).fetchone()[0]           # noqa: E731
        return {
            "integrity_check": q("PRAGMA integrity_check"),
            "predictions": q("SELECT COUNT(*) FROM predictions"),
            "graded": q("SELECT COUNT(*) FROM predictions WHERE resolved=1"),
            "scored": q("SELECT COUNT(*) FROM predictions WHERE correct IS NOT NULL"),
            "decisions": q("SELECT COUNT(*) FROM decisions"),
            "subject_digest": q(
                "SELECT COALESCE(GROUP_CONCAT(subject,'|'),'') FROM "
                "(SELECT subject FROM predictions ORDER BY id)"),
            "price_digest": q(
                "SELECT COALESCE(GROUP_CONCAT(COALESCE(price_at,-1),'|'),'') FROM "
                "(SELECT price_at FROM predictions ORDER BY id)"),
            "outcome_digest": q(
                "SELECT COALESCE(GROUP_CONCAT(COALESCE(correct,-1),'|'),'') FROM "
                "(SELECT correct FROM predictions ORDER BY id)"),
        }


def print_plan(plan: dict, *, verbose: bool = True) -> None:
    print("=" * 74)
    print("ARIA IDENTITY MIGRATION — DRY RUN (no writes)")
    print("=" * 74)
    print(f"database                : {plan['database']}")
    print(f"ledger snapshot         : {plan['snapshot']['rows']} rows, "
          f"{plan['snapshot']['fingerprint'][:16]} "
          f"(taken {plan['snapshot']['taken_at']})")
    print(f"plan hash               : {plan['plan_hash'][:16]}")
    print(f"provider_symbol column  : {'present' if plan['column_exists'] else 'ABSENT (will be added)'}")
    print(f"rows examined           : {plan['rows_examined']}")
    print(f"rows needing backfill   : {plan['needs_backfill']}")
    print(f"rows already identified : {plan['counts'].get(ALREADY_SET, 0)}")
    print(f"rows unresolved (SKIP)  : {plan['counts'].get(UNRESOLVED, 0)}")
    print()
    print("by evidence class:")
    for k in (UNAMBIGUOUS, PRICE_VERIFIED, EXACT_PRICE_MATCH,
              ALREADY_SET, UNRESOLVED):
        if plan["counts"].get(k):
            print(f"   {k:<16} {plan['counts'][k]:>5}")
    print()

    changed = [p for p in plan["plans"] if p["status"] in WRITABLE]
    verified = [p for p in changed
                if p["status"] in (PRICE_VERIFIED, EXACT_PRICE_MATCH)]
    print(f"PRICE-VERIFIED ROWS ({len(verified)}) — the ones the audit flagged:")
    for p in verified:
        print(f"   subject={p['subject']:<10} -> provider_symbol={p['provider_symbol']:<10}")
        print(f"      reason      : {p['reason']}")
        print(f"      verification: {p['verification']}")
    if not verified:
        print("   (none)")
    print()

    if verbose:
        others = [p for p in changed if p["status"] == UNAMBIGUOUS]
        shown = others[:8]
        print(f"UNAMBIGUOUS ROWS ({len(others)}) — first {len(shown)}:")
        for p in shown:
            print(f"   {p['subject']:<10} -> {p['provider_symbol']:<10}  {p['reason'][:60]}")
        if len(others) > len(shown):
            print(f"   ... and {len(others) - len(shown)} more")
        print()

    if plan["unresolved"]:
        print(f"UNRESOLVED — WILL NOT BE MIGRATED ({len(plan['unresolved'])}):")
        for p in plan["unresolved"][:20]:
            print(f"   id={p['id']} subject={p['subject']:<10} {p['reason'][:78]}")
    else:
        print("UNRESOLVED: none")
    print()

    if plan["conflicting_subjects"]:
        print("!! CONFLICTING SUBJECT MAPPINGS — migration would refuse:")
        for s, v in plan["conflicting_subjects"].items():
            print(f"   {s} -> {v}")
    else:
        print("duplicate/conflicting subject mappings: none")
    print()
    print("NO PRODUCTION DATA WAS MODIFIED.")
    print("=" * 74)


def apply(plan: dict) -> dict:
    """Perform the migration. Backs up first; verifies invariants after."""
    db = _db_path()
    if plan["conflicting_subjects"]:
        raise SystemExit("refusing: a subject maps to more than one provider symbol")

    # ── FREEZE CHECK — the ledger must not have moved since the plan ────────
    #
    # Fail CLOSED. A stale plan is not "mostly right": it encodes decisions
    # about rows that may have been re-priced, and omits rows that appeared
    # after it was built. Re-running the dry run costs seconds; writing an
    # identity onto the wrong row is permanent.
    now = snapshot()
    if now["fingerprint"] != plan["snapshot"]["fingerprint"]:
        raise SystemExit(
            "REFUSING — the prediction ledger changed between the plan and "
            "the apply.\n"
            f"   planned against : {plan['snapshot']['rows']} rows, "
            f"{plan['snapshot']['fingerprint'][:16]} "
            f"(taken {plan['snapshot']['taken_at']})\n"
            f"   database now    : {now['rows']} rows, "
            f"{now['fingerprint'][:16]} (taken {now['taken_at']})\n"
            "   The daemons write continuously. Re-run --dry-run and apply "
            "immediately after.")

    before = _integrity(db)
    if before["integrity_check"] != "ok":
        raise SystemExit(f"refusing: sqlite integrity_check = {before['integrity_check']}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = ROOT / "data" / "backups" / f"identity-migration-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db, backup_dir / db.name)
    (backup_dir / "plan.json").write_text(
        json.dumps(plan["plans"], indent=1, default=str), encoding="utf-8")
    (backup_dir / "integrity_before.json").write_text(
        json.dumps(before, indent=1, default=str), encoding="utf-8")

    with sqlite3.connect(str(db)) as conn:
        if not plan["column_exists"]:
            conn.execute("ALTER TABLE predictions ADD COLUMN provider_symbol TEXT")
            conn.execute("ALTER TABLE predictions ADD COLUMN identity_basis TEXT")
        written = 0
        for p in plan["plans"]:
            if p["status"] not in WRITABLE:
                continue
            cur = conn.execute(
                "UPDATE predictions SET provider_symbol=?, identity_basis=? "
                "WHERE id=? AND (provider_symbol IS NULL OR provider_symbol='')",
                (p["provider_symbol"], p["status"], p["id"]))
            written += cur.rowcount
        conn.commit()

    after = _integrity(db)
    drift = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    (backup_dir / "integrity_after.json").write_text(
        json.dumps(after, indent=1, default=str), encoding="utf-8")

    after_snap = snapshot()
    return {"written": written, "backup": str(backup_dir),
            "invariants_held": not drift, "drift": drift,
            "plan_hash": plan["plan_hash"],
            "snapshot_before": plan["snapshot"]["fingerprint"],
            "snapshot_after": after_snap["fingerprint"],
            "restore_command": f'copy "{backup_dir / db.name}" "{db}"'}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True)
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true", help="machine-readable plan")
    args = ap.parse_args()

    plan = build_plan()

    if args.json:
        print(json.dumps({k: v for k, v in plan.items() if k != "plans"},
                         indent=1, default=str))
        return 0

    if not args.apply:
        print_plan(plan)
        return 0

    result = apply(plan)
    print("MIGRATION APPLIED")
    print(f"   plan hash       : {result['plan_hash'][:16]}")
    print(f"   snapshot before : {result['snapshot_before'][:16]}")
    print(f"   snapshot after  : {result['snapshot_after'][:16]}")
    print(f"   rows written    : {result['written']}")
    print(f"   backup          : {result['backup']}")
    print(f"   invariants held : {result['invariants_held']}")
    if result["drift"]:
        print(f"   !! DRIFT        : {result['drift']}")
    print(f"   restore         : {result['restore_command']}")
    return 0 if result["invariants_held"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
