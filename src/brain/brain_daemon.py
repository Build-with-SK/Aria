"""
src/brain/brain_daemon.py
=========================
BRAIN DAEMON — the heartbeat.

Orchestrates one full cognitive cycle every 15 minutes:
LEARN → PERCEIVE → REASON → PLAN → EXECUTE → REMEMBER → SLEEP

Started by the FastAPI server on startup (if Ollama is available);
can be paused/resumed and re-paced via the /api/brain/* endpoints.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent

# ── Synapse: file-based peering with ATLAS (the user's personal AI) ─────────
# Peering directory with ATLAS. Env-overridable; defaults beside this repo
# rather than to one machine's absolute path.
SYNAPSE = Path(os.environ.get("ARIA_SYNAPSE_PATH") or (ROOT.parent / "synapse"))
SYNAPSE_IN = SYNAPSE / "atlas_to_aria.jsonl"      # ATLAS → ARIA
SYNAPSE_OUT = SYNAPSE / "aria_to_atlas.jsonl"     # ARIA → ATLAS
SYNAPSE_CURSOR = ROOT / "data" / "brain_memory" / "synapse_cursor.json"


def _synapse_read() -> str:
    """New lessons from ATLAS since last cycle, as text for RECALL context."""
    try:
        if not SYNAPSE_IN.exists():
            return ""
        cursor = 0
        if SYNAPSE_CURSOR.exists():
            cursor = json.loads(SYNAPSE_CURSOR.read_text(encoding="utf-8")).get("line", 0)
        lines = SYNAPSE_IN.read_text(encoding="utf-8").splitlines()
        msgs = []
        for line in lines[cursor:]:
            try:
                m = json.loads(line)
                msgs.append(f"- ({m.get('kind', 'lesson')}) {m.get('text', '')[:200]}")
            except Exception:
                continue
        SYNAPSE_CURSOR.parent.mkdir(parents=True, exist_ok=True)
        SYNAPSE_CURSOR.write_text(json.dumps({"line": len(lines)}), encoding="utf-8")
        return "\n".join(msgs[-5:])
    except Exception as e:
        logger.debug(f"synapse read failed: {e}")
        return ""


def _synapse_write(regime: str, decisions: list, lesson: str):
    """Leave ATLAS one line about this cycle."""
    try:
        SYNAPSE.mkdir(parents=True, exist_ok=True)
        with SYNAPSE_OUT.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "from": "ARIA", "to": "ATLAS",
                "at": datetime.now().isoformat(), "kind": "lesson",
                "text": f"Regime {regime}. Decisions: {decisions}. {lesson}"[:400],
                "refs": [],
            }, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug(f"synapse write failed: {e}")


class BrainDaemon:
    """
    Orchestrates one full cognitive cycle on a fixed interval.
    The brain proposes, the human approves — nothing here executes trades.
    """

    STATE_FILE = ROOT / "data" / "brain_state.json"

    def __init__(self, model: str = "qwen2.5-coder:7b"):
        self.model = model
        self.scheduler = None
        self.memory = None            # LongTermMemory, lazily initialised
        self.running = False
        self.thinking = False         # True while a cycle is in progress
        self.interval_minutes = 15
        self.last_cycle: dict = {}
        self.cycle_count = 0
        self._cycle_lock = threading.Lock()
        self._load_state()

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self):
        if self.running:
            return
        from apscheduler.schedulers.background import BackgroundScheduler
        self.scheduler = BackgroundScheduler()
        self.scheduler.add_job(self._run_cycle, "interval",
                               minutes=self.interval_minutes,
                               id="brain_cycle", replace_existing=True)
        self.scheduler.start()
        self.running = True
        # Run one cycle immediately on start
        self.scheduler.add_job(self._run_cycle, "date")
        logger.info(f"Brain daemon started (model={self.model}, "
                    f"interval={self.interval_minutes}min)")

    def stop(self):
        self.running = False
        if self.scheduler is not None:
            try:
                self.scheduler.shutdown(wait=False)
            except Exception:
                pass
            self.scheduler = None
        logger.info("Brain daemon stopped")

    def _propose_trades_enabled(self) -> bool:
        """Research-only by default — the desk (src/desk/) owns trading."""
        import json
        from pathlib import Path
        cfg_file = Path(__file__).parent.parent.parent / "data" / "brain_config.json"
        try:
            if cfg_file.exists():
                return bool(json.loads(cfg_file.read_text(encoding="utf-8"))
                            .get("propose_trades", False))
        except Exception:
            pass
        return False

    def set_interval(self, minutes: int):
        self.interval_minutes = max(1, int(minutes))
        if self.scheduler is not None and self.running:
            self.scheduler.reschedule_job("brain_cycle", trigger="interval",
                                          minutes=self.interval_minutes)

    def run_now(self):
        """Trigger an immediate cycle without waiting for the scheduler."""
        threading.Thread(target=self._run_cycle, daemon=True).start()

    def status(self) -> dict:
        mem_count = None
        if self.memory is not None:
            try:
                mem_count = self.memory.stats()["total_memories"]
            except Exception:
                pass
        return {
            "running":          self.running,
            "thinking":         self.thinking,
            "model":            self.model,
            "interval_minutes": self.interval_minutes,
            "cycle_count":      self.cycle_count,
            "last_cycle_at":    self.last_cycle.get("timestamp"),
            "memory_count":     mem_count,
        }

    # ── the cognitive cycle ──────────────────────────────────────────────

    def _get_memory(self):
        """Lazy — ChromaDB + sentence-transformers load only when first needed."""
        if self.memory is None:
            from src.brain.cognitive.long_term_memory import LongTermMemory
            self.memory = LongTermMemory()
        return self.memory

    def _run_cycle(self):
        if not self._cycle_lock.acquire(blocking=False):
            logger.info("Brain cycle skipped — previous cycle still running")
            return
        from src.brain.cognitive.executor import BrainExecutor
        from src.brain.cognitive.learner import BrainLearner
        from src.brain.cognitive.long_term_memory import BrainMemory
        from src.brain.cognitive.perception import MarketPerception
        from src.brain.cognitive.planner import TradePlanner
        from src.brain.cognitive.reasoner import ReasoningLoop
        from src.brain.cognitive.working_memory import WorkingMemory

        cycle_id = str(uuid.uuid4())[:8]
        wm = WorkingMemory(cycle_id=cycle_id, started_at=datetime.now())
        self.thinking = True
        try:
            memory = self._get_memory()

            # 1. LEARN — record outcomes of past executed trades
            try:
                BrainLearner(memory).check_outcomes()
            except Exception as e:
                logger.warning(f"Learner step failed (non-fatal): {e}")
                wm.warnings.append(f"Learner failed: {e}")

            # 2. PERCEIVE
            perception = MarketPerception().perceive()
            if perception.regime_shift:
                wm.warnings.append("Macro regime shifted since last cycle")

            # 3. REASON — with access to the user's Obsidian vault knowledge
            vault = None
            try:
                from src.brain.vault import get_vault
                vault = get_vault()
            except Exception as e:
                logger.warning(f"Vault unavailable for this cycle: {e}")
            reasoner = ReasoningLoop(model=self.model, vault=vault,
                                     peer_context=_synapse_read())
            result = reasoner.run_cycle(perception, memory, wm)

            # 4. PLAN
            planner = TradePlanner()
            propose = [d for d in result.trade_decisions
                       if d.action.startswith("PROPOSE")]
            planned = planner.plan(propose, perception)

            # 5. EXECUTE — push to approval queue only; human approves.
            # Since the v3.1 desk owns autonomous trading (src/desk/), the
            # brain defaults to RESEARCH-ONLY: its proposals would just pile
            # up in the Execute tab duplicating the desk's debates. Flip
            # data/brain_config.json {"propose_trades": true} to re-enable.
            queued_ids = []
            if self._propose_trades_enabled():
                queued_ids = BrainExecutor().execute_plan(planned)
            elif planned:
                logger.info(f"brain research-only: {len(planned)} planned trades "
                            f"NOT queued (desk owns trading)")

            # 6. REMEMBER — store this cycle in long-term memory
            brain_mem = BrainMemory(
                id=cycle_id,
                timestamp=datetime.now(),
                cycle_summary=result.full_monologue[:500],
                tickers_considered=result.focus_tickers,
                regime=perception.macro.regime,
                action_taken="PROPOSED_TRADE" if queued_ids else "MONITORING",
                trade_proposed={"ids": queued_ids},
                outcome={},
                tags=self._extract_tags(perception, result),
            )
            memory.remember(brain_mem)

            # 7. Save state for the API / UI to read
            self.cycle_count += 1
            self.last_cycle = {
                "cycle_id":       cycle_id,
                "timestamp":      datetime.now().isoformat(),
                "cycle_count":    self.cycle_count,
                "regime":         perception.macro.regime,
                "vix":            perception.macro.vix,
                "alerts":         perception.alerts,
                "regime_shift":   perception.regime_shift,
                "focus_tickers":  result.focus_tickers,
                "thinking_steps": result.thinking_steps,
                "brains":         result.brains,
                "decisions":      [d.__dict__ for d in result.trade_decisions],
                "trades_queued":  len(queued_ids),
                "queued_ids":     queued_ids,
                "orientation":    result.orientation,
                "reflection":     result.reflection,
                "warnings":       wm.warnings,
                "memory_count":   memory.stats()["total_memories"],
                "model":          self.model,
            }
            self._save_state()
            _synapse_write(
                perception.macro.regime,
                [(d.ticker, d.action) for d in result.trade_decisions],
                result.reflection[:150])
            logger.info(f"Brain cycle {cycle_id} complete — "
                        f"{len(queued_ids)} trade(s) queued")

        except Exception as e:
            logger.exception(f"Brain cycle {cycle_id} failed")
            self.last_cycle = {
                "error":          str(e),
                "cycle_id":       cycle_id,
                "cycle_count":    self.cycle_count,
                "timestamp":      datetime.now().isoformat(),
                "thinking_steps": wm.reasoning_steps,
                "model":          self.model,
            }
            self._save_state()
        finally:
            self.thinking = False
            self._cycle_lock.release()

    def _extract_tags(self, perception, result) -> list:
        tags = [perception.macro.regime]
        try:
            vix = float(perception.macro.vix) if perception.macro.vix else None
        except (TypeError, ValueError):
            vix = None
        if vix is not None and vix > 25:
            tags.append("high-vix")
        if vix is not None and vix < 15:
            tags.append("low-vix")
        if perception.regime_shift:
            tags.append("regime-shift")
        return tags

    # ── persistence ──────────────────────────────────────────────────────

    def _save_state(self):
        self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.STATE_FILE.write_text(
            json.dumps(self.last_cycle, indent=2, default=str), encoding="utf-8")

    def _load_state(self):
        if self.STATE_FILE.exists():
            try:
                self.last_cycle = json.loads(self.STATE_FILE.read_text(encoding="utf-8"))
                self.cycle_count = self.last_cycle.get("cycle_count", 0)
            except Exception:
                self.last_cycle = {}


# Global daemon instance (singleton)
_brain: BrainDaemon | None = None


def get_brain(model: str = "qwen2.5-coder:7b") -> BrainDaemon:
    global _brain
    if _brain is None:
        _brain = BrainDaemon(model=model)
    return _brain


def peek_brain() -> BrainDaemon | None:
    """Return the daemon if it has been created, without creating it."""
    return _brain
