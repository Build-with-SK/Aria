"""
src/brain/cognitive/planner.py
==============================
PLANNER — converts ReasoningResult decisions into concrete trades.
Bridges the brain's language-level decisions into the execution system.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .perception import PerceptionSnapshot

logger = logging.getLogger(__name__)

MIN_NOTIONAL = 50.0      # trades below this are too small to execute
MAX_FRACTION = 0.05      # never risk more than 5% of equity on one idea
KELLY_SCALE  = 0.25      # quarter-Kelly


@dataclass
class PlannedTrade:
    ticker:        str
    side:          str          # "buy" | "sell"
    qty:           float
    asset_class:   str          # "equity" | "crypto"
    stop_loss:     float
    take_profit:   float
    signal_score:  float
    conviction:    int
    thesis:        str          # the brain's reasoning, for display in the UI
    broker:        str = "alpaca"
    current_price: float = 0.0


def _map_asset_class(sig) -> str:
    """Map signal asset_class / ticker style to execution asset classes."""
    ac = (sig.asset_class or "").lower()
    if "crypto" in ac or sig.ticker.endswith("-USD"):
        return "crypto"
    if any(c in sig.ticker for c in ("=", "^")):
        return ""   # futures / indices — not executable via alpaca
    return "equity"


class TradePlanner:
    """Turns PROPOSE_* decisions into sized, risk-bounded PlannedTrades."""

    def plan(
        self,
        decisions: list,                       # list[TradeDecision]
        perception: PerceptionSnapshot,
        account_equity: float = 100_000,
    ) -> list[PlannedTrade]:
        planned = []
        pending = self._pending_tickers()

        for d in decisions:
            if not d.action.startswith("PROPOSE"):
                continue
            sig = perception.signals.get(d.ticker)
            if sig is None:
                logger.info(f"Planner: {d.ticker} not in signals.json — skipped")
                continue
            if d.ticker in pending:
                logger.info(f"Planner: {d.ticker} already pending in approval queue — skipped")
                continue
            price = sig.current_price
            if not price or price <= 0:
                logger.info(f"Planner: {d.ticker} has no valid price — skipped")
                continue

            asset_class = _map_asset_class(sig)
            if not asset_class:
                logger.info(f"Planner: {d.ticker} asset class not executable — skipped")
                continue

            side = "buy" if d.action == "PROPOSE_BUY" else "sell"

            # Kelly-inspired sizing: edge over vol, quarter-Kelly, capped at 5%
            prob = sig.bullish_prob if side == "buy" else sig.bearish_prob
            edge = prob - 0.5
            kelly = edge / max(sig.realised_vol, 0.01)
            fraction = min(max(kelly, 0.0) * KELLY_SCALE, MAX_FRACTION)
            notional = account_equity * fraction
            if notional < MIN_NOTIONAL:
                logger.info(f"Planner: {d.ticker} notional ${notional:.0f} < ${MIN_NOTIONAL} — skipped")
                continue

            qty = notional / price
            if asset_class == "crypto":
                qty = round(qty, 6)
                if qty < 0.001:
                    continue
            else:
                qty = float(int(qty))
                if qty < 1:
                    continue

            thesis = d.reason or sig.explanation

            # ── second opinion, before this reaches a human ──────────────────
            #
            # A trade heading for the approval queue is the most expensive thing
            # ARIA produces to reverse, which makes it the one decision worth
            # asking an independent system to attack. SENTINEL is a separate
            # intelligence in its own process; this is one HTTP call, advisory
            # only, and OFF unless ARIA_SENTINEL_CONSULT is set.
            #
            # It cannot block the trade. If it could, there would be two
            # decision makers and the approval queue would no longer be the
            # place where a human decides. What it can do is put its objections
            # in front of that human, attached to the thesis they are reading.
            try:
                from src.consult.bridge import consider_trade
                second = consider_trade(
                    ticker=d.ticker, side=side, thesis=thesis,
                    conviction=d.conviction,
                    evidence=[sig.explanation] if sig.explanation else None)
                if second.get("consulted") and second.get("ok"):
                    review = second.get("summary", "")
                    thesis = (f"{thesis}\n\n--- INDEPENDENT REVIEW ---\n{review}")
                elif second.get("sentinel_status") != "NOT_CONSULTED":
                    # ARIA asked and got nothing usable back — unreachable,
                    # timed out, refused, or malformed. Every one of those is
                    # disclosed, not just the one that was easiest to name:
                    # a human who sees a disclosure for an offline consultant
                    # and none for a garbled one will reasonably read the
                    # silence as approval, which is the failure this whole
                    # bridge exists to prevent.
                    #
                    # NOT_CONSULTED is the exception, and is deliberately
                    # silent. It means ARIA never asked — the feature is off,
                    # or the gate judged the call routine enough to make on its
                    # own. That is not SENTINEL declining to answer, and
                    # stamping a banner on every confidently-held proposal
                    # would teach the reader to skip the one that matters.
                    state = second.get("sentinel_status") or "UNAVAILABLE"
                    thesis = (f"{thesis}\n\n[No independent review — SENTINEL: "
                              f"{state}. This is not agreement.]")
            except Exception as e:            # never break the trading path
                logger.debug(f"Planner: consultation skipped for {d.ticker}: {e}")

            planned.append(PlannedTrade(
                ticker=d.ticker,
                side=side,
                qty=qty,
                asset_class=asset_class,
                stop_loss=sig.stop_loss,
                take_profit=sig.take_profit,
                signal_score=sig.composite_score,
                conviction=d.conviction,
                thesis=thesis,
                broker="alpaca",
                current_price=price,
            ))
        return planned

    def _pending_tickers(self) -> set:
        """Tickers that already have a pending trade in the approval queue."""
        try:
            from src.execution.approval_queue import ApprovalQueue
            return {t.ticker for t in ApprovalQueue().get_all(limit=500)
                    if t.status == "pending"}
        except Exception as e:
            logger.warning(f"Planner could not read approval queue: {e}")
            return set()
