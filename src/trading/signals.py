"""
src/trading/signals.py
======================
What a strategy hands to the risk engine. Nothing more authoritative than this.

A Signal is a PROPOSAL. It carries the strategy's view — side, expected edge,
where it is wrong (the stop) — and the time of the data it was computed from.
It carries no size: sizing is the risk engine's job, so a strategy that
"believes" strongly cannot buy itself a bigger position by saying so.

`expected_edge_bps` is measured against the MID and is GROSS — before fees, the
spread and slippage. The risk engine subtracts those; a strategy that netted
them itself would have them subtracted twice, and one that forgot would be
caught by nothing.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class Signal:
    strategy_id: str
    symbol: str
    side: str                            # "buy" | "sell"
    expected_edge_bps: float             # gross, vs mid
    stop_price: Optional[float]          # where the idea is wrong
    data_timestamp: datetime             # when the inputs were observed
    asset_class: str = "equity"
    sector: str = ""
    target_price: Optional[float] = None
    horizon_s: float = 0.0
    rationale: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fractionable: bool = True
    signal_id: str = ""

    def __post_init__(self):
        if self.side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', not {self.side!r}")
        if not self.signal_id:
            # Deterministic from content: the same observation from the same
            # strategy is the same signal, which is what makes the order it
            # produces idempotent downstream.
            digest = hashlib.sha256(
                f"{self.strategy_id}|{self.symbol}|{self.side}|"
                f"{self.data_timestamp.isoformat()}".encode()).hexdigest()[:16]
            object.__setattr__(self, "signal_id", f"sig-{digest}")

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("data_timestamp", "created_at"):
            d[k] = d[k].isoformat()
        return d
