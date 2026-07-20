"""
src/desk/position_manager.py
============================
THE EXIT ENGINE — the desk's missing half. Runs every management tick and
owns the entire lifecycle of every open paper position.

Exit rules are enforced IN CODE — an LLM never overrides an exit:
  1. Hard stop        — price at/through stop → market-close immediately
  2. Target hit       — close, or scale out 50% and trail (config flag)
  3. Trailing stop    — up > 1R → breakeven, then trail by ATR
  4. Thesis invalid   — price through invalidation → close;
                        composite flips hard against → exit debate (rule 6)
  5. Time stop        — older than max_hold_days with < +0.5R → close
  6. Exit debate      — borderline rule-4 cases get a deterministic
                        hold-vs-close score (LLM writes prose only)

Legacy triage on first run: every untracked broker position is adopted with
stop = entry − 1×ATR, target = entry + 2×ATR, then managed like the rest.
Gross exposure above max_gross_exposure_pct is flattened worst-first —
no accidental margin ever again.

State: data/desk/positions.json  ·  closed trades: data/desk/closed_trades.jsonl
Every close goes through the executions log + push-notification path.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from src.desk.config import load_config, paper_mode_confirmed
from src.desk.opinion import load_data_json

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
DESK_DIR = ROOT / "data" / "desk"
POSITIONS_FILE = DESK_DIR / "positions.json"
CLOSED_FILE = DESK_DIR / "closed_trades.jsonl"
EXEC_LOG = DESK_DIR / "executions.jsonl"
DEBATES_DIR = DESK_DIR / "debates"

COMPOSITE_FLIP = 20.0          # long + composite < −20 (or mirror) → rule 4b
EXIT_DEBATE_CLOSE_AT = 50      # deterministic hold-vs-close score threshold


# ─────────────────────────────────────────────────────────────────────────────
# Pure exit-rule math — unit-testable, no I/O, no LLM
# ─────────────────────────────────────────────────────────────────────────────

def _is_long(pos: dict) -> bool:
    return pos.get("side", "long") in ("long", "buy")


def _sane_invalidation(invalidation: float, entry: float, long: bool) -> float:
    """An invalidation level must sit on the LOSING side of the entry
    (below for longs, above for shorts) — anything else is stale/garbage
    signal data and would close the position instantly. Drop it."""
    if not invalidation or not entry:
        return 0.0
    if (long and invalidation >= entry) or (not long and invalidation <= entry):
        logger.warning(f"dropping nonsensical invalidation {invalidation} "
                       f"({'long' if long else 'short'} entry {entry})")
        return 0.0
    return invalidation


def r_progress(pos: dict, price: float) -> float:
    """Current profit measured in R (initial risk units)."""
    entry = float(pos.get("entry_price") or 0.0)
    initial_stop = float(pos.get("initial_stop") or pos.get("stop") or 0.0)
    risk = abs(entry - initial_stop)
    if not entry or not risk:
        return 0.0
    move = (price - entry) if _is_long(pos) else (entry - price)
    return move / risk


def trading_age_days(entry_at: str, now: datetime | None = None) -> int:
    """Weekday count between entry and now (crypto trades 24/7 but the
    time-stop intent is 'trading days'; weekdays is the honest common ruler)."""
    try:
        start = datetime.fromisoformat(entry_at).date()
    except Exception:
        return 0
    end = (now or datetime.now()).date()
    days, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


def evaluate_exit(pos: dict, price: float, signal: dict, cfg: dict,
                  now: datetime | None = None) -> dict:
    """Apply the exit rules to one position at one price.
    Returns {action: hold|close|scale_out|debate, reason, new_stop, new_high_water}.
    Deterministic — this is the law an LLM cannot override."""
    long = _is_long(pos)
    entry = float(pos.get("entry_price") or 0.0)
    stop = float(pos.get("stop") or 0.0)
    target = float(pos.get("target") or 0.0)
    out = {"action": "hold", "reason": "", "new_stop": None, "new_high_water": None}
    if price <= 0 or entry <= 0:
        return out

    # High-water mark since entry (low-water for shorts)
    hw = float(pos.get("high_water") or entry)
    new_hw = max(hw, price) if long else min(hw, price)
    if new_hw != hw:
        out["new_high_water"] = new_hw

    # 1. HARD STOP
    if stop and ((long and price <= stop) or (not long and price >= stop)):
        return {**out, "action": "close", "reason": "stop hit"}

    # 2. TARGET
    if target and ((long and price >= target) or (not long and price <= target)):
        if cfg.get("scale_out_at_target") and not pos.get("scaled_out"):
            return {**out, "action": "scale_out", "reason": "target hit — scaling out 50%"}
        return {**out, "action": "close", "reason": "target hit"}

    # 3. TRAILING STOP — once up > 1R: breakeven, then trail by ATR off the HWM
    progress = r_progress(pos, price)
    atr_pct = float(signal.get("atr_pct") or 0.0)
    if progress >= 1.0 and stop:
        candidate = entry  # breakeven ratchet
        if atr_pct > 0:
            trail = new_hw * (1 - atr_pct) if long else new_hw * (1 + atr_pct)
            candidate = max(candidate, trail) if long else min(candidate, trail)
        if (long and candidate > stop) or (not long and candidate < stop):
            out["new_stop"] = round(candidate, 4)

    # 4. THESIS INVALIDATION
    invalidation = float(pos.get("invalidation") or 0.0)
    if invalidation and ((long and price <= invalidation) or
                         (not long and price >= invalidation)):
        return {**out, "action": "close",
                "reason": "thesis invalidated (price through invalidation level)"}
    composite = signal.get("composite_score")
    if composite is not None:
        composite = float(composite)
        if (long and composite < -COMPOSITE_FLIP) or (not long and composite > COMPOSITE_FLIP):
            return {**out, "action": "debate",
                    "reason": f"signal flipped against position (composite {composite:+.0f})"}

    # 5. TIME STOP
    max_hold = int(cfg.get("max_hold_days", 10))
    min_r = float(cfg.get("time_stop_min_r", 0.5))
    if trading_age_days(pos.get("entry_at", ""), now) > max_hold and progress < min_r:
        return {**out, "action": "close",
                "reason": f"time stop ({max_hold} trading days, {progress:+.2f}R)"}

    return out


def exit_debate_score(pos: dict, price: float, signal: dict, cfg: dict,
                      now: datetime | None = None) -> tuple[int, list[str]]:
    """Deterministic hold-vs-close score for borderline exits (rule 6).
    ≥ EXIT_DEBATE_CLOSE_AT → close. The LLM may narrate; it never decides."""
    long = _is_long(pos)
    score, why = 0, []
    composite = float(signal.get("composite_score") or 0.0)
    against = (-composite) if long else composite
    if against > COMPOSITE_FLIP:
        score += 40
        why.append(f"composite {composite:+.0f} is hard against the position")
    progress = r_progress(pos, price)
    if progress < 0:
        score += 20
        why.append(f"position is losing ({progress:+.2f}R)")
    invalidation = float(pos.get("invalidation") or 0.0)
    if invalidation and price > 0:
        dist = (price - invalidation) / price if long else (invalidation - price) / price
        if dist < 0.02:
            score += 20
            why.append("price within 2% of the invalidation level")
    max_hold = int(cfg.get("max_hold_days", 10))
    if trading_age_days(pos.get("entry_at", ""), now) > max_hold / 2 and progress < 0.5:
        score += 20
        why.append("older than half the max hold with < 0.5R progress")
    return score, why


# ─────────────────────────────────────────────────────────────────────────────
# The manager
# ─────────────────────────────────────────────────────────────────────────────

class PositionManager:
    def __init__(self, config: dict | None = None):
        self.cfg = config or load_config()

    # ── state ────────────────────────────────────────────────────────────

    def load_positions(self) -> dict:
        if POSITIONS_FILE.exists():
            try:
                return json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"positions.json unreadable: {e}")
        return {}

    def save_positions(self, positions: dict):
        POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        POSITIONS_FILE.write_text(json.dumps(positions, indent=2, default=str),
                                  encoding="utf-8")

    # ── the tick ─────────────────────────────────────────────────────────

    def tick(self) -> dict:
        """One management pass: adopt → prune → exposure triage → exit rules
        → bracket healing → stale-order cleanup. Cheap unless an exit fires."""
        summary = {"at": datetime.now().isoformat(), "exits": [], "adopted": [],
                   "healed": [], "stops_moved": [], "errors": []}
        from src.desk.auto_executor import account_snapshot, get_order_manager
        mgr = get_order_manager()
        account = account_snapshot()
        if not account.get("connected"):
            summary["errors"].append("broker disconnected — tick skipped")
            return summary

        signals = load_data_json("signals.json")
        tracked = self.load_positions()
        broker_positions = {p["ticker"]: p for p in (account.get("positions") or [])}

        # LEGACY TRIAGE / adoption — every broker position gets tracked
        for ticker, bp in broker_positions.items():
            if ticker not in tracked:
                pos = self._adopt(ticker, bp, signals.get(ticker) or {})
                tracked[ticker] = pos
                summary["adopted"].append(ticker)
                logger.info(f"PositionManager adopted {ticker}: "
                            f"stop {pos['stop']}, target {pos['target']}")

        # PRUNE — tracked positions gone from the broker (bracket fired or
        # closed elsewhere). Cancel leftover sibling orders, record the close.
        open_orders = mgr.open_orders_by_ticker() if mgr else {}
        for ticker in list(tracked.keys()):
            if ticker not in broker_positions:
                self._record_external_close(tracked.pop(ticker), mgr,
                                            open_orders.get(ticker, []))
                summary["exits"].append({"ticker": ticker,
                                         "reason": "closed at broker (bracket or manual)"})

        # GROSS EXPOSURE TRIAGE — flatten worst-first until inside the cap
        equity = float(account.get("equity") or 0.0)
        cap = equity * float(self.cfg.get("max_gross_exposure_pct", 100.0)) / 100.0
        gross = sum(abs(float(bp.get("market_value") or 0.0))
                    for bp in broker_positions.values())
        if equity > 0 and gross > cap:
            for ticker in self._worst_first(broker_positions):
                if gross <= cap:
                    break
                bp = broker_positions[ticker]
                mv = abs(float(bp.get("market_value") or 0.0))
                r = self._close(tracked, ticker, bp, mgr,
                                reason=f"gross exposure {gross / equity * 100:.0f}% "
                                       f"over cap {self.cfg.get('max_gross_exposure_pct')}%")
                if r:
                    summary["exits"].append(r)
                    broker_positions.pop(ticker, None)
                    gross -= mv

        # EXIT RULES per position
        for ticker, pos in list(tracked.items()):
            bp = broker_positions.get(ticker)
            if bp is None:
                continue
            signal = signals.get(ticker) or {}
            price = self._current_price(ticker, bp, signal, mgr)
            verdict = evaluate_exit(pos, price, signal, self.cfg)
            if verdict["new_high_water"] is not None:
                pos["high_water"] = verdict["new_high_water"]
            if verdict["new_stop"] is not None:
                old = pos.get("stop")
                pos["stop"] = verdict["new_stop"]
                summary["stops_moved"].append(
                    {"ticker": ticker, "from": old, "to": pos["stop"]})
                self._replace_stop_order(ticker, pos, mgr, open_orders.get(ticker, []))

            action = verdict["action"]
            if action == "debate":
                score, why = exit_debate_score(pos, price, signal, self.cfg)
                self._persist_exit_debate(ticker, pos, price, score, why)
                if score >= EXIT_DEBATE_CLOSE_AT:
                    action, verdict["reason"] = "close", (
                        f"exit debate closed it ({score}/100): " + "; ".join(why))
                else:
                    action = "hold"

            if action == "close":
                r = self._close(tracked, ticker, bp, mgr, reason=verdict["reason"])
                if r:
                    summary["exits"].append(r)
                    broker_positions.pop(ticker, None)
            elif action == "scale_out":
                r = self._close(tracked, ticker, bp, mgr, reason=verdict["reason"],
                                fraction=0.5)
                if r:
                    summary["exits"].append(r)
                    pos["scaled_out"] = True
                    pos["target"] = None  # runner trails; no fixed target left

        # BRACKET HEALING — every surviving tracked position gets its stop
        # (and target) present at the broker; A2's fix stops new nakedness,
        # this heals restarts and partial failures.
        open_orders = mgr.open_orders_by_ticker() if mgr else {}
        for ticker, pos in tracked.items():
            if ticker in broker_positions:
                healed = self._heal_brackets(ticker, pos, mgr,
                                             open_orders.get(ticker, []))
                if healed:
                    summary["healed"].append({"ticker": ticker, **healed})

        try:
            stale = mgr.cancel_stale_orders() if mgr else []
            if stale:
                summary["stale_cancelled"] = [o["id"] for o in stale]
        except Exception as e:
            summary["errors"].append(f"stale-order cleanup: {e}")

        self.save_positions(tracked)
        return summary

    # ── adoption & triage ────────────────────────────────────────────────

    def _adopt(self, ticker: str, bp: dict, signal: dict) -> dict:
        entry = float(bp.get("avg_cost") or 0.0)
        long = bp.get("side", "long") in ("long", "buy")
        atr_pct = float(signal.get("atr_pct") or 0.02) or 0.02
        atr = entry * atr_pct
        stop = round(entry - atr, 4) if long else round(entry + atr, 4)
        target = round(entry + 2 * atr, 4) if long else round(entry - 2 * atr, 4)
        invalidation = _sane_invalidation(
            float(signal.get("invalidation") or 0.0), entry, long)
        return {
            "ticker": ticker,
            "side": "long" if long else "short",
            "qty": abs(float(bp.get("qty") or 0.0)),
            "entry_price": entry,
            "entry_at": datetime.now().isoformat(),
            "stop": stop,
            "initial_stop": stop,
            "target": target,
            "invalidation": invalidation,
            "debate_id": "",
            "thesis": "adopted legacy position — synthetic 1×ATR stop / 2×ATR target",
            "high_water": entry,
            "scaled_out": False,
            "adopted": True,
            "asset_class": "crypto" if "-USD" in ticker else "equity",
        }

    def track_entry(self, entry: dict, fill_price: float, trade_id: str = ""):
        """Called by the auto-executor after a confirmed entry fill."""
        tracked = self.load_positions()
        stop = entry.get("stop")
        tracked[entry["ticker"]] = {
            "ticker": entry["ticker"],
            "side": "long" if entry["side"] == "buy" else "short",
            "qty": float(entry["qty"]),
            "entry_price": float(fill_price or entry.get("price") or 0.0),
            "entry_at": datetime.now().isoformat(),
            "stop": stop,
            "initial_stop": stop,
            "target": entry.get("target"),
            "invalidation": _sane_invalidation(
                float(entry.get("invalidation") or 0.0),
                float(fill_price or entry.get("price") or 0.0),
                entry["side"] == "buy"),
            "debate_id": entry.get("debate_id", ""),
            "thesis": (entry.get("thesis") or "")[:300],
            "high_water": float(fill_price or entry.get("price") or 0.0),
            "scaled_out": False,
            "adopted": False,
            "asset_class": entry.get("asset_class", "equity"),
            "trade_id": trade_id,
        }
        self.save_positions(tracked)

    def _worst_first(self, broker_positions: dict) -> list[str]:
        """Triage order for exposure flattening: worst unrealized P&L% first."""
        def pnl_pct(bp):
            mv = abs(float(bp.get("market_value") or 0.0))
            pl = float(bp.get("unrealized_pl") or 0.0)
            return pl / mv if mv else 0.0
        return sorted(broker_positions, key=lambda t: pnl_pct(broker_positions[t]))

    # ── closing ──────────────────────────────────────────────────────────

    def _close(self, tracked: dict, ticker: str, bp: dict, mgr,
               reason: str, fraction: float = 1.0) -> dict | None:
        """Market-close a position (or a fraction). Paper-only autonomy:
        a live account routes to the approval queue — welded shut."""
        pos = tracked.get(ticker) or {}
        qty = abs(float(bp.get("qty") or pos.get("qty") or 0.0)) * fraction
        if pos.get("asset_class") != "crypto":
            qty = float(int(qty)) or abs(float(bp.get("qty") or 0.0))
        if qty <= 0 or mgr is None:
            return None
        long = bp.get("side", "long") in ("long", "buy")

        from src.execution.broker_base import (AssetClass, OrderRequest,
                                               OrderSide, OrderStatus, OrderType)
        broker = mgr._alpaca
        if broker is None or not broker.is_connected():
            return None

        # SAFETY: live account never auto-closes — queue for the human.
        if not (paper_mode_confirmed() and broker.paper):
            self._queue_manual_exit(ticker, pos, qty, long, reason)
            return {"ticker": ticker, "reason": reason, "mode": "queued (live account)"}

        # Equities only close while the market can actually FILL the order —
        # a market order queued overnight would sit unfilled, the position
        # would be re-adopted next tick, and closes would stack up. Defer.
        if pos.get("asset_class") != "crypto":
            from src.desk.desk_daemon import us_equities_open
            if not us_equities_open():
                logger.info(f"exit for {ticker} deferred — US market closed "
                            f"({reason})")
                return None

        # A close already in flight? Wait for it — never stack exit orders.
        exit_side = "sell" if long else "buy"
        open_for_ticker = mgr.open_orders_by_ticker().get(ticker) or []
        if any(o.get("side") == exit_side and o.get("order_type") == "market"
               for o in open_for_ticker):
            logger.info(f"exit for {ticker} already in flight — skipping")
            return None

        # Cancel protective orders first so the GTC brackets can't double-fill
        for o in open_for_ticker:
            broker.cancel_order(o["id"])

        req = OrderRequest(
            ticker=ticker,
            side=OrderSide.SELL if long else OrderSide.BUY,
            qty=qty,
            order_type=OrderType.MARKET,
            time_in_force="gtc" if pos.get("asset_class") == "crypto" else "day",
            asset_class=AssetClass(pos.get("asset_class", "equity")
                                   if pos.get("asset_class") in ("equity", "crypto")
                                   else "equity"),
        )
        result = broker.submit_order(req)
        if result.status == OrderStatus.REJECTED:
            logger.error(f"exit order rejected for {ticker}: {result.error_message}")
            return None
        if result.status != OrderStatus.FILLED:
            result = mgr._await_fill(broker, result)
        if result.status != OrderStatus.FILLED:
            # No fill, no exit: cancel so orders never stack, retry next tick.
            # A recorded P&L must come from a REAL fill, never an estimate.
            broker.cancel_order(result.broker_order_id)
            logger.warning(f"exit for {ticker} did not fill in the poll window "
                           f"— cancelled, will retry next tick ({reason})")
            return None

        exit_price = result.avg_fill_price or self._current_price(ticker, bp, {}, mgr)
        entry = float(pos.get("entry_price") or bp.get("avg_cost") or 0.0)
        direction = 1 if long else -1
        pnl = round((exit_price - entry) * qty * direction, 2)
        pnl_pct = round((exit_price - entry) / entry * 100 * direction, 2) if entry else 0.0

        record = {
            "at": datetime.now().isoformat(),
            "action": "exit",
            "ticker": ticker,
            "side": "sell" if long else "buy",
            "entry_side": "buy" if long else "sell",
            "qty": qty,
            "entry_price": entry,
            "exit_price": round(exit_price, 4),
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "r_multiple": round(r_progress(pos, exit_price), 2),
            "reason": reason,
            "fraction": fraction,
            "debate_id": pos.get("debate_id", ""),
            "thesis": pos.get("thesis", ""),
            "entry_at": pos.get("entry_at", ""),
            "mode": "auto",
            "broker_order_id": result.broker_order_id,
        }
        self._log_execution(record)
        self._log_closed(record)
        if fraction >= 1.0:
            tracked.pop(ticker, None)
        else:
            if pos:
                pos["qty"] = round(abs(float(bp.get("qty") or 0.0)) - qty, 6)

        from src.desk.notify import fmt_exit, push
        push(fmt_exit("buy" if long else "sell", qty, ticker, exit_price,
                      pnl, pnl_pct, reason), self.cfg)
        self._update_scorecard(record)
        self._record_outcome_memory(record)
        logger.info(f"CLOSED {ticker} {qty:g} @ {exit_price:.2f} "
                    f"({pnl_pct:+.2f}%) — {reason}")
        return record

    def _queue_manual_exit(self, ticker: str, pos: dict, qty: float,
                           long: bool, reason: str):
        try:
            from src.execution.approval_queue import ApprovalQueue
            from src.execution.broker_base import (AssetClass, OrderRequest,
                                                   OrderSide, OrderType)
            ApprovalQueue().push(OrderRequest(
                ticker=ticker,
                side=OrderSide.SELL if long else OrderSide.BUY,
                qty=qty, order_type=OrderType.MARKET,
                asset_class=AssetClass(pos.get("asset_class", "equity")),
                situation=f"EXIT — {reason}",
                thesis_summary=f"PositionManager exit: {reason}",
            ), confidence="exit rule", broker="alpaca")
        except Exception as e:
            logger.error(f"could not queue manual exit for {ticker}: {e}")

    def _record_external_close(self, pos: dict, mgr, leftover_orders: list):
        """A tracked position vanished from the broker: a bracket filled or a
        human closed it. Cancel sibling orders, estimate the realized P&L."""
        ticker = pos.get("ticker", "?")
        if mgr and mgr._alpaca:
            for o in leftover_orders:
                try:
                    mgr._alpaca.cancel_order(o["id"])
                except Exception:
                    pass
        entry = float(pos.get("entry_price") or 0.0)
        long = _is_long(pos)
        signal = load_data_json("signals.json").get(ticker) or {}
        exit_price = float(signal.get("current_price") or 0.0)
        # A stop or target fill happened at (roughly) its trigger price
        stop, target = float(pos.get("stop") or 0), float(pos.get("target") or 0)
        reason = "closed at broker"
        if exit_price and stop and ((long and exit_price <= stop) or
                                    (not long and exit_price >= stop)):
            exit_price, reason = stop, "stop hit (broker bracket)"
        elif exit_price and target and ((long and exit_price >= target) or
                                        (not long and exit_price <= target)):
            exit_price, reason = target, "target hit (broker bracket)"
        qty = float(pos.get("qty") or 0.0)
        direction = 1 if long else -1
        pnl = round((exit_price - entry) * qty * direction, 2) if entry and exit_price else 0.0
        pnl_pct = round((exit_price - entry) / entry * 100 * direction, 2) if entry and exit_price else 0.0
        record = {
            "at": datetime.now().isoformat(), "action": "exit",
            "ticker": ticker, "side": "sell" if long else "buy",
            "entry_side": "buy" if long else "sell", "qty": qty,
            "entry_price": entry, "exit_price": round(exit_price, 4),
            "pnl": pnl, "pnl_pct": pnl_pct,
            "r_multiple": round(r_progress(pos, exit_price), 2) if exit_price else 0.0,
            "reason": reason, "fraction": 1.0,
            "debate_id": pos.get("debate_id", ""),
            "thesis": pos.get("thesis", ""), "entry_at": pos.get("entry_at", ""),
            "mode": "broker",
        }
        self._log_execution(record)
        self._log_closed(record)
        from src.desk.notify import fmt_exit, push
        push(fmt_exit("buy" if long else "sell", qty, ticker,
                      exit_price or entry, pnl, pnl_pct, reason), self.cfg)
        self._update_scorecard(record)
        self._record_outcome_memory(record)

    # ── brackets ─────────────────────────────────────────────────────────

    def _heal_brackets(self, ticker: str, pos: dict, mgr,
                       open_orders: list) -> dict | None:
        if mgr is None:
            return None
        has_stop = any(o.get("order_type") in ("stop", "stop_limit")
                       for o in open_orders)
        has_target = any(o.get("order_type") == "limit" for o in open_orders)
        need_stop = pos.get("stop") and not has_stop
        need_target = pos.get("target") and not has_target
        if not (need_stop or need_target):
            return None
        # Protections are placed as one OCO — clear partial leftovers first
        # so the fresh order can reserve the shares.
        if mgr._alpaca is not None:
            for o in open_orders:
                if o.get("order_type") in ("stop", "stop_limit", "limit"):
                    try:
                        mgr._alpaca.cancel_order(o["id"])
                    except Exception:
                        pass
        placed = mgr.place_protective_orders(
            ticker, float(pos.get("qty") or 0.0),
            "buy" if _is_long(pos) else "sell",
            pos.get("stop"), pos.get("target"),
            asset_class=pos.get("asset_class", "equity"))
        healed = {}
        if need_stop and placed.get("stop_loss_order"):
            healed["stop"] = pos["stop"]
        if need_target and placed.get("take_profit_order"):
            healed["target"] = pos["target"]
        if healed:
            logger.info(f"healed brackets for {ticker}: {healed}")
        return healed or None

    def _replace_stop_order(self, ticker: str, pos: dict, mgr, open_orders: list):
        """Trailing ratchet: cancel the old protections, re-place the OCO
        (or stop) at the new level."""
        if mgr is None or mgr._alpaca is None:
            return
        for o in open_orders:
            if o.get("order_type") in ("stop", "stop_limit", "limit"):
                try:
                    mgr._alpaca.cancel_order(o["id"])
                except Exception:
                    pass
        mgr.place_protective_orders(
            ticker, float(pos.get("qty") or 0.0),
            "buy" if _is_long(pos) else "sell",
            pos.get("stop"), pos.get("target"),
            asset_class=pos.get("asset_class", "equity"))

    # ── helpers ──────────────────────────────────────────────────────────

    def _current_price(self, ticker: str, bp: dict, signal: dict, mgr) -> float:
        try:
            if mgr and mgr._alpaca:
                q = mgr._alpaca.get_quote(ticker)
                if q.get("mid"):
                    return float(q["mid"])
        except Exception:
            pass
        if signal.get("current_price"):
            return float(signal["current_price"])
        qty = abs(float(bp.get("qty") or 0.0))
        mv = abs(float(bp.get("market_value") or 0.0))
        return mv / qty if qty else 0.0

    def _persist_exit_debate(self, ticker: str, pos: dict, price: float,
                             score: int, why: list[str]):
        """Record the hold-vs-close mini-debate. Judge math is the score above
        — deterministic. The local LLM may add prose; it never decides."""
        debate_id = f"exit-{uuid.uuid4().hex[:8]}"
        decision = "CLOSE" if score >= EXIT_DEBATE_CLOSE_AT else "HOLD"
        prose = ""
        try:
            from src.desk.debate import _llm, _ollama_available
            if _ollama_available():
                prose = _llm(
                    f"You are the judge of a hold-vs-close debate on an open "
                    f"{pos.get('side')} position in {ticker} "
                    f"(entry {pos.get('entry_price')}, now {price:.2f}, "
                    f"{r_progress(pos, price):+.2f}R).\n"
                    f"Close case evidence: {'; '.join(why) or 'none'}.\n"
                    f"The computed decision is {decision} (score {score}/100, "
                    f"close at ≥{EXIT_DEBATE_CLOSE_AT}). In 2 sentences explain "
                    f"why this follows. Do not change the decision.",
                    self.cfg.get("llm_model", "qwen2.5-coder:7b"),
                    num_predict=120) or ""
        except Exception:
            pass
        try:
            DEBATES_DIR.mkdir(parents=True, exist_ok=True)
            (DEBATES_DIR / f"{debate_id}.json").write_text(json.dumps({
                "id": debate_id, "type": "exit_debate", "ticker": ticker,
                "at": datetime.now().isoformat(), "position": pos,
                "price": price, "score": score,
                "close_threshold": EXIT_DEBATE_CLOSE_AT,
                "evidence": why, "decision": decision, "reasoning": prose,
                "entry_debate_id": pos.get("debate_id", ""),
            }, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.warning(f"could not persist exit debate: {e}")

    def _update_scorecard(self, record: dict):
        try:
            from src.desk.scorecard import record_closed_trade
            record_closed_trade(record)
        except Exception as e:
            logger.debug(f"scorecard update failed: {e}")

    def _record_outcome_memory(self, record: dict):
        """Realized P&L → ChromaDB keyed by the entry's debate_id, so future
        debates on similar setups recall what this thesis actually did."""
        debate_id = record.get("debate_id")
        if not debate_id:
            return
        try:
            from src.brain.cognitive.long_term_memory import LongTermMemory
            duration = ""
            try:
                d0 = datetime.fromisoformat(record.get("entry_at", ""))
                duration = round((datetime.now() - d0).total_seconds() / 86400, 1)
            except Exception:
                pass
            LongTermMemory().record_outcome(debate_id, {
                "ticker": record["ticker"],
                "side": record.get("entry_side"),
                "entry_price": record.get("entry_price"),
                "exit_price": record.get("exit_price"),
                "pnl": record.get("pnl"),
                "pnl_pct": record.get("pnl_pct"),
                "r_multiple": record.get("r_multiple"),
                "duration_days": duration,
                "exit_reason": record.get("reason"),
            })
        except Exception as e:
            logger.debug(f"outcome memory write failed: {e}")

    def _log_execution(self, record: dict):
        try:
            EXEC_LOG.parent.mkdir(parents=True, exist_ok=True)
            with EXEC_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            logger.warning(f"could not log exit execution: {e}")

    def _log_closed(self, record: dict):
        try:
            CLOSED_FILE.parent.mkdir(parents=True, exist_ok=True)
            with CLOSED_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            logger.warning(f"could not log closed trade: {e}")


def read_closed_trades(n: int = 500) -> list[dict]:
    if not CLOSED_FILE.exists():
        return []
    out = []
    for line in CLOSED_FILE.read_text(encoding="utf-8").splitlines()[-n:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
