"""
src/intelligence/evidence.py
============================
One snapshot of everything the record supports, built off the trade path.

Evaluating every model takes seconds (it reads the whole ledger and computes
intervals, slice corrections and the promotion gate). The execution path must
stay deterministic and bounded, so it never does that work: it reads the last
snapshot (data/intelligence/evidence_snapshot.json), a dict lookup. A missing
or unreadable snapshot means reliability UNKNOWN and the DEFAULT route — which
is also the truth today.

`build(as_of=T)` is point in time: the dataset is cut at T (calls made by T,
graded by T), the owner's status decisions are read as of T, so a snapshot for
a past instant cannot see outcomes or decisions that came after it.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
SNAPSHOT_FILE = ROOT / "data" / "intelligence" / "evidence_snapshot.json"


def build(*, as_of: Optional[datetime] = None, rows: Optional[list] = None,
          regrades: Optional[dict] = None, v5_bases: Optional[dict] = None,
          market_regime: Optional[Callable] = None) -> dict:
    from src.intelligence import calibration, dataset, evaluation, registry, regrade, routing
    rows = dataset.ledger_rows() if rows is None else rows
    regrades = regrade.load() if regrades is None else regrades
    v5_bases = dataset.v5_return_bases() if v5_bases is None else v5_bases
    ds = dataset.build(rows, as_of=as_of, regrades=regrades, v5_bases=v5_bases,
                       market_regime=market_regime)
    ev = evaluation.evaluate_all(ds)
    declared = {s.ledger_source: s.declared_probability
                for s in registry.CATALOGUE.values() if s.ledger_source}
    cal = {m: calibration.status(ds.for_model(m), model_id=m,
                                 declared_probability=declared.get(m, True))
           for m in ds.models()}
    cards = registry.cards(ds, ev, cal, ledger_rows=rows, as_of=as_of)
    return {
        "as_of": as_of.isoformat() if as_of else None,
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "dataset": ds.summary(),
        "evaluations": ev,
        "calibration": cal,
        "registry": cards,
        "routing_table": routing.table(cards, ds, as_of=as_of),
        "_dataset": ds,                          # in-process only; never serialised
    }


def save(snapshot: dict) -> Path:
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in snapshot.items() if not k.startswith("_")}
    tmp = SNAPSHOT_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(clean, indent=1, default=str), encoding="utf-8")
    tmp.replace(SNAPSHOT_FILE)
    return SNAPSHOT_FILE


def load() -> Optional[dict]:
    """The last saved snapshot, or None. Never raises."""
    try:
        if SNAPSHOT_FILE.exists():
            return json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        logger.warning("evidence snapshot unreadable: %s", e)
    return None


def age_hours(snapshot: Optional[dict]) -> Optional[float]:
    if not snapshot or not snapshot.get("built_at"):
        return None
    try:
        return round((datetime.now() - datetime.fromisoformat(snapshot["built_at"]))
                     .total_seconds() / 3600, 2)
    except ValueError:
        return None


def refresh() -> dict:
    """Rebuild from the ledger and save. For a scheduled job or an endpoint —
    never called from the order path."""
    snap = build()
    save(snap)
    return {k: v for k, v in snap.items() if not k.startswith("_")}
