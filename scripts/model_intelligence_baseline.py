"""
scripts/model_intelligence_baseline.py
======================================
Measure the prediction / model-intelligence baseline, and (optionally) freeze it.

    venv\\Scripts\\python.exe scripts\\model_intelligence_baseline.py              # measure + print
    venv\\Scripts\\python.exe scripts\\model_intelligence_baseline.py --write      # also freeze the
                                                                                 # aggregates in docs/baselines
    venv\\Scripts\\python.exe scripts\\model_intelligence_baseline.py --offline    # use cached regrades,
                                                                                 # skip debate grading

What it runs, in order: the data-integrity audit of the ledger (Phase 1); the
same-series regrade of defective historical grades (a VIEW: nothing is
written to the ledger); the canonical dataset; per-model evaluation;
calibration status; the registry and its promotion gate; the routing table;
ensemble independence; the debate-desk evaluation; the regime audit.

Outputs:
    data/intelligence/regrades.json            the re-measurements (private: ids)
    data/intelligence/evidence_snapshot.json   what the order path reads
    data/intelligence/debate_eval.json         the desk evaluation
    docs/baselines/model_intelligence_baseline.json   AGGREGATES only, with --write

The ledger is opened read-only by everything here.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASELINE = ROOT / "docs" / "baselines" / "model_intelligence_baseline.json"


def _strip(obj):
    """Aggregates only: drop anything keyed by an identifier."""
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items()
                if k != "_dataset" and not (k in ("excluded", "rows") and isinstance(v, list))}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    from src.intelligence import (baseline, dataset, debate_eval, ensemble, evidence,
                                  regime, regrade)
    t0 = time.perf_counter()
    timings = {}
    rows = dataset.ledger_rows()
    timings["read_ledger_s"] = round(time.perf_counter() - t0, 3)

    t = time.perf_counter()
    audit = baseline.audit(rows)
    timings["audit_s"] = round(time.perf_counter() - t, 3)

    v5_bases = dataset.v5_return_bases()
    t = time.perf_counter()
    if args.offline:
        rg = regrade.load()
    else:
        rg = regrade.regrade(rows, v5_bases=v5_bases)
        regrade.save(rg)
    timings["regrade_s"] = round(time.perf_counter() - t, 3)

    t = time.perf_counter()
    labeler = None if args.offline else regime.benchmark_labeler("SPY")
    snap = evidence.build(rows=rows, regrades=rg, v5_bases=v5_bases, market_regime=labeler)
    evidence.save(snap)
    timings["evidence_s"] = round(time.perf_counter() - t, 3)
    ds = snap["_dataset"]

    t = time.perf_counter()
    independence = ensemble.independence(rows, ds)
    timings["ensemble_s"] = round(time.perf_counter() - t, 3)

    t = time.perf_counter()
    debate = debate_eval.run(None if args.offline else debate_eval.vendor_closes)
    debate_eval.save(debate)
    timings["debate_s"] = round(time.perf_counter() - t, 3)

    macro = regime.audit_macro(rows)
    out = {
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "note": ("Aggregates only. Regrades are a same-series VIEW of defective historical "
                 "grades; the ledger itself is unchanged."),
        "data_integrity": audit,
        "regrade": {"needed": len(rg),
                    "remeasured": sum(1 for v in rg.values() if v.get("correct") is not None),
                    "not_remeasurable": sum(1 for v in rg.values() if v.get("correct") is None),
                    "outcome_changed": snap["dataset"]["regrade"].get("outcome_changed")},
        "dataset": snap["dataset"],
        "evaluations": snap["evaluations"],
        "calibration": snap["calibration"],
        "registry": snap["registry"],
        "routing_table": snap["routing_table"],
        "ensemble": independence,
        "debate_desk": debate,
        "macro_regime_audit": macro,
        "timings": timings,
    }
    out = _strip(out)
    if args.write:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
        print(f"wrote {BASELINE}")
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
