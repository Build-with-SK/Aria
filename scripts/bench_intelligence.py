"""
scripts/bench_intelligence.py
=============================
Measured cost of the prediction / model-intelligence path — the only honest
claim about speed is a measured one.

    venv\\Scripts\\python.exe scripts\\bench_intelligence.py

Stages:
    prediction creation    ledger.record_prediction with identity, venue and
                           session derivation (a scratch database, never the
                           real ledger)
    evaluation             canonical dataset + per-model evaluation over the
                           real ledger, read-only
    calibration            temporal train/validation/test fit on 800
                           synthetic stated probabilities
    routing                one full route() and one table lookup
    ensemble               pairwise independence over the real ledger
    portfolio handoff      conviction from a debate transcript, and the
                           conviction check inside pretrade.assess

The order path pays only for "portfolio handoff": everything else runs off it.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


def timed(fn, n=20):
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000)
    out.sort()
    return {"n": n, "p50_ms": round(statistics.median(out), 3),
            "p95_ms": round(out[int(0.95 * (n - 1))], 3), "max_ms": round(out[-1], 3)}


def main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="aria-bench-"))
    from src.intelligence import (calibration, conviction, dataset, ensemble, evaluation,
                                  evidence, regrade, routing)
    res = {}

    # Read the real ledger BEFORE pointing the process at a scratch database.
    rows = dataset.ledger_rows()
    rg = regrade.load()
    bases = dataset.v5_return_bases()

    os.environ["ARIA_CORE_DB"] = str(scratch / "bench.db")
    from src.core import bus, ledger
    bus._initialised = False
    ledger._ready = False
    i = iter(range(10 ** 6))
    res["prediction_creation"] = timed(lambda: ledger.record_prediction(
        subject="AAPL", claim="bench", direction="bull", source="bench",
        probability_kind="none", horizon_days=10, price_at=100.0,
        external_ref=f"bench-{next(i)}", announce=False), n=50)

    res["evaluation_dataset_build"] = timed(lambda: dataset.build(rows, regrades=rg,
                                                                  v5_bases=bases), n=5)
    ds = dataset.build(rows, regrades=rg, v5_bases=bases)
    evaluation.evaluate_all(ds)                       # warm imports (scipy)
    res["evaluation_all_models"] = timed(lambda: evaluation.evaluate_all(ds), n=5)

    from intel_helpers import business_days, row
    import random
    rnd = random.Random(1)
    prob_rows = []
    for k, d in enumerate(business_days("2023-01-02", 800)):
        p = 0.55 + 0.4 * ((k * 37) % 100) / 100
        ok = rnd.random() < 0.5
        prob_rows.append(row(k, source="v5", kind="stated", p_correct=p, horizon=1,
                             created=f"{d}T22:00:00", correct=int(ok), information_session=d))
    prob_ds = dataset.build(prob_rows).rows
    res["calibration_800_obs"] = timed(lambda: calibration.status(prob_ds, model_id="v5"), n=5)

    snap = evidence.build(rows=rows, regrades=rg, v5_bases=bases)
    cards = snap["registry"]
    res["routing_route"] = timed(lambda: routing.route(
        routing.RouteRequest("AAPL", 10), cards, snap["_dataset"]), n=10)
    res["routing_lookup"] = timed(lambda: routing.lookup(snap["routing_table"], "equity", 10),
                                  n=200)
    res["ensemble_independence"] = timed(lambda: ensemble.independence(rows, ds), n=5)

    T = datetime.now(timezone.utc)
    transcript = {"id": "dbt-bench000", "ticker": "AAPL", "llm": "bench",
                  "at": (T - timedelta(hours=1)).astimezone().replace(tzinfo=None).isoformat(),
                  "opinions": [{"agent": "technical", "view": "bull", "conviction": 70}],
                  "judge": {"view": "bull", "conviction": 70, "conviction_bar": 65,
                            "verdict": "BUY", "regime": "x"}}
    clean = json.loads(json.dumps({k: v for k, v in snap.items() if not k.startswith("_")},
                                  default=str))
    res["portfolio_handoff_conviction_build"] = timed(lambda: conviction.from_debate(
        transcript, as_of=T, snapshot=clean, signals_age_hours=2.0).to_dict(), n=200)

    from test_portfolio_risk import LOOSE, T as PT_T, acct, build_frames, order
    from src.risk import pretrade
    from src.risk.market_data import FrameMarketData
    frames = build_frames()
    md = FrameMarketData(frames)
    c = conviction.from_debate(dict(transcript, at=(PT_T - timedelta(hours=1)).astimezone()
                                    .replace(tzinfo=None).isoformat()),
                               as_of=PT_T, snapshot=clean, signals_age_hours=2.0).to_dict()
    o_plain = order(frames, "AAPL", 10)
    o_conv = order(frames, "AAPL", 10, conviction=c)
    a = acct()
    res["pretrade_without_conviction"] = timed(lambda: pretrade.assess(
        o_plain, a, at=PT_T, limits=LOOSE, data=md), n=30)
    res["pretrade_with_conviction"] = timed(lambda: pretrade.assess(
        o_conv, a, at=PT_T, limits=LOOSE, data=md), n=30)
    res["measured_at"] = datetime.now().isoformat(timespec="seconds")
    res["ledger_rows"] = len(rows)
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
