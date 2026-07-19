"""
src/desk/risk_officer.py
========================
RISK OFFICER — hard rules enforced IN CODE. The debate informs sizing;
it can never override these caps. An LLM has no code path into this file.

Rules:
  1. ≤ max_name_pct   of equity per name (including existing exposure)
  2. ≤ max_sector_pct of equity per sector
  3. Portfolio heat cap — sum of open stop-distance risk ≤ heat_cap_pct
  4. Regime gate — risk-off regimes demand a higher conviction bar
     (enforced again here in code, independent of the judge)
  5. Correlation check — no stacking >N same-sector same-side positions
  6. Daily trade-count cap
  7. Drawdown circuit-breaker — paper account down >X% today halts entries
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from src.desk.day_state import load_day_state

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
UNIVERSE_DB = ROOT / "data" / "universe.db"

_sector_cache: dict = {}


def sector_of(ticker: str) -> str:
    """Best-effort sector lookup from the universe index. 'Unknown' otherwise."""
    if ticker in _sector_cache:
        return _sector_cache[ticker]
    sector = "Unknown"
    try:
        if UNIVERSE_DB.exists():
            with sqlite3.connect(str(UNIVERSE_DB)) as conn:
                row = conn.execute(
                    "SELECT sector FROM symbols WHERE symbol = ? OR yahoo = ?",
                    (ticker, ticker)).fetchone()
                if row and row[0]:
                    sector = row[0]
    except Exception as e:
        logger.debug(f"sector lookup failed for {ticker}: {e}")
    _sector_cache[ticker] = sector
    return sector


class RiskOfficer:
    def __init__(self, config: dict, conditioner):
        self.cfg = config
        self.conditioner = conditioner

    def review(self, candidates: list, account: dict) -> tuple[list, list, dict]:
        """
        candidates: list of slate-entry dicts (from the portfolio manager),
                    each {ticker, side, qty, price, notional, risk_amount,
                          conviction, sector, ...}
        account:    {equity: float, positions: [{ticker, side, market_value, sector}]}
        Returns (approved, rejected, checks) — rejected entries carry a reason;
        checks summarises the code-level state for the UI.
        """
        equity = float(account.get("equity") or 0.0) or 100_000.0
        positions = account.get("positions") or []
        day = load_day_state(current_equity=equity)

        checks = {"equity": equity, "day": dict(day),
                  "circuit_breaker": False, "drawdown_pct": 0.0}

        # 7. Drawdown circuit-breaker — halts ALL new entries
        start_eq = day.get("start_equity") or 0.0
        if start_eq > 0:
            dd = (start_eq - equity) / start_eq * 100.0
            checks["drawdown_pct"] = round(dd, 3)
            if dd > self.cfg["drawdown_halt_pct"]:
                checks["circuit_breaker"] = True
                return [], [{"trade": c, "reason":
                             f"CIRCUIT BREAKER — account down {dd:.2f}% today "
                             f"(halt at {self.cfg['drawdown_halt_pct']}%)"}
                            for c in candidates], checks

        # Existing exposure maps
        name_exposure: dict = {}
        sector_exposure: dict = {}
        sector_side_count: dict = {}
        open_heat = 0.0
        for p in positions:
            mv = abs(float(p.get("market_value") or 0.0))
            sec = p.get("sector") or sector_of(p.get("ticker", ""))
            name_exposure[p.get("ticker")] = name_exposure.get(p.get("ticker"), 0.0) + mv
            sector_exposure[sec] = sector_exposure.get(sec, 0.0) + mv
            key = (sec, "buy" if p.get("side", "long") in ("long", "buy") else "sell")
            sector_side_count[key] = sector_side_count.get(key, 0) + 1
            # Existing open risk approximated at 2% of market value when the
            # original stop is unknown — conservative but bounded.
            open_heat += mv * 0.02

        approved, rejected = [], []
        trades_today = day.get("trades_count", 0)

        for c in sorted(candidates, key=lambda x: -x.get("conviction", 0)):
            reason = None
            notional = abs(float(c.get("notional") or 0.0))
            sec = c.get("sector") or sector_of(c["ticker"])
            c["sector"] = sec

            # 1+2. Name & sector caps act as CAPS — trim size to the headroom
            # left under both, reject only if nothing meaningful fits.
            name_room = equity * self.cfg["max_name_pct"] / 100 - name_exposure.get(c["ticker"], 0.0)
            sector_room = equity * self.cfg["max_sector_pct"] / 100 - sector_exposure.get(sec, 0.0)
            headroom = min(name_room, sector_room)
            if notional > headroom and headroom > 0 and c.get("price"):
                new_qty = headroom / c["price"]
                # floor, never round up — trimmed size must stay inside the cap
                new_qty = (int(new_qty * 1e6) / 1e6 if c.get("asset_class") == "crypto"
                           else float(int(new_qty)))
                if new_qty > 0:
                    trimmed = round(new_qty * c["price"], 2)
                    if c.get("qty"):
                        c["risk_amount"] = round(float(c.get("risk_amount") or 0.0)
                                                 * new_qty / c["qty"], 2)
                    c["qty"], c["notional"] = new_qty, trimmed
                    c["trimmed_by_risk_officer"] = True
                    notional = trimmed

            # 6. Daily trade cap (count includes trades already done today)
            if trades_today + len(approved) >= self.cfg["max_trades_per_day"]:
                reason = (f"Daily trade cap reached "
                          f"({self.cfg['max_trades_per_day']}/day)")

            # 4. Regime gate — code-level, independent of the judge
            elif c.get("conviction", 0) < self.conditioner.conviction_bar:
                reason = (f"Conviction {c.get('conviction')} below regime bar "
                          f"{self.conditioner.conviction_bar} ({self.conditioner.regime})")

            # 1. Per-name cap (post-trim; 1-cent tolerance for 2dp rounding)
            elif notional > name_room + 0.01 or notional < 50:
                reason = (f"Name cap — {c['ticker']} would exceed "
                          f"{self.cfg['max_name_pct']}% of equity (no room to trim)")

            # 2. Per-sector cap
            elif notional > sector_room + 0.01:
                reason = (f"Sector cap — {sec} would exceed "
                          f"{self.cfg['max_sector_pct']}% of equity (no room to trim)")

            # 5. Correlation stack check
            elif (sector_side_count.get((sec, c["side"]), 0) + 1) > self.cfg["max_correlated_positions"]:
                reason = (f"Correlation — already "
                          f"{sector_side_count.get((sec, c['side']), 0)} open {c['side']} "
                          f"positions in {sec} (cap {self.cfg['max_correlated_positions']})")

            # 3. Portfolio heat cap
            elif (open_heat + sum(a["risk_amount"] for a in approved) +
                  float(c.get("risk_amount") or 0.0)) > equity * self.cfg["heat_cap_pct"] / 100:
                reason = (f"Heat cap — total open risk would exceed "
                          f"{self.cfg['heat_cap_pct']}% of equity")

            if reason:
                rejected.append({"trade": c, "reason": reason})
            else:
                approved.append(c)
                name_exposure[c["ticker"]] = name_exposure.get(c["ticker"], 0.0) + notional
                sector_exposure[sec] = sector_exposure.get(sec, 0.0) + notional
                sector_side_count[(sec, c["side"])] = sector_side_count.get((sec, c["side"]), 0) + 1

        checks["approved"] = len(approved)
        checks["rejected"] = len(rejected)
        return approved, rejected, checks
