"""
src/trading/outcomes.py
=======================
What happened, recorded in full, as RESEARCH DATA.

Every closed trade becomes a structured record — not a sentence, a row:

    strategy · signal · entry · exit · expected edge · realised P&L · fees
    slippage · holding period · market regime · why it was taken · risk state

and five research memories alongside it:

    research    what was investigated and what was found
    strategy    what a strategy is, and how its assumptions have held
    regime      what the market was doing, so like is compared with like
    outcome     closed trades (the rows above)
    failure     what went wrong — rejections, timeouts, drift, bad fills

NOT TRAINING DATA
-----------------
Every row is written with `training_eligible: false`. Nothing here is fed to a
training pipeline by this module or any other; making a corpus out of these
records would be a separate, explicit, human act — and training is paused in
this deployment. The distinction matters beyond housekeeping: a model trained on
its own trades learns to reproduce its own mistakes with more confidence, and
these rows contain the owner's positions and balances, which are private.

`expected_vs_realised()` is the honest scoreboard: what the strategy said the
edge was, against what the account actually received after costs.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent.parent
MEMORY_DIR = ROOT / "data" / "trading" / "research_memory"
OUTCOMES = MEMORY_DIR / "outcome.jsonl"

KINDS = ("research", "strategy", "regime", "outcome", "failure")


@dataclass
class TradeOutcome:
    strategy_id: str
    signal_id: str
    symbol: str
    side: str
    qty: float
    entry_price: float
    entry_at: datetime
    exit_price: Optional[float] = None
    exit_at: Optional[datetime] = None
    expected_edge_bps: float = 0.0
    expected_costs_bps: float = 0.0
    gross_pnl: float = 0.0
    fees: float = 0.0
    realised_pnl: float = 0.0            # net of fees
    slippage_bps: float = 0.0            # realised fill vs decision price
    holding_period_s: float = 0.0
    regime: str = ""
    decision_reasons: list = field(default_factory=list)
    risk_state: dict = field(default_factory=dict)      # limits & account at the time
    mode: str = ""
    broker: str = ""
    client_order_id: str = ""
    out_of_sample: bool = True
    notes: str = ""

    @property
    def notional(self) -> float:
        return abs(self.qty * self.entry_price)

    @property
    def realised_edge_bps(self) -> float:
        return (self.realised_pnl / self.notional * 1e4) if self.notional else 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("entry_at", "exit_at"):
            v = d.get(k)
            d[k] = v.isoformat() if isinstance(v, datetime) else v
        d["notional"] = round(self.notional, 6)
        d["realised_edge_bps"] = round(self.realised_edge_bps, 2)
        return d


def remember(kind: str, payload: dict) -> dict:
    """Append one research memory. Always marked not-training-eligible."""
    if kind not in KINDS:
        raise ValueError(f"unknown memory kind {kind!r}; one of {KINDS}")
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "kind": kind, "training_eligible": False, **payload}
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with (MEMORY_DIR / f"{kind}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
    return row


def record_outcome(outcome: TradeOutcome) -> dict:
    row = remember("outcome", outcome.to_dict())
    if outcome.realised_pnl < 0 or (
            outcome.expected_edge_bps > 0 and outcome.realised_edge_bps <= 0):
        remember("failure", {
            "what": "trade lost money or missed its expected edge",
            "strategy_id": outcome.strategy_id, "signal_id": outcome.signal_id,
            "expected_edge_bps": outcome.expected_edge_bps,
            "realised_edge_bps": round(outcome.realised_edge_bps, 2),
            "slippage_bps": outcome.slippage_bps, "fees": outcome.fees,
            "regime": outcome.regime})
    return row


def record_failure(what: str, **detail) -> dict:
    """A rejection, a timeout, a halt, a bad fill — the things that do not
    appear in a P&L curve but explain it."""
    return remember("failure", {"what": what, **detail})


def read(kind: str, limit: int = 500) -> list[dict]:
    path = MEMORY_DIR / f"{kind}.jsonl"
    if kind not in KINDS or not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def outcomes(strategy_id: str = "", limit: int = 1000) -> list[dict]:
    rows = read("outcome", limit)
    return [r for r in rows if not strategy_id or r.get("strategy_id") == strategy_id]


def expected_vs_realised(strategy_id: str = "") -> dict:
    """Did the edge the strategy claimed actually arrive? The single most
    useful number about a strategy, and the easiest one to avoid computing."""
    rows = [r for r in outcomes(strategy_id) if r.get("exit_at")]
    if not rows:
        return {"trades": 0, "note": "no closed trades recorded"}
    exp = [float(r.get("expected_edge_bps", 0)) for r in rows]
    real = [float(r.get("realised_edge_bps", 0)) for r in rows]
    n = len(rows)
    return {
        "strategy_id": strategy_id or "all", "trades": n,
        "expected_edge_bps_mean": round(sum(exp) / n, 2),
        "realised_edge_bps_mean": round(sum(real) / n, 2),
        "shortfall_bps_mean": round((sum(exp) - sum(real)) / n, 2),
        "slippage_bps_mean": round(
            sum(float(r.get("slippage_bps", 0)) for r in rows) / n, 2),
        "fees_total": round(sum(float(r.get("fees", 0)) for r in rows), 4),
        "training_eligible": False,
    }


def stats() -> dict:
    return {"memory_dir": str(MEMORY_DIR),
            "counts": {k: len(read(k, limit=10_000)) for k in KINDS},
            "training_eligible": False,
            "note": ("research records only; nothing here is training data, and "
                     "no training pipeline reads this directory")}
