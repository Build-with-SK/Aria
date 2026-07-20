"""
src/desk/auto_executor.py
=========================
AUTONOMOUS PAPER EXECUTION — approved trades fire on their own; the human
is INFORMED after, never asked first.

SAFETY CONTRACT — do not weaken, ever:
  1. ALPACA_PAPER must be exactly "true" (env). Otherwise auto-exec is
     DISABLED and every trade falls back to the manual approval queue.
     There is no override.
  2. data/desk_config.json {"auto_execute": false} — default OFF; the user
     arms it explicitly in the UI.
  3. Per-day notional budget + max-trades/day cap. Breach → stop, notify.
  4. Every auto-fill is (a) logged to data/desk/executions.jsonl,
     (b) pushed to the alerts feed, (c) optionally pushed to the user's
     phone (ntfy / Pushover). Ticker, side, qty, fill price, thesis.
  5. A LIVE account ALWAYS routes to the manual approval queue — auto-exec
     can only ever touch the paper account. Checked against BOTH the env
     var and the live broker object's own paper flag.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from src.desk.config import load_config, paper_mode_confirmed
from src.desk.day_state import load_day_state, record_trade

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
EXEC_LOG = ROOT / "data" / "desk" / "executions.jsonl"
ALERTS_FILE = ROOT / "data" / "alerts.json"

_order_manager = None


def get_order_manager(rebuild: bool = False):
    """Desk-local lazy OrderManager (Alpaca only — desk trades are equity/crypto).
    rebuild=True discards the cached instance — a broker client can wedge
    after a network drop while a fresh one connects fine."""
    global _order_manager
    if rebuild:
        _order_manager = None
    if _order_manager is None:
        try:
            from src.execution.alpaca_broker import AlpacaBroker
            from src.execution.order_manager import OrderManager
            _order_manager = OrderManager(alpaca=AlpacaBroker())
        except Exception as e:
            logger.warning(f"desk order manager init failed: {e}")
    return _order_manager


def account_snapshot() -> dict:
    """{equity, positions, connected, paper} from the broker; paper defaults offline."""
    snap = {"equity": 0.0, "positions": [], "connected": False, "paper": None}
    try:
        mgr = get_order_manager()
        if not (mgr and mgr._alpaca and mgr._alpaca.is_connected()):
            # Self-heal: rebuild the client once — stale sessions stay stale
            mgr = get_order_manager(rebuild=True)
            if mgr and mgr._alpaca and mgr._alpaca.is_connected():
                logger.info("broker client rebuilt after stale connection")
        if mgr and mgr._alpaca and mgr._alpaca.is_connected():
            acct = mgr._alpaca.get_account()
            snap["equity"] = acct.portfolio_value or acct.cash
            snap["connected"] = True
            snap["paper"] = bool(mgr._alpaca.paper)
            snap["cash"] = acct.cash
            snap["buying_power"] = acct.buying_power
            for p in mgr._alpaca.get_positions():
                snap["positions"].append({
                    "ticker": p.ticker, "qty": p.qty, "side": p.side,
                    "avg_cost": p.avg_cost, "market_value": p.market_value,
                    "unrealized_pl": p.unrealized_pl,
                })
    except Exception as e:
        logger.warning(f"account snapshot failed: {e}")
    return snap


class AutoExecutor:
    def __init__(self, config: dict | None = None):
        self.cfg = config or load_config()

    # ── gate evaluation (pure, reportable) ────────────────────────────────

    def gates(self, account: dict | None = None) -> dict:
        """Evaluate every safety gate. auto_allowed only if ALL pass."""
        account = account or account_snapshot()
        day = load_day_state(current_equity=account.get("equity"))
        env_paper = paper_mode_confirmed()
        broker_paper = account.get("paper")
        armed = bool(self.cfg.get("auto_execute"))
        budget_left = self.cfg["daily_notional_budget"] - day.get("notional_used", 0.0)
        trades_left = self.cfg["max_trades_per_day"] - day.get("trades_count", 0)
        g = {
            "env_paper": env_paper,                       # gate 1
            "armed": armed,                               # gate 2
            "budget_left": round(budget_left, 2),         # gate 3
            "trades_left": trades_left,                   # gate 3
            "broker_connected": account.get("connected", False),
            "broker_paper": broker_paper,                 # gate 5
            "account": "paper" if broker_paper else
                       ("live" if broker_paper is False else "disconnected"),
        }
        g["auto_allowed"] = (env_paper and armed and account.get("connected")
                             and broker_paper is True
                             and budget_left > 0 and trades_left > 0)
        return g

    # ── the cycle's execution leg ─────────────────────────────────────────

    def execute_slate(self, slate: list[dict], transcripts: list[dict]) -> list[dict]:
        """
        For each slate entry: push into the approval queue (audit trail),
        then EITHER execute immediately (all gates green) OR leave it
        pending for the human. Either way the user is informed.
        """
        results = []
        debates = {t["id"]: t for t in transcripts}
        for entry in slate:
            try:
                results.append(self._handle(entry, debates.get(entry.get("debate_id"), {})))
            except Exception as e:
                logger.exception(f"desk execution failed for {entry.get('ticker')}")
                results.append({**self._base_record(entry), "mode": "error", "reason": str(e)})
        for r in results:
            self._log(r)
        return results

    def _handle(self, entry: dict, transcript: dict) -> dict:
        from src.execution.approval_queue import ApprovalQueue
        from src.execution.broker_base import AssetClass, OrderRequest, OrderSide, OrderType

        judge = transcript.get("judge") or {}
        rounds = transcript.get("rounds") or []
        bull_case = next((r.get("bull", "") for r in rounds if r.get("bull")), "")
        bear_case = next((r.get("bear", "") for r in rounds if r.get("bear")), "")

        req = OrderRequest(
            ticker=entry["ticker"],
            side=OrderSide(entry["side"]),
            qty=entry["qty"],
            order_type=OrderType.MARKET,
            asset_class=AssetClass(entry["asset_class"]),
            stop_loss=entry.get("stop"),
            take_profit=entry.get("target"),
            signal_score=entry.get("signal_score", 0.0),
            situation=f"ARIA desk cycle — debate {entry.get('debate_id', '?')}",
            thesis_summary=entry.get("thesis", "")[:400],
        )
        queued = ApprovalQueue().push(
            req,
            confidence=f"{entry.get('conviction', 0)}%",
            bull_case=bull_case[:600],
            bear_case=bear_case[:600],
            invalidation=str(entry.get("invalidation") or ""),
            current_price=entry.get("price", 0.0),
            broker="alpaca",
        )
        record = {**self._base_record(entry), "trade_id": queued.id}

        account = account_snapshot()
        gates = self.gates(account)
        record["gates"] = gates

        # SAFETY CONTRACT: any gate red → manual approval queue, no execution.
        if not gates["auto_allowed"]:
            record["mode"] = "queued"
            record["reason"] = self._gate_reason(gates)
            self._notify(f"⏸ QUEUED {entry['side'].upper()} {entry['qty']:g} {entry['ticker']}",
                         f"Awaiting your approval — {record['reason']}. "
                         f"Thesis: {entry.get('thesis', '')[:140]}")
            return record

        # gate 3 fine-grain: this specific trade must fit the remaining budget
        if entry.get("notional", 0.0) > gates["budget_left"]:
            record["mode"] = "queued"
            record["reason"] = (f"Day notional budget exhausted "
                                f"(${gates['budget_left']:.0f} left, "
                                f"trade needs ${entry.get('notional', 0):.0f})")
            self._notify("⏸ DESK BUDGET REACHED",
                         f"{entry['ticker']} queued for manual approval — {record['reason']}")
            return record

        # All gates green — execute on the PAPER account, inform after.
        mgr = get_order_manager()
        result = mgr.execute(queued.id)
        if result.get("ok"):
            fill = result.get("fill_price") or entry.get("price", 0.0)
            record["mode"] = "auto"
            record["action"] = "entry"
            record["fill_price"] = fill
            record["broker_order_id"] = result.get("broker_order_id", "")
            record_trade(entry.get("notional", 0.0))
            # Hand lifecycle ownership to the exit engine
            try:
                from src.desk.position_manager import PositionManager
                PositionManager(self.cfg).track_entry(entry, fill, queued.id)
            except Exception as e:
                logger.warning(f"could not track entry for {entry['ticker']}: {e}")
            from src.desk.notify import fmt_entry, push
            push(fmt_entry(entry["side"], entry["qty"], entry["ticker"], fill,
                           entry.get("stop"), entry.get("target")), self.cfg)
            self._alert_feed(entry, fill)
        else:
            record["mode"] = "failed"
            record["reason"] = result.get("error", "broker rejected")
            self._notify(f"✕ ORDER FAILED {entry['ticker']}",
                         f"{record['reason']} — nothing was executed.")
        return record

    # ── helpers ───────────────────────────────────────────────────────────

    def _base_record(self, entry: dict) -> dict:
        return {
            "at": datetime.now().isoformat(),
            "ticker": entry["ticker"],
            "side": entry["side"],
            "qty": entry["qty"],
            "price": entry.get("price"),
            "notional": entry.get("notional"),
            "conviction": entry.get("conviction"),
            "thesis": entry.get("thesis", "")[:300],
            "debate_id": entry.get("debate_id", ""),
            "sector": entry.get("sector", ""),
            "stop": entry.get("stop"),
            "target": entry.get("target"),
        }

    def _gate_reason(self, g: dict) -> str:
        if not g["env_paper"]:
            return "ALPACA_PAPER is not exactly 'true' — auto-exec hard-disabled"
        if g["broker_paper"] is False:
            return "LIVE account detected — live always requires human approval"
        if not g["armed"]:
            return "auto-execute is not armed"
        if not g["broker_connected"]:
            return "broker not connected"
        if g["trades_left"] <= 0:
            return "max trades/day reached"
        if g["budget_left"] <= 0:
            return "daily notional budget spent"
        return "safety gate closed"

    def _log(self, record: dict):
        try:
            EXEC_LOG.parent.mkdir(parents=True, exist_ok=True)
            with EXEC_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            logger.warning(f"could not write executions log: {e}")

    def _alert_feed(self, entry: dict, fill: float):
        """Push the fill into the sitewide alerts feed."""
        try:
            alerts = []
            if ALERTS_FILE.exists():
                alerts = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
                if not isinstance(alerts, list):
                    alerts = []
            arrow = "▲ BOUGHT" if entry["side"] == "buy" else "▼ SOLD"
            alerts.append({
                "alert_type": "DESK_AUTO_FILL",
                "severity": "INFO",
                "ticker": entry["ticker"],
                "title": f"{arrow} {entry['qty']:g} {entry['ticker']} @ ${fill:.2f} (paper)",
                "message": f"Auto-executed by the desk · conviction {entry.get('conviction')}/100 · "
                           f"{entry.get('thesis', '')[:120]}",
                "value": fill,
                "threshold": None,
                "date": datetime.now().isoformat(),
            })
            ALERTS_FILE.write_text(json.dumps(alerts, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.warning(f"could not push fill to alerts feed: {e}")

    def _notify(self, title: str, message: str):
        """Phone push — ntfy and/or Pushover, whichever is configured."""
        topic = (self.cfg.get("ntfy_topic") or "").strip()
        if topic:
            try:
                req = urllib.request.Request(
                    f"https://ntfy.sh/{urllib.parse.quote(topic)}",
                    data=message.encode("utf-8"),
                    headers={"Title": title.encode("ascii", "ignore").decode(),
                             "Tags": "chart_with_upwards_trend"})
                urllib.request.urlopen(req, timeout=10)
            except Exception as e:
                logger.warning(f"ntfy push failed: {e}")
        user, token = self.cfg.get("pushover_user", ""), self.cfg.get("pushover_token", "")
        if user and token:
            try:
                data = urllib.parse.urlencode({
                    "token": token, "user": user,
                    "title": title, "message": message}).encode()
                urllib.request.urlopen(
                    urllib.request.Request("https://api.pushover.net/1/messages.json",
                                           data=data, method="POST"), timeout=10)
            except Exception as e:
                logger.warning(f"pushover push failed: {e}")


def read_executions(n: int = 100) -> list[dict]:
    if not EXEC_LOG.exists():
        return []
    out = []
    for line in EXEC_LOG.read_text(encoding="utf-8").splitlines()[-n:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return list(reversed(out))
