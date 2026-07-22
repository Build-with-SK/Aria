"""
src/desk/portfolio_manager.py
=============================
PORTFOLIO MANAGER — turns judge verdicts into a ranked trade slate.

Sizing is the existing quarter-Kelly TradePlanner (reused, not duplicated),
then scaled by the macro conditioner's risk multiplier and finally capped
by the RiskOfficer. Each slate entry carries its debate_id so every trade
is explainable back to the transcript and evidence that produced it.
"""
from __future__ import annotations

import logging

from src.desk.risk_officer import RiskOfficer, sector_of

logger = logging.getLogger(__name__)


class PortfolioManager:
    def __init__(self, config: dict, conditioner):
        self.cfg = config
        self.conditioner = conditioner

    def build_slate(self, transcripts: list[dict], account: dict) -> dict:
        """
        transcripts: debate dicts (with judge verdicts) from DebateEngine.
        account:     {equity, positions} from the broker (or paper defaults).
        Returns {slate, rejected, checks} — slate is ranked, sized, capped.
        """
        from src.brain.cognitive.perception import MarketPerception
        from src.brain.cognitive.planner import TradePlanner
        from src.brain.cognitive.reasoner import TradeDecision

        held = {p.get("ticker") for p in (account.get("positions") or [])}
        decisions, by_ticker = [], {}
        for t in transcripts:
            judge = t.get("judge") or {}
            verdict = judge.get("verdict")
            if verdict not in ("BUY", "SELL"):
                continue
            # Alpaca cannot short crypto: a SELL on an unheld crypto name is
            # un-executable and would loop in the queue forever. The bear view
            # still lands in memory via the debate transcript.
            if (verdict == "SELL" and t["ticker"].endswith("-USD")
                    and t["ticker"] not in held):
                logger.info(f"slate: dropping SELL {t['ticker']} — "
                            f"crypto shorting unsupported, nothing held")
                continue
            by_ticker[t["ticker"]] = t
            decisions.append(TradeDecision(
                ticker=t["ticker"],
                action="PROPOSE_BUY" if verdict == "BUY" else "PROPOSE_SELL",
                conviction=judge.get("conviction", 0),
                reason=judge.get("reasoning", "")[:300],
            ))

        if not decisions:
            return {"slate": [], "rejected": [], "checks": {"note": "no BUY/SELL verdicts"}}

        equity = float(account.get("equity") or 0.0) or 100_000.0
        perception = MarketPerception().perceive()
        planned = TradePlanner().plan(decisions, perception, account_equity=equity)

        mult = self.conditioner.risk_multiplier
        candidates = []
        for p in planned:
            qty = p.qty * mult
            if p.asset_class == "crypto":
                qty = round(qty, 6)
                if qty < 0.001:
                    continue
            else:
                qty = float(int(qty))
                if qty < 1:
                    continue
            price = p.current_price or 0.0
            notional = round(qty * price, 2)
            risk_amount = round(abs(price - (p.stop_loss or price)) * qty, 2)
            t = by_ticker.get(p.ticker) or {}
            judge = t.get("judge") or {}
            candidates.append({
                "ticker": p.ticker,
                "side": p.side,
                "qty": qty,
                "price": price,
                "notional": notional,
                "risk_amount": risk_amount,
                "conviction": p.conviction,
                "thesis": p.thesis,
                "stop": p.stop_loss,
                "target": p.take_profit,
                "invalidation": judge.get("invalidation_level"),
                "key_risk": judge.get("key_risk", ""),
                "sector": sector_of(p.ticker),
                "asset_class": p.asset_class,
                "signal_score": p.signal_score,
                "debate_id": t.get("id", ""),
                "regime": self.conditioner.regime,
                "risk_multiplier": mult,
            })

        approved, rejected, checks = RiskOfficer(self.cfg, self.conditioner).review(
            candidates, account)
        approved.sort(key=lambda c: -c["conviction"])
        return {"slate": approved, "rejected": rejected, "checks": checks}
