"""
scripts/repair_universe_identity.py
===================================
Re-file symbol-master rows whose venue and asset class contradict their own
provider symbol.

WHAT IT REPAIRS
---------------
`Universe._resolution_candidates()` used to funnel every provider symbol that
was not `.NS`, `.BO`, `.L` or `-USD` into a final `else` reading
`(q, q, "US", "equity")`. So every FX cross, listed future and index that a
live `resolve()` touched was written into the symbol master as a US EQUITY:

    GBPINR=X   GBP/INR   exchange=US   asset_class=equity   currency=NULL

That is not a judgement call that went the wrong way. `=X` means an FX cross,
`=F` a future, `^` an index level — the provider assigns those suffixes, and
`src/core/identity.classify_provider_symbol()` reads them. The classification
was always derivable; nothing asked.

WHY THIS IS SAFE TO RUN
-----------------------
It changes only `exchange`, `asset_class` and `currency`, and only where the
provider symbol's own shape contradicts what is stored. It never touches
`symbol`, `yahoo`, `name` or `isin` — nothing that establishes WHICH security a
row is. A row whose shape says nothing is left exactly as it is.

It cannot create the defect it repairs, and the reason is structural rather
than a check: `symbol`, `yahoo` and `name` are not in `REPAIRABLE`, so no write
here can point a provider symbol at a second company. There is deliberately no
call to `assert_provider_symbol_safe()` — that guard answers "may this
(display -> provider) mapping be written?", and this script never writes one.
(An earlier version of this docstring claimed it did call the guard. It never
did; the claim was wrong and is corrected here rather than quietly deleted.)

USAGE
-----
    python scripts/repair_universe_identity.py            # dry run
    python scripts/repair_universe_identity.py --apply    # write, after backup
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.identity import classify_provider_symbol       # noqa: E402

UNIVERSE_DB = ROOT / "data" / "universe.db"

#: Only these columns may be rewritten. Listed rather than implied, because the
#: whole failure mode being repaired was a writer that touched a column it had
#: no business touching.
REPAIRABLE = ("exchange", "asset_class", "currency")


def plan() -> list[dict]:
    """Every row whose stored venue/class/currency contradicts its own symbol."""
    with sqlite3.connect(str(UNIVERSE_DB)) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT symbol, yahoo, name, exchange, asset_class, currency "
            "FROM symbols")]

    out: list[dict] = []
    for r in rows:
        shape = classify_provider_symbol(r["yahoo"] or r["symbol"])
        if not shape["exchange"]:
            continue                       # the shape says nothing; leave it alone
        # A bare US ticker is the DEFAULT assumption, not evidence. Re-filing a
        # row onto US on the strength of "it has no suffix" would overwrite the
        # NSE/BSE/LSE loaders' authoritative CSV data with a guess.
        if shape["exchange"] == "US":
            continue
        changes = {}
        if (r["exchange"] or "") != shape["exchange"]:
            changes["exchange"] = (r["exchange"], shape["exchange"])
        # Asset class is only repaired where the SUFFIX determines it. `=X`,
        # `=F`, `-USD` and `^` each name exactly one asset class. `.L` does not:
        # the London exchange lists equities, ETFs, investment trusts and bonds
        # alike, so re-filing every `.L` row as 'equity' would be the same kind
        # of guess this script exists to undo — including on `EDV.L`, whose
        # stored 'fixed_income' is contamination from the Vanguard ETF that
        # shares its ticker, but which nothing here can prove.
        if shape["asset_class"] in ("forex", "futures", "crypto", "index") \
                and (r["asset_class"] or "") != shape["asset_class"]:
            changes["asset_class"] = (r["asset_class"], shape["asset_class"])
        # Currency is FILLED IN, never rewritten.
        #
        # The canonical model is `stored currency = GBP, price_unit = GBp,
        # unit_multiplier = 0.01`: London rows deliberately store the MAJOR unit
        # and the minor unit is derived at read time by identity.describe() and
        # currency.native_currency(). Rewriting 2,827 London rows from GBP to
        # GBp here would not fix the 100x defect — it would bake it into the
        # master, because every reader that already divides by 100 would then do
        # it twice. So a stored currency is left alone and only a MISSING one is
        # supplied.
        if changes and shape["currency"] and not (r["currency"] or "").strip():
            changes["currency"] = (r["currency"], shape["currency"])
        # Only rows that are actually MIS-FILED. A `.NS` row with a NULL
        # currency is not mis-filed — `native_currency()` resolves `.NS` by
        # suffix before it ever reaches the database — and rewriting 2,600 of
        # them would be a large blind write dressed up as a repair. The defect
        # is the venue and the asset class; that is what this repairs.
        if changes and ("exchange" in changes or "asset_class" in changes):
            out.append({"symbol": r["symbol"], "provider_symbol": r["yahoo"],
                        "name": r["name"], "basis": shape["basis"],
                        "changes": changes})
    return out


def apply(rows: list[dict]) -> dict:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = ROOT / "data" / "backups" / f"universe-repair-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(UNIVERSE_DB, backup_dir / UNIVERSE_DB.name)
    (backup_dir / "plan.json").write_text(
        json.dumps(rows, indent=1, default=str), encoding="utf-8")

    with sqlite3.connect(str(UNIVERSE_DB)) as conn:
        before = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        written = 0
        for r in rows:
            sets, args = [], []
            for col, (_old, new) in r["changes"].items():
                assert col in REPAIRABLE, col
                sets.append(f"{col}=?")
                args.append(new)
            args.append(r["symbol"])
            written += conn.execute(
                f"UPDATE symbols SET {', '.join(sets)} WHERE symbol=?", args).rowcount
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]

    return {"written": written, "rows_before": before, "rows_after": after,
            "row_count_unchanged": before == after,
            "backup": str(backup_dir),
            "restore": f'copy "{backup_dir / UNIVERSE_DB.name}" "{UNIVERSE_DB}"'}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    rows = plan()
    print("=" * 74)
    print("UNIVERSE IDENTITY REPAIR - venue/class/currency vs provider symbol")
    print("=" * 74)
    print(f"database         : {UNIVERSE_DB}")
    print(f"rows to re-file  : {len(rows)}")
    print()
    for r in rows[:40]:
        diff = "  ".join(f"{c}: {o!r} -> {n!r}" for c, (o, n) in r["changes"].items())
        print(f"  {r['provider_symbol']:<14} {diff}")
        print(f"      {r['basis']}")
    if len(rows) > 40:
        print(f"  ... and {len(rows) - 40} more")
    print()

    if not args.apply:
        print("DRY RUN - nothing written. Re-run with --apply.")
        return 0
    if not rows:
        print("Nothing to repair.")
        return 0

    result = apply(rows)
    print("APPLIED")
    for k in ("written", "rows_before", "rows_after", "row_count_unchanged", "backup"):
        print(f"   {k:<20}: {result[k]}")
    print(f"   restore             : {result['restore']}")
    return 0 if result["row_count_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
