"""
src/desk/capital_allocator.py
=============================
PORTFOLIO-MINDFUL SIZING — one deterministic policy both lanes consult before
sizing anything. Answers: given the ACTUAL money in the account right now and
the regime, how aggressive should the desk be, how many names may it hold, and
may it short?

This does NOT replace the RiskOfficer's hard caps (name %, sector %, heat,
correlation, drawdown) — those still run and still win. The allocator sits in
front of them and adapts the *strategy* to the balance:

  equity band → mode           positions   risk/trade   shorts
  < $5k       CONSERVATIVE      2           1.0%         no
  $5k–25k     STANDARD          config      config       regime
  > $25k      DIVERSIFIED       ⌊eq/2500⌋   config       regime (≤12)

Cash-aware: sizing is bounded by AVAILABLE CASH, never assumed margin — the
`max_gross_exposure_pct` wall in the RiskOfficer remains the hard limit.

Pure functions, no I/O — unit-testable and safe to call on the hot path.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

RISK_OFF_REGIMES = ("Contraction", "Crisis", "Recession", "Stagflation", "Risk-Off")


@dataclass
class Allocation:
    mode: str                 # CONSERVATIVE | STANDARD | DIVERSIFIED
    max_positions: int        # cap on concurrent open names
    risk_per_trade_pct: float # % of equity risked (stop distance) per trade
    shorts_allowed: bool
    size_multiplier: float    # extra scalar on top of the regime multiplier
    net_exposure_cap_pct: float   # target max net exposure for the regime
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def allocate(equity: float, cash: float | None, buying_power: float | None,
             regime: str, cfg: dict, open_positions: int = 0) -> Allocation:
    """Decide the strategy envelope from the live balance and regime."""
    equity = float(equity or 0.0)
    cash = float(cash if cash is not None else equity)
    risk_off = any(r in (regime or "") for r in RISK_OFF_REGIMES)
    crisis = "Crisis" in (regime or "")

    # ── equity band → mode ────────────────────────────────────────────────
    if equity < 5_000:
        mode = "CONSERVATIVE"
        max_positions = 2
        risk_per_trade = 1.0
        shorts = False
        size_mult = 0.6
    elif equity <= 25_000:
        mode = "STANDARD"
        max_positions = int(cfg.get("max_correlated_positions", 3)) + 2
        risk_per_trade = 2.0
        shorts = risk_off            # only lean short when the tape is risk-off
        size_mult = 1.0
    else:
        mode = "DIVERSIFIED"
        max_positions = min(12, int(equity // 2500))
        risk_per_trade = 2.0
        shorts = risk_off
        size_mult = 1.0

    # ── regime net-exposure ceiling ───────────────────────────────────────
    if crisis:
        net_cap = 25.0
        size_mult *= 0.5
    elif risk_off:
        net_cap = 50.0
        size_mult *= 0.75
    else:
        net_cap = 100.0

    # ── cash reality check: can't deploy money that isn't there ───────────
    # If free cash is a small fraction of equity (already heavily invested),
    # throttle new size proportionally so the desk stops adding into a full book.
    if equity > 0:
        cash_ratio = max(0.0, min(1.0, cash / equity))
        if cash_ratio < 0.25:
            size_mult *= max(0.25, cash_ratio / 0.25)  # taper toward 0.25x

    # Never let the allocator authorize more open names than are already held
    # plus a little headroom in a full book.
    if open_positions >= max_positions:
        max_positions = open_positions   # freeze new names; exits still free room

    reason = (f"{mode} @ ${equity:,.0f} equity, ${cash:,.0f} cash, regime "
              f"{regime or 'Unknown'} → {max_positions} names, "
              f"{risk_per_trade:.1f}% risk/trade, "
              f"{'shorts ok' if shorts else 'longs only'}, "
              f"net cap {net_cap:.0f}%, size ×{size_mult:.2f}")
    return Allocation(mode=mode, max_positions=max_positions,
                      risk_per_trade_pct=risk_per_trade, shorts_allowed=shorts,
                      size_multiplier=round(size_mult, 3),
                      net_exposure_cap_pct=net_cap, reason=reason)


def allocate_for_account(account: dict, regime: str, cfg: dict) -> Allocation:
    """Convenience: pull equity/cash/buying_power/open-count from an
    account_snapshot() dict."""
    return allocate(
        equity=account.get("equity"),
        cash=account.get("cash"),
        buying_power=account.get("buying_power"),
        regime=regime, cfg=cfg,
        open_positions=len(account.get("positions") or []))
