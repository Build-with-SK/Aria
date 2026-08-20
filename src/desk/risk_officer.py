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
    """Best-effort sector lookup from the universe index.
    Crypto gets its own bucket; an equity with no known sector buckets
    per-ticker ('Unsectored:X') so unknowns never aggregate into one
    fake sector that blockades the 25% cap — for those names the sector
    cap honestly degrades to the name cap."""
    if ticker in _sector_cache:
        return _sector_cache[ticker]
    sector, asset_class = None, None
    try:
        if UNIVERSE_DB.exists():
            with sqlite3.connect(str(UNIVERSE_DB)) as conn:
                row = conn.execute(
                    "SELECT sector, asset_class FROM symbols WHERE symbol = ? OR yahoo = ?",
                    (ticker, ticker)).fetchone()
                if row:
                    sector, asset_class = row[0], row[1]
    except Exception as e:
        logger.debug(f"sector lookup failed for {ticker}: {e}")
    if not sector:
        if asset_class == "crypto" or ticker.endswith("-USD"):
            sector = "Crypto"
        else:
            sector = f"Unsectored:{ticker}"
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
        # Size against what he would actually risk, not against what the paper
        # account happens to hold. At $100k a 5% position is $5,000 and the
        # drawdown halt trips at $2,000 — numbers describing a portfolio that
        # does not exist, and a paper record sized like that teaches nothing
        # about a person with £100. src/desk/capital.py resolves the base and
        # falls back to the broker whenever it cannot (no base set, no FX
        # rate, a base larger than the account).
        # TWO EQUITY FIGURES, ANSWERING DIFFERENT QUESTIONS. Conflating them
        # halts the desk: the day state had recorded the broker's $10,005 as
        # this morning's start, and sizing against a £100 base made that read
        # as a 98.65% intraday loss. The circuit breaker fired on every
        # candidate, permanently, for a re-basing rather than a loss.
        #
        #   broker_equity — has the ACCOUNT lost money today? Only real money
        #                   movement can trip the drawdown halt.
        #   equity        — how big may a position be? That is his declared
        #                   capital, and it is a constant, so it can never
        #                   "fall".
        # reported_equity is what the broker actually said — 0.0/None when it
        # said nothing. broker_equity adds a fallback so sizing still works.
        # Only the former may seed the day's start equity: seeding the fallback
        # would make the next real reading look like a 90% collapse.
        reported_equity = float(account.get("equity") or 0.0)
        broker_equity = reported_equity or 100_000.0
        try:
            from src.desk.capital import sizing_base
            base = sizing_base(account, getattr(self, "cfg", None))
            equity = float(base.get("equity") or 0.0)
            capital_note = base.get("note", "")
        except Exception as e:                  # sizing must never be blocked
            logger.warning("capital base unavailable, using broker equity: %s", e)
            equity, capital_note = 0.0, f"capital base unavailable: {e}"
        equity = equity or broker_equity

        positions = account.get("positions") or []
        day = load_day_state(current_equity=reported_equity or None)

        checks = {"equity": equity, "broker_equity": broker_equity,
                  "day": dict(day),
                  "circuit_breaker": False, "drawdown_pct": 0.0,
                  "capital_base": capital_note}

        # 7. Drawdown circuit-breaker — halts ALL new entries
        #
        # Three states, not two. This used to have two: a positive start
        # equity ran the check, and everything else — including a 0 written by
        # a broker that was down at the day roll — skipped it silently while
        # reporting drawdown_pct 0.0, which reads on the UI as "flat today".
        # A breaker that reports 0% because it could not run is worse than one
        # that reports nothing, because 0% is reassuring.
        start_eq = day.get("start_equity")
        start_eq = float(start_eq) if start_eq else 0.0
        if start_eq <= 0:
            # UNKNOWN — refuse rather than assume flat. In practice this window
            # is small: the day state is seeded by the first tick that gets a
            # real reading, and a broker too disconnected to report equity
            # cannot fill an order anyway.
            checks["drawdown_pct"] = None
            checks["drawdown_state"] = "unknown"
            checks["circuit_breaker"] = True
            return [], [{"trade": c, "reason":
                         "CIRCUIT BREAKER — start-of-day equity unknown "
                         "(broker down at the day roll); drawdown cannot be "
                         "measured, so new entries are held"}
                        for c in candidates], checks
        dd = (start_eq - broker_equity) / start_eq * 100.0
        checks["drawdown_pct"] = round(dd, 3)
        checks["drawdown_state"] = "measured"
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

        # Portfolio-mindful position cap from the capital allocator (optional;
        # None on legacy callers). New names may not push the open-book count
        # past the allocator's max_positions for this equity band + regime.
        alloc = getattr(self, "allocation", None)
        held_names = {p.get("ticker") for p in positions}
        max_positions = getattr(alloc, "max_positions", None)

        for c in sorted(candidates, key=lambda x: -x.get("conviction", 0)):
            reason = None
            notional = abs(float(c.get("notional") or 0.0))
            sec = c.get("sector") or sector_of(c["ticker"])
            c["sector"] = sec
            # Count open names + already-approved new names against the cap;
            # adding to an existing position is exempt.
            projected_names = held_names | {a["ticker"] for a in approved}
            is_new_name = c["ticker"] not in projected_names

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

            # 8. Portfolio position cap (capital allocator) — new names only
            elif (max_positions is not None and is_new_name
                  and len(projected_names) >= max_positions):
                reason = (f"Position cap — {alloc.mode} mode allows "
                          f"{max_positions} open names at this equity "
                          f"(holding {len(projected_names)})")

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
