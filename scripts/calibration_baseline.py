"""
scripts/calibration_baseline.py
===============================
Write, or compare against, the ledger's calibration baseline.

    venv\\Scripts\\python.exe scripts\\calibration_baseline.py            # print current metrics
    venv\\Scripts\\python.exe scripts\\calibration_baseline.py --write    # freeze them as the baseline
    venv\\Scripts\\python.exe scripts\\calibration_baseline.py --compare  # out-of-sample comparison

The baseline file holds AGGREGATES only — counts, rates, intervals, Brier —
never tickers or individual predictions (data/v5/ is deliberately private).
It is committed so the number every future claim is measured against is
visible in review, not just on one machine.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASELINE = ROOT / "docs" / "baselines" / "calibration_baseline.json"


def _rows() -> list[dict]:
    from src.core import ledger
    with ledger.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM predictions").fetchall()]


def main() -> int:
    from src.core import baseline as B
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args()
    rows = _rows()

    if args.compare:
        base = json.loads(BASELINE.read_text(encoding="utf-8"))
        cur = B.measure(rows, created_after=base["cutoff"])
        print(json.dumps(B.compare(base, cur), indent=2))
        return 0

    m = B.measure(rows)
    if args.write:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        m["note"] = ("Frozen 2026-10-06 after the probability-semantics correction. "
                     "Compare only on predictions created after `cutoff`.")
        BASELINE.write_text(json.dumps(m, indent=2), encoding="utf-8")
        print(f"wrote {BASELINE}")
    print(json.dumps(m, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
