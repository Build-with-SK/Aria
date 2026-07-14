"""
src/brain/cognitive/learner.py
==============================
LEARNER — the brain learns from outcomes.
After a trade is executed and time has passed, the learner fetches the
current price, computes P&L, and records the result into long-term
memory so future reasoning cycles can reference what actually happened.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent.parent
RECORDED_FILE = ROOT / "data" / "brain_memory" / "outcomes_recorded.json"

MIN_AGE_DAYS = 1.0   # only evaluate trades executed at least 1 day ago


class BrainLearner:
    def __init__(self, memory):
        self.memory = memory   # LongTermMemory

    def check_outcomes(self):
        """
        Reads the approval queue for executed trades older than 1 day,
        fetches the current price via yfinance, computes P&L, and calls
        memory.record_outcome(). Run at the start of every brain cycle.
        """
        try:
            from src.execution.approval_queue import ApprovalQueue
            trades = ApprovalQueue().get_all(limit=500)
        except Exception as e:
            logger.warning(f"Learner could not read approval queue: {e}")
            return

        recorded = self._load_recorded()
        now = datetime.utcnow()

        for t in trades:
            if t.status != "executed" or t.id in recorded:
                continue
            executed_at = t.executed_at or t.created_at
            try:
                age_days = (now - datetime.fromisoformat(executed_at)).total_seconds() / 86400
            except Exception:
                continue
            if age_days < MIN_AGE_DAYS:
                continue

            entry = t.fill_price or t.est_value / t.qty if t.qty else 0.0
            current = self._current_price(t.ticker)
            if not entry or not current:
                continue

            direction = 1 if t.side == "buy" else -1
            pnl = (current - entry) * t.qty * direction
            pnl_pct = (current - entry) / entry * 100 * direction
            outcome = {
                "ticker":        t.ticker,
                "side":          t.side,
                "entry_price":   round(entry, 4),
                "exit_price":    round(current, 4),
                "pnl":           round(pnl, 2),
                "pnl_pct":       round(pnl_pct, 2),
                "duration_days": round(age_days, 1),
            }
            try:
                self.memory.record_outcome(t.id, outcome)
                recorded.append(t.id)
                logger.info(f"Learner recorded outcome for {t.ticker} [{t.id}]: {pnl_pct:+.2f}%")
            except Exception as e:
                logger.warning(f"Learner failed to record outcome for {t.id}: {e}")

        self._save_recorded(recorded)

    def generate_lesson(self, memory) -> str:
        """
        Given a BrainMemory with an outcome, produce a one-sentence lesson,
        e.g. "In Goldilocks regime, buying AAPL resulted in +3.2% in 5 days."
        These lessons are injected into future RECALL steps.
        """
        if not memory.outcome:
            return ""
        lessons = []
        for _, o in memory.outcome.items():
            if not isinstance(o, dict) or "pnl_pct" not in o:
                continue
            lessons.append(
                f"In {memory.regime or 'unknown'} regime, "
                f"{'buying' if o.get('side') == 'buy' else 'selling'} {o.get('ticker')} "
                f"resulted in {o['pnl_pct']:+.1f}% in {o.get('duration_days', '?')} days."
            )
        return " ".join(lessons)

    # ── helpers ──────────────────────────────────────────────────────────

    def _current_price(self, ticker: str):
        """Latest close via yfinance; falls back to signals.json."""
        try:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period="1d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception as e:
            logger.debug(f"yfinance price fetch failed for {ticker}: {e}")
        try:
            signals = json.loads((ROOT / "data" / "signals.json").read_text(encoding="utf-8"))
            return signals.get(ticker, {}).get("current_price")
        except Exception:
            return None

    def _load_recorded(self) -> list:
        if RECORDED_FILE.exists():
            try:
                return json.loads(RECORDED_FILE.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def _save_recorded(self, ids: list):
        RECORDED_FILE.parent.mkdir(parents=True, exist_ok=True)
        RECORDED_FILE.write_text(json.dumps(ids, indent=2), encoding="utf-8")
