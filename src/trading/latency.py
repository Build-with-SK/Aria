"""
src/trading/latency.py
======================
How fast this system actually is — measured, not claimed.

Six stages, each timed by the pipeline with a monotonic clock:

    market_data        fetching the quote
    decision           risk engine
    order_generation   building the order and validating it
    broker_ack         submitting and receiving the acknowledgement
    fill               submission to confirmed fill (when it fills at once)
    total              signal in to acknowledgement out

WHAT THIS SYSTEM IS
-------------------
`classify()` names the system from the measured numbers, and HFT is not one of
the options it can return. High-frequency trading means co-located hardware,
kernel-bypass networking and decisions in microseconds; a Python process on a
Mac mini reaching a retail broker over the public internet is, at best,
low-latency systematic trading — and more often plain systematic trading. The
label follows the measurement.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
LOG = ROOT / "data" / "trading" / "latency.jsonl"

STAGES = ("market_data", "decision", "order_generation", "broker_ack", "fill", "total")


def record(timings_ms: dict, *, broker: str, outcome: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
           "broker": broker, "outcome": outcome,
           **{k: round(v, 3) for k, v in timings_ms.items() if v is not None}}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _rows(broker: str | None = None, last: int = 1000) -> list[dict]:
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text(encoding="utf-8").splitlines()[-last:]:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if broker is None or r.get("broker") == broker:
            out.append(r)
    return out


def summary(broker: str | None = None) -> dict:
    rows = _rows(broker)
    out = {"samples": len(rows), "broker": broker or "all", "stages": {}}
    for st in STAGES:
        vals = sorted(r[st] for r in rows if isinstance(r.get(st), (int, float)))
        if not vals:
            out["stages"][st] = None
            continue
        out["stages"][st] = {
            "n": len(vals), "p50_ms": round(statistics.median(vals), 3),
            "p95_ms": round(vals[min(len(vals) - 1, int(0.95 * (len(vals) - 1)))], 3),
            "max_ms": round(vals[-1], 3)}
    out["classification"] = classify(out)
    return out


def classify(s: dict) -> dict:
    """Name the architecture from its numbers. Never 'HFT'."""
    stages = s.get("stages") or {}
    ack = (stages.get("broker_ack") or {}).get("p50_ms")
    decision = (stages.get("decision") or {}).get("p50_ms")
    if ack is None:
        label = "unmeasured"
        why = ("no broker round trip has been measured against a real venue; "
               "the decision path alone does not characterise the system")
    elif ack < 5:
        label = "low-latency systematic (simulated venue)"
        why = (f"broker acknowledgement p50 {ack} ms — that is a simulator or a "
               f"co-located venue, not a retail broker over the internet")
    elif ack < 250:
        label = "low-latency systematic trading"
        why = f"broker acknowledgement p50 {ack} ms"
    else:
        label = "systematic trading"
        why = f"broker acknowledgement p50 {ack} ms"
    return {"label": label, "why": why, "decision_p50_ms": decision,
            "not_hft_because": ("HFT needs co-location, kernel-bypass networking and "
                                "microsecond decision paths; this is a Python process "
                                "reaching a broker over the public internet")}
