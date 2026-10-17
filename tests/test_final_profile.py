"""Deterministic synthetic timing sample; no claim about production throughput."""
import json
import os
import statistics
import time
from pathlib import Path

import numpy as np
import pandas as pd


def test_scanner_and_queue_profile(tmp_path, monkeypatch):
    import main
    from src.execution import approval_queue as aq
    from src.execution.broker_base import OrderRequest, OrderSide

    rng = np.random.default_rng(17)
    dates = pd.bdate_range("2024-01-01", periods=400)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 400)))
    bars = pd.DataFrame({"Open": close, "Close": close, "High": close * 1.02,
                         "Low": close * 0.98, "Volume": 1e6}, index=dates)
    inputs = {f"FIXTURE{i}": bars.copy() for i in range(25)}
    timings, outputs = [], []
    for _ in range(5):
        start = time.perf_counter()
        outputs.append(main.compute_signal_scores(main.compute_features(inputs, {}), {}))
        timings.append((time.perf_counter() - start) * 1000)
    assert len(outputs[0]) == 25 and all(row == outputs[0] for row in outputs)
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "queue.json")
    trade = aq.ApprovalQueue().push(OrderRequest("AAPL", OrderSide.BUY, 1))
    row = aq._load_queue()[0]
    with aq._queue_guard():
        aq._save_queue([{**row, "id": f"fixture-{i}"} for i in range(1000)])
    reads = []
    for _ in range(10):
        start = time.perf_counter()
        assert aq.ApprovalQueue().get_by_id("fixture-999").ticker == trade.ticker
        reads.append((time.perf_counter() - start) * 1000)
    report = {"scope": "synthetic, local test harness; no production benchmark",
              "scanner": {"symbols": 25, "bars_per_symbol": 400, "repeats": 5,
                          "median_ms": statistics.median(timings), "max_ms": max(timings)},
              "queue_lookup": {"records": 1000, "repeats": 10,
                               "median_ms": statistics.median(reads), "max_ms": max(reads)}}
    if os.environ.get("ARIA_PROFILE_OUT"):
        Path(os.environ["ARIA_PROFILE_OUT"]).write_text(json.dumps(report, indent=2), encoding="utf-8")
