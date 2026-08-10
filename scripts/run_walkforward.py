"""
scripts/run_walkforward.py
==========================
Walk-forward validate every registered research module and store the result.

    venv\\Scripts\\python.exe scripts\\run_walkforward.py
    venv\\Scripts\\python.exe scripts\\run_walkforward.py --tickers AAPL,MSFT --dates 4
    venv\\Scripts\\python.exe scripts\\run_walkforward.py --modules momentum,trend

This is a BATCH job. It replays 41 modules across several tickers and dozens of
historical dates, and each of those is a full module evaluation — expect tens of
minutes, not seconds. Nothing in the request path calls it; the API reads what
this writes to data/v5/walk_forward.json.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tickers", help="comma-separated evaluation universe")
    ap.add_argument("--modules", help="comma-separated module names (default: all)")
    ap.add_argument("--splits", type=int, default=5, help="walk-forward folds")
    ap.add_argument("--dates", type=int, default=6, help="evaluation dates per fold")
    ap.add_argument("--out", help="output path (default data/v5/walk_forward.json)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")

    from src.v5 import registry, walkforward

    universe = ([t.strip().upper() for t in args.tickers.split(",") if t.strip()]
                if args.tickers else None)
    modules = ([m.strip() for m in args.modules.split(",") if m.strip()]
               if args.modules else None)

    registry.load_modules()
    print(f"registry: {len(registry.REGISTRY)} modules")
    print(f"universe: {universe or walkforward.DEFAULT_UNIVERSE}")
    print("this will take a while - every module is replayed at every date\n")

    t0 = time.time()
    report = walkforward.run_all(universe=universe, modules=modules,
                                 n_splits=args.splits, dates_per_fold=args.dates)
    path = walkforward.save(report, Path(args.out) if args.out else None)

    # ASCII only in console output: the Windows console this runs on is cp1252,
    # and a stray arrow character crashed the script AFTER a 3-minute run.
    print(f"\nfinished in {time.time() - t0:.0f}s -> {path}")
    print(f"validated {report['modules_validated']}/{report['modules_total']} modules\n")

    rows = sorted(report["results"].values(),
                  key=lambda r: -(r.get("edge_over_base") or -9))
    print(f"{'module':<28} {'calls':>6} {'hit':>7} {'base':>7} {'edge':>7} {'IC':>7}")
    print("-" * 70)
    for r in rows:
        def f(key, pct=True):
            v = r.get(key)
            if v is None:
                return "     -"
            return f"{v:>6.1%}" if pct else f"{v:>+6.3f}"
        print(f"{r.get('module', '?'):<28} {r.get('n_calls', 0):>6} "
              f"{f('hit_rate')} {f('base_rate_up')} {f('edge_over_base')} "
              f"{f('information_coefficient', pct=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
