"""
scripts/bench_research.py
=========================
Measured cost of the research engine, stage by stage.

    venv\\Scripts\\python.exe scripts\\bench_research.py           # fixture world, warm
    venv\\Scripts\\python.exe scripts\\bench_research.py --live    # also real vendors

Fixture mode uses tests/research_fixtures (deterministic, in memory): it
measures the computation itself. Live mode measures a complete request
against real vendors, cache bypassed, after one warm-up (vendor series are
cached by src/v5/marketdata, so later requests reuse them).

None of this runs on the order path. The order path only reads the compact
thesis saved by a live request (src/intelligence/research/engine.load_latest).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


def timed(fn, n):
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000)
    out.sort()
    return {"n": n, "p50_ms": round(statistics.median(out), 2),
            "p95_ms": round(out[min(len(out) - 1, int(round(0.95 * (len(out) - 1))))], 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--symbol", default="AAPL")
    args = ap.parse_args()

    import research_fixtures as F
    from src.intelligence.research import (analogues, attribution, cross_asset, engine,
                                           evidence_graph, news, snapshot, thesis)
    from src.intelligence.research.pit_data import visible_news
    from src.risk.market_data import FrameMarketData

    frames = F.build_frames()
    data = F.world(frames=frames)
    md = FrameMarketData(frames)
    T = F.T
    res = {}
    engine.research("AAPL", at=T, data=data)                       # warm imports
    res["snapshot_build"] = timed(lambda: snapshot.build("AAPL", T, data), 10)
    res["factor_attribution"] = timed(lambda: attribution.attribute("AAPL", T, md), 10)
    res["cross_asset"] = timed(lambda: cross_asset.analyse("AAPL", T, md, asset_class="equity",
                                                          sector="Technology"), 10)
    items = visible_news(F.news() * 10, T)                         # ~180 items
    res["news_clustering_180_items"] = timed(lambda: news.analyse(items, T, provider_status={}), 10)
    c = md.history("AAPL", T)
    b = md.history("SPY", T)
    res["historical_analogue_search"] = timed(lambda: analogues.find(c["Close"], b["Close"],
                                                                     c["Volume"]), 10)
    rep = engine.research("AAPL", at=T, data=data)
    snap = snapshot.build("AAPL", T, data)
    ev_items = evidence_graph.price_items("AAPL", c["Close"], c["Volume"], as_of=None,
                                          freshness="FRESH", technical=None)

    def thesis_only():
        ag = evidence_graph.agreement(ev_items)
        thesis.build(snap, ag, ev_items, analogues=rep["analogues"],
                     models=rep["model_predictions"], routing=rep["routing"], live=False)
    res["thesis_generation"] = timed(thesis_only, 20)
    res["complete_request_fixture"] = timed(lambda: engine.research("AAPL", at=T, data=data), 10)

    if args.live:
        engine.research(args.symbol, horizon_days=21, use_cache=False)       # warm-up
        res[f"complete_request_live_{args.symbol}"] = timed(
            lambda: engine.research(args.symbol, horizon_days=21, use_cache=False), 5)
        res[f"cache_hit_live_{args.symbol}"] = timed(
            lambda: engine.research(args.symbol, horizon_days=21), 20)
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
