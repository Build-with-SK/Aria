"""
src/desk/desk_daemon.py
=======================
THE DESK — one autonomous cycle:

  analysts → debate → risk officer → slate → (auto-exec | approval queue) → reflect

Runs on APScheduler every N minutes (config), started by the FastAPI server.
The cycle always produces a slate; whether anything EXECUTES is decided
solely by the auto_executor's safety contract (paper-only, armed, budgeted).
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
DESK_DIR = ROOT / "data" / "desk"
STATE_FILE = DESK_DIR / "last_cycle.json"
SLATE_FILE = DESK_DIR / "slate.json"
EQUITY_FILE = DESK_DIR / "equity_curve.jsonl"


class DeskDaemon:
    def __init__(self):
        self.scheduler = None
        self.running = False
        self.working = False            # True while a cycle is in progress
        self.cycle_count = 0
        self.last_cycle: dict = {}
        self.memory = None              # LongTermMemory, lazy
        self._lock = threading.Lock()
        self._load_state()

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self):
        if self.running:
            return
        from src.desk.config import load_config
        from apscheduler.schedulers.background import BackgroundScheduler
        interval = load_config()["interval_minutes"]
        self.scheduler = BackgroundScheduler()
        self.scheduler.add_job(self._run_cycle, "interval", minutes=interval,
                               id="desk_cycle", replace_existing=True)
        self.scheduler.start()
        self.running = True
        logger.info(f"Desk daemon started (every {interval} min)")

    def stop(self):
        self.running = False
        if self.scheduler is not None:
            try:
                self.scheduler.shutdown(wait=False)
            except Exception:
                pass
            self.scheduler = None

    def set_interval(self, minutes: int):
        if self.scheduler is not None and self.running:
            self.scheduler.reschedule_job("desk_cycle", trigger="interval",
                                          minutes=max(5, int(minutes)))

    def run_now(self):
        threading.Thread(target=self._run_cycle, daemon=True).start()

    def status(self) -> dict:
        from src.desk.auto_executor import AutoExecutor, account_snapshot
        from src.desk.config import load_config
        cfg = load_config()
        account = account_snapshot()
        gates = AutoExecutor(cfg).gates(account)
        return {
            "running": self.running,
            "working": self.working,
            "cycle_count": self.cycle_count,
            "last_cycle_at": self.last_cycle.get("at"),
            "last_cycle": {k: v for k, v in self.last_cycle.items()
                           if k not in ("transcripts",)},
            "config": cfg,
            "gates": gates,
            "account": {k: account.get(k) for k in
                        ("equity", "cash", "buying_power", "connected", "paper")},
            "open_positions": len(account.get("positions") or []),
        }

    # ── the desk cycle ───────────────────────────────────────────────────

    def _get_memory(self):
        if self.memory is None:
            try:
                from src.brain.cognitive.long_term_memory import LongTermMemory
                self.memory = LongTermMemory()
            except Exception as e:
                logger.warning(f"desk memory unavailable: {e}")
        return self.memory

    def _run_cycle(self):
        if not self._lock.acquire(blocking=False):
            logger.info("Desk cycle skipped — previous cycle still running")
            return
        self.working = True
        started = datetime.now()
        summary = {"at": started.isoformat(), "focus": [], "debates": [],
                   "slate": [], "executions": [], "errors": []}
        try:
            from src.desk.analysts import (fundamental_agent, macro_agent,
                                           sentiment_agent, technical_agent)
            from src.desk.auto_executor import AutoExecutor, account_snapshot
            from src.desk.config import load_config
            from src.desk.debate import DebateEngine
            from src.desk.portfolio_manager import PortfolioManager

            cfg = load_config()
            memory = self._get_memory()

            # 0. LEARN — record outcomes of past desk trades (reuses BrainLearner)
            try:
                from src.brain.cognitive.learner import BrainLearner
                if memory is not None:
                    BrainLearner(memory).check_outcomes()
            except Exception as e:
                summary["errors"].append(f"learner: {e}")

            # 1. MACRO conditioner — market-wide, scales everything downstream
            conditioner = macro_agent.condition()
            summary["regime"] = conditioner.regime
            summary["risk_multiplier"] = conditioner.risk_multiplier

            # 2. FOCUS — strongest executable signals not already held/pending
            account = account_snapshot()
            focus = self._focus_tickers(cfg["focus_tickers"], account)
            summary["focus"] = focus

            # 3. ANALYSTS → 4. DEBATE per focus ticker
            engine = DebateEngine(model=cfg["llm_model"],
                                  rounds=cfg["debate_rounds"], memory=memory)
            transcripts = []
            for ticker in focus:
                try:
                    opinions = [technical_agent.opine(ticker),
                                fundamental_agent.opine(ticker),
                                sentiment_agent.opine(ticker)]
                    t = engine.debate(ticker, opinions, conditioner)
                    transcripts.append(t)
                    summary["debates"].append({
                        "id": t["id"], "ticker": ticker,
                        "verdict": t["judge"]["verdict"],
                        "conviction": t["judge"]["conviction"]})
                except Exception as e:
                    logger.exception(f"debate failed for {ticker}")
                    summary["errors"].append(f"debate {ticker}: {e}")

            # 5. RISK OFFICER + PORTFOLIO MANAGER → the slate
            pm = PortfolioManager(cfg, conditioner)
            slate_result = pm.build_slate(transcripts, account)
            summary["slate"] = slate_result["slate"]
            summary["risk_rejected"] = [
                {"ticker": r["trade"]["ticker"], "reason": r["reason"]}
                for r in slate_result["rejected"]]
            summary["risk_checks"] = slate_result["checks"]

            # 6. EXECUTE — safety contract decides auto vs approval queue
            executor = AutoExecutor(cfg)
            exec_results = executor.execute_slate(slate_result["slate"], transcripts)
            summary["executions"] = exec_results

            # 7. REFLECT — store each debate in ChromaDB tagged with debate_id,
            #    linked to its trade ids so outcomes flow back into future recalls
            self._reflect(transcripts, exec_results, conditioner, memory)

            # 8. Persist state for the API/UI
            self._snapshot_equity(account)
            SLATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            SLATE_FILE.write_text(json.dumps({
                "at": summary["at"],
                "regime": conditioner.regime,
                "slate": slate_result["slate"],
                "rejected": summary["risk_rejected"],
                "checks": slate_result["checks"],
                "debates": summary["debates"],
            }, indent=2, default=str), encoding="utf-8")

            self.cycle_count += 1
            summary["cycle_count"] = self.cycle_count
            summary["duration_s"] = round((datetime.now() - started).total_seconds(), 1)
            auto_fills = sum(1 for r in exec_results if r.get("mode") == "auto")
            logger.info(f"Desk cycle done — {len(transcripts)} debates, "
                        f"{len(slate_result['slate'])} slated, {auto_fills} auto-filled")
        except Exception as e:
            logger.exception("Desk cycle failed")
            summary["errors"].append(str(e))
        finally:
            self.last_cycle = summary
            self._save_state()
            self.working = False
            self._lock.release()

    # ── helpers ──────────────────────────────────────────────────────────

    def _focus_tickers(self, n: int, account: dict) -> list:
        from src.desk.opinion import load_data_json
        signals = load_data_json("signals.json")
        held = {p["ticker"] for p in (account.get("positions") or [])}
        try:
            from src.execution.approval_queue import ApprovalQueue
            pending = {t.ticker for t in ApprovalQueue().get_all(limit=500)
                       if t.status == "pending"}
        except Exception:
            pending = set()

        def executable(t, d):
            ac = (d.get("asset_class") or "").lower()
            if "crypto" in ac or t.endswith("-USD"):
                return True
            return not any(c in t for c in ("=", "^"))    # no futures/indices

        ranked = sorted(
            ((t, d) for t, d in signals.items()
             if isinstance(d, dict) and executable(t, d)
             and t not in held and t not in pending
             and (d.get("current_price") or 0) > 0),
            key=lambda kv: -abs(kv[1].get("composite_score") or 0))
        return [t for t, _ in ranked[:max(1, n)]]

    def _reflect(self, transcripts, exec_results, conditioner, memory):
        """Module 5 — semantic, per-thesis memory keyed by debate_id."""
        if memory is None:
            return
        by_debate = {}
        for r in exec_results:
            if r.get("trade_id"):
                by_debate.setdefault(r.get("debate_id"), []).append(r["trade_id"])
        for t in transcripts:
            try:
                from src.brain.cognitive.long_term_memory import BrainMemory
                judge = t["judge"]
                trade_ids = by_debate.get(t["id"], [])
                text = (f"Desk debate on {t['ticker']} in {judge['regime']} regime: "
                        f"verdict {judge['verdict']} ({judge['view']}, "
                        f"conviction {judge['conviction']}/100). "
                        f"Key risk: {judge['key_risk']} "
                        f"Reasoning: {judge['reasoning'][:300]}")
                memory.remember(BrainMemory(
                    id=t["id"],                      # memory id IS the debate id
                    timestamp=datetime.now(),
                    cycle_summary=text,
                    tickers_considered=[t["ticker"]],
                    regime=judge["regime"],
                    action_taken="PROPOSED_TRADE" if trade_ids else "MONITORING",
                    trade_proposed={"ids": trade_ids, "debate_id": t["id"]},
                    outcome={},
                    tags=["desk-debate", judge["regime"], f"debate:{t['id']}"],
                ))
            except Exception as e:
                logger.warning(f"desk reflection failed for {t.get('id')}: {e}")

    def _snapshot_equity(self, account: dict):
        if not account.get("connected"):
            return
        try:
            EQUITY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with EQUITY_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"at": datetime.now().isoformat(),
                                    "equity": account.get("equity")}) + "\n")
        except Exception as e:
            logger.debug(f"equity snapshot failed: {e}")

    # ── persistence ──────────────────────────────────────────────────────

    def _save_state(self):
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(self.last_cycle, indent=2, default=str),
                                  encoding="utf-8")
        except Exception as e:
            logger.warning(f"desk state save failed: {e}")

    def _load_state(self):
        if STATE_FILE.exists():
            try:
                self.last_cycle = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                self.cycle_count = self.last_cycle.get("cycle_count", 0)
            except Exception:
                self.last_cycle = {}


_desk: DeskDaemon | None = None


def get_desk() -> DeskDaemon:
    global _desk
    if _desk is None:
        _desk = DeskDaemon()
    return _desk


def peek_desk() -> DeskDaemon | None:
    return _desk
