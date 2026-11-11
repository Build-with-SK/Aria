"""Audit ALL catalogue rows; optionally verify/repair explicit BSE listings.

Read-only by default. Repairs require --apply --maintenance (backend stopped).
Public listing metadata only; never scans accounts, predictions or vault notes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.provider_audit import catalogue_audit, verify_bse_candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "universe.db")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-bse", nargs="*", default=[])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--maintenance", action="store_true", help="Confirm backend is stopped; restart after repairs")
    args = parser.parse_args()
    if args.apply and (not args.maintenance or not args.verify_bse):
        parser.error("repairs require explicit symbols and --maintenance with the backend stopped")
    if args.apply and args.db.resolve() != (ROOT / "data" / "universe.db").resolve():
        parser.error("repairs must use this checkout's catalogue and associated caches")
    if args.output.resolve() == args.db.resolve():
        parser.error("output must not overwrite the catalogue")
    with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute("SELECT * FROM symbols ORDER BY symbol")]
    report = catalogue_audit(rows)
    lookup = {row["symbol"].upper(): row for row in rows}
    checks = []
    if args.apply:
        from src.data import universe
        universe.DB_PATH = args.db.resolve()
        manager = universe.UniverseManager()
    for symbol in dict.fromkeys(s.upper() for s in args.verify_bse):
        if symbol not in lookup:
            checks.append({"symbol": symbol, "verified": False, "reason": "unknown listing"})
        elif args.apply:
            checks.append(manager.repair_bse_provider(symbol))
        else:
            checks.append(verify_bse_candidate(lookup[symbol]))
    report["live_checks"] = checks
    report["live_checked"] = len(checks)
    by_symbol = {check["symbol"]: check for check in checks}
    for record in report["records"]:
        check = by_symbol.get(record["symbol"])
        if check:
            record["live_status"] = "candidate_verified" if check.get("verified") else "unverified"
            record["repair_applied"] = check.get("applied", False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "records"}, indent=2))


if __name__ == "__main__":
    main()
