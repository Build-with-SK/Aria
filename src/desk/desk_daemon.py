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


def _backup_desk_state():
    """Zip data/desk/ JSON state to data/backups/, keeping the last 14.
    A corrupted positions.json or scorecard.json is then one file-copy away
    from recovery."""
    import zipfile
    try:
        backup_dir = ROOT / "data" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = backup_dir / f"desk_{stamp}.zip"
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for p in DESK_DIR.glob("*.json"):
                z.write(p, p.name)
            for p in DESK_DIR.glob("*.jsonl"):
                z.write(p, p.name)
        backups = sorted(backup_dir.glob("desk_*.zip"))
        for old in backups[:-14]:
            old.unlink()
        logger.info(f"desk state backed up → {dest.name} ({len(backups)} kept)")
    except Exception as e:
        logger.warning(f"desk backup failed: {e}")


# Liquid crypto majors the desk will consider (Alpaca-tradeable, deep books).
CRYPTO_MAJORS = {
    "BTC-USD", "ETH-USD", "SOL-USD", "AVAX-USD", "LINK-USD", "LTC-USD",
    "BCH-USD", "DOGE-USD", "DOT-USD", "AAVE-USD", "UNI-USD", "XTZ-USD",
}


def _snapshot_technical_recs():
    """Record today's technical recommendations so their forward performance
    can be measured. Runs once daily; safe no-op on any failure."""
    try:
        from src.data.technical_tracker import snapshot
        snapshot(timeframe="1d", limit=30)
    except Exception as e:
        logger.warning(f"technical recs snapshot failed: {e}")


def us_equities_open(now: datetime | None = None) -> bool:
    """US cash session 09:30–16:00 ET, Mon–Fri. Best-effort (holidays and
    early closes are not modelled — a closed-market order just queues at
    the broker). Crypto is always eligible and bypasses this check."""
    try:
        from zoneinfo import ZoneInfo
        et = (now or datetime.now(ZoneInfo("America/New_York")))
        if et.tzinfo is None:
            et = et.replace(tzinfo=ZoneInfo("America/New_York"))
    except Exception:
        et = now or datetime.now()
    if et.weekday() >= 5:
        return False
    minutes = et.hour * 60 + et.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


class DeskDaemon:
    def __init__(self):
        self.scheduler = None
        self.running = False
        self.working = False            # True while a cycle is in progress
        self.cycle_count = 0
        self.tick_count = 0
        self.last_cycle: dict = {}
        self.last_tick: dict = {}
        self.memory = None              # LongTermMemory, lazy
        self._lock = threading.Lock()
        self._mgmt_lock = threading.Lock()
        self._broker_down_notified = False
        self._load_state()

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self):
        if self.running:
            return
        from src.desk.config import load_config
        from apscheduler.schedulers.background import BackgroundScheduler
        cfg = load_config()
        interval = cfg["interval_minutes"]
        mgmt_interval = int(cfg.get("management_tick_minutes", 5))
        # APScheduler's defaults are wrong for a box like this one.
        #
        #   misfire_grace_time defaults to 1 SECOND: a job that cannot start
        #   within a second of its slot — routine on a 2014 Mac mini running an
        #   LLM — is dropped with a log line nobody reads. An unattended loop
        #   that silently skips runs is the failure mode this whole phase is
        #   about, so the grace is an hour and misfires coalesce into one run.
        #
        #   max_instances=1 stays: two hunt cycles at once would debate and
        #   size against each other's half-finished state.
        self.scheduler = BackgroundScheduler(job_defaults={
            "misfire_grace_time": 3600,
            "coalesce": True,
            "max_instances": 1,
        })
        # Hunt cycle — analysts→debate→slate, only when the market is open
        self.scheduler.add_job(self._run_cycle, "interval", minutes=interval,
                               id="desk_cycle", replace_existing=True)
        # Management tick — exit rules + bracket healing, 24/7, cheap.
        # First run immediately: startup reconciliation + legacy triage.
        self.scheduler.add_job(self._management_tick, "interval",
                               minutes=mgmt_interval, id="desk_mgmt",
                               replace_existing=True,
                               next_run_time=datetime.now())
        # ── the research flywheel (src/v5/loop.py) ───────────────────────────
        # PREDICT once a day after the close, RESOLVE every few hours. Without
        # these two jobs the track record cannot accumulate no matter how long
        # the machine stays up: predictions were only ever logged when a human
        # opened a page, and resolution only ever ran from a manual POST.
        from src.v5 import loop as v5_loop
        self.scheduler.add_job(v5_loop.predict_once, "cron",
                               hour=v5_loop.PREDICT_HOUR, minute=10,
                               id="v5_predict", replace_existing=True)
        self.scheduler.add_job(v5_loop.resolve_once, "interval",
                               hours=v5_loop.RESOLVE_INTERVAL_HOURS,
                               id="v5_resolve", replace_existing=True,
                               next_run_time=datetime.now())
        # RESOLVE catches up on startup; PREDICT did not, and that asymmetry is
        # why the loop kept turning with nothing going into it. A machine that
        # is asleep at 22:10 skipped the whole day's predictions silently.
        # predict_catchup() declines before the slot and declines twice in one
        # day — it makes today's call late, it does not invent past ones.
        self.scheduler.add_job(v5_loop.predict_catchup, "date",
                               run_date=datetime.now(),
                               id="v5_predict_catchup", replace_existing=True)
        # His held intents — "buy 20 AAPL when it dips under 210". Checked on
        # the management tick's cadence rather than the hunt cycle's: a
        # condition he set can come true at any point in a session, and making
        # him wait half an hour for a limit he named is not holding it, it is
        # ignoring it.
        self.scheduler.add_job(self._intent_tick, "interval",
                               minutes=mgmt_interval, id="desk_intents",
                               replace_existing=True,
                               next_run_time=datetime.now())
        # Watchdog: the scheduler is in-process, so if it dies the app keeps
        # serving requests and quietly stops trading and learning. Checking
        # every 15 minutes turns that from invisible into logged and repaired.
        self.scheduler.add_job(self._watchdog, "interval", minutes=15,
                               id="desk_watchdog", replace_existing=True)
        # Nightly state backup (cheap insurance against a corrupted JSON file)
        self.scheduler.add_job(_backup_desk_state, "cron", hour=2, minute=0,
                               id="desk_backup", replace_existing=True)
        # Daily technical-recommendations snapshot (for forward hit-rate)
        self.scheduler.add_job(_snapshot_technical_recs, "cron", hour=21,
                               minute=30, id="tech_snapshot", replace_existing=True)
        self.scheduler.start()
        self.running = True
        # REFLEX fast lane — watches armed playbooks, fires in seconds
        try:
            from src.desk.reflex import get_reflex
            get_reflex().start()
        except Exception as e:
            logger.warning(f"reflex engine start failed: {e}")
        logger.info(f"Desk daemon started (hunt every {interval} min, "
                    f"management tick every {mgmt_interval} min)")

    def _watchdog(self):
        """Notice when the loop has stopped being a loop.

        Three things can go wrong quietly on a long-running box: the scheduler
        shuts itself down, a job disappears from the table, or a leg of the
        research flywheel stops firing while everything else keeps working. All
        three present identically from outside — the app answers requests
        normally and simply stops learning.
        """
        try:
            if self.scheduler is None or not self.scheduler.running:
                logger.error("desk watchdog: scheduler is not running — restarting")
                self.running = False
                self.start()
                return

            expected = {"desk_cycle", "desk_mgmt", "v5_predict", "v5_resolve"}
            present = {j.id for j in self.scheduler.get_jobs()}
            missing = expected - present
            if missing:
                logger.error("desk watchdog: jobs vanished from the scheduler "
                             "(%s) — rebuilding the schedule", sorted(missing))
                self.running = False
                self.start()
                return

            from src.v5 import loop as v5_loop
            health = v5_loop.health()
            if not health["healthy"] and health["predict"]["last_run"]:
                logger.error("desk watchdog: the research loop has stalled — %s",
                             health["note"])
        except Exception:
            logger.exception("desk watchdog failed")

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
            "tick_count": self.tick_count,
            "last_tick": self.last_tick,
            "equities_open": us_equities_open(),
            "last_cycle_at": self.last_cycle.get("at"),
            "last_cycle": {k: v for k, v in self.last_cycle.items()
                           if k not in ("transcripts",)},
            "config": cfg,
            "gates": gates,
            "account": {k: account.get(k) for k in
                        ("equity", "cash", "buying_power", "connected", "paper")},
            "open_positions": len(account.get("positions") or []),
        }

    # ── the management tick (exit engine, 24/7) ──────────────────────────

    def _intent_tick(self):
        """Check what he asked her to trade, and act if the moment has come.

        Wrapped whole: an intent book that raises must not take the management
        schedule down with it, and a held instruction failing silently is
        exactly the failure this system keeps finding in itself — so it logs
        loudly.
        """
        try:
            from src.desk import intents
            result = intents.check_once()
            if result["acted"] or result["expired"]:
                logger.info("intents: %d filled, %d expired, %d still open",
                            len(result["acted"]), len(result["expired"]),
                            result["open"])
        except Exception:
            logger.exception("the intent tick failed — his held instructions "
                             "were NOT checked this cycle")

    def _management_tick(self):
        """Every N minutes, 24/7: exit rules, trailing stops, bracket healing,
        gross-exposure triage. No LLM calls unless an exit debate triggers."""
        if not self._mgmt_lock.acquire(blocking=False):
            return
        try:
            from src.desk.position_manager import PositionManager
            summary = PositionManager().tick()
            # Arm signal-spike playbooks for the reflex lane (cheap: cached
            # signals + a held-set read; no LLM, no broker orders).
            try:
                from src.desk.auto_executor import account_snapshot
                from src.desk.reflex import arm_from_signal
                spiked = arm_from_signal(account=account_snapshot())
                if spiked:
                    summary["armed_signal_playbooks"] = [p["ticker"] for p in spiked]
            except Exception as e:
                summary.setdefault("errors", []).append(f"reflex signal-arm: {e}")
            self.tick_count += 1
            summary["tick_count"] = self.tick_count
            self.last_tick = summary
            # Heartbeat for the watchdog: proof the exit engine is alive
            try:
                (DESK_DIR / "heartbeat.json").write_text(json.dumps({
                    "at": summary["at"], "tick_count": self.tick_count,
                    "errors": summary.get("errors", [])}), encoding="utf-8")
            except Exception:
                pass

            # Broker disconnects: skip, retry next tick, notify ONCE.
            down = any("disconnected" in e for e in summary.get("errors", []))
            if down and not self._broker_down_notified:
                self._broker_down_notified = True
                try:
                    from src.desk.config import load_config
                    from src.desk.notify import push
                    push("⚠ desk: broker disconnected — retrying every tick",
                         load_config())
                except Exception:
                    pass
            elif not down and self._broker_down_notified:
                self._broker_down_notified = False
                logger.info("broker reconnected — management ticks resumed")

            if summary.get("exits") or summary.get("adopted") or summary.get("healed"):
                logger.info(f"management tick: {len(summary.get('exits', []))} exits, "
                            f"{len(summary.get('adopted', []))} adopted, "
                            f"{len(summary.get('healed', []))} brackets healed")
        except Exception:
            logger.exception("management tick failed")
        finally:
            self._mgmt_lock.release()

    def run_tick_now(self):
        threading.Thread(target=self._management_tick, daemon=True).start()

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

            # 0b. TEACH — Fable grades newly closed trades, writes lessons that
            # future debates recall (opt-in, budget-capped, never blocks).
            try:
                from src.desk.teacher import teach_from_closed_trades
                taught = teach_from_closed_trades(cfg)
                if taught.get("reviewed"):
                    summary["taught"] = taught["reviewed"]
            except Exception as e:
                summary["errors"].append(f"teacher: {e}")

            # 1. MACRO conditioner — market-wide, scales everything downstream
            conditioner = macro_agent.condition()
            summary["regime"] = conditioner.regime
            summary["risk_multiplier"] = conditioner.risk_multiplier

            # 2. FOCUS — strongest executable signals not already held/pending.
            # Equity names only debate while the US cash session is open;
            # crypto stays eligible 24/7 (weekends included).
            account = account_snapshot()
            equities_open = us_equities_open()
            summary["equities_open"] = equities_open
            focus = self._focus_tickers(cfg["focus_tickers"], account, equities_open)
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

            # 5b. ARM REFLEX PLAYBOOKS — debates that landed just under the bar
            # become fast-lane triggers instead of being discarded.
            try:
                from src.desk.reflex import arm_from_debate
                armed = [arm_from_debate(t, conditioner.conviction_bar, cfg)
                         for t in transcripts]
                summary["armed_playbooks"] = [a["ticker"] for a in armed if a]
            except Exception as e:
                summary["errors"].append(f"reflex arm: {e}")

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

    def _focus_tickers(self, n: int, account: dict,
                       equities_open: bool = True) -> list:
        """Names for this cycle, ranked by the RESEARCH ENGINE.

        This used to rank signals.json on abs(composite_score) while the
        41-module v5 engine scored the same watchlist every day and was never
        asked. Two programs sharing a database: the system computed
        recommendations and then traded a different universe.

        src/desk/focus.py ranks by the engine's conviction and falls back to
        the composite only for names it has no fresh view on — and says which
        is which, so a slate built mostly from the fallback cannot pass itself
        off as the engine's picks.
        """
        from src.desk.opinion import load_data_json
        signals = load_data_json("signals.json")

        def executable(t, d=None):
            d = d if isinstance(d, dict) else (signals.get(t) or {})
            ac = (d.get("asset_class") or "").lower()
            if "crypto" in ac or t.endswith("-USD"):
                # Liquid majors only — noisy micro-caps (BONK/PENDLE/…) dominate
                # off-hours cycles with extreme signals that never resolve.
                return t in CRYPTO_MAJORS
            if not equities_open:
                return False                  # market closed → no equity debates
            return not any(c in t for c in ("=", "^"))    # no futures/indices

        from src.desk.focus import focus_tickers
        picked = focus_tickers(max(1, n), account, executable)
        self.last_focus = picked
        logger.info("focus: %s (%s)", picked["tickers"], picked["note"])
        return picked["tickers"]

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
