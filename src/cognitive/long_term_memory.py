"""
long_term_memory.py — ARIA Long-Term Memory
Persists trade decisions, patterns, and session history across ARIA sessions.
Saved to data/memory/long_term_memory.json
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


class LongTermMemory:
    """Persistent cross-session memory for ARIA."""

    MAX_DECISIONS = 200
    MAX_PATTERNS = 100
    MAX_SESSIONS = 50

    def __init__(self, base_path: str = None):
        if base_path is None:
            base_path = Path(__file__).parent.parent.parent
        self.base_path = Path(base_path)
        self._path = self.base_path / "data" / "memory" / "long_term_memory.json"
        self._data: dict = {
            "decisions": [],
            "session_summaries": [],
            "learned_patterns": [],
            "ticker_notes": {},
        }
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self._data.update(loaded)
            except Exception:
                pass

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, default=str)
        except Exception as e:
            print(f"[LTM] Save failed: {e}")

    # ── Write methods ─────────────────────────────────────────────────────────

    def record_decision_discussed(
        self,
        ticker: str,
        direction: str,
        confidence: str,
        reasoning: str,
        regime: Optional[str] = None,
    ):
        """Log a trade thesis discussed in conversation."""
        entry = {
            "ts": datetime.now().isoformat(),
            "ticker": ticker.upper(),
            "direction": direction,
            "confidence": confidence,
            "reasoning": reasoning[:500],
            "regime": regime or "unknown",
        }
        self._data["decisions"].append(entry)
        # Trim to max
        if len(self._data["decisions"]) > self.MAX_DECISIONS:
            self._data["decisions"] = self._data["decisions"][-self.MAX_DECISIONS:]
        self._save()

    def record_session_summary(self, summary: str, key_tickers: list = None):
        """Summarise a completed conversation session."""
        entry = {
            "ts": datetime.now().isoformat(),
            "summary": summary[:800],
            "key_tickers": key_tickers or [],
        }
        self._data["session_summaries"].append(entry)
        if len(self._data["session_summaries"]) > self.MAX_SESSIONS:
            self._data["session_summaries"] = self._data["session_summaries"][-self.MAX_SESSIONS:]
        self._save()

    def record_pattern(self, pattern: str, evidence: str = ""):
        """Record a recurring market pattern ARIA has observed."""
        entry = {
            "ts": datetime.now().isoformat(),
            "pattern": pattern[:300],
            "evidence": evidence[:300],
        }
        self._data["learned_patterns"].append(entry)
        if len(self._data["learned_patterns"]) > self.MAX_PATTERNS:
            self._data["learned_patterns"] = self._data["learned_patterns"][-self.MAX_PATTERNS:]
        self._save()

    def add_ticker_note(self, ticker: str, note: str):
        """Attach a persistent note to a specific ticker."""
        ticker = ticker.upper()
        if ticker not in self._data["ticker_notes"]:
            self._data["ticker_notes"][ticker] = []
        self._data["ticker_notes"][ticker].append({
            "ts": datetime.now().isoformat(),
            "note": note[:300],
        })
        # Keep last 10 notes per ticker
        self._data["ticker_notes"][ticker] = self._data["ticker_notes"][ticker][-10:]
        self._save()

    # ── Read methods ──────────────────────────────────────────────────────────

    def recent_decisions(self, n: int = 10) -> list:
        return self._data["decisions"][-n:]

    def recent_sessions(self, n: int = 5) -> list:
        return self._data["session_summaries"][-n:]

    def ticker_history(self, ticker: str) -> list:
        ticker = ticker.upper()
        decisions = [d for d in self._data["decisions"] if d.get("ticker") == ticker]
        notes = self._data["ticker_notes"].get(ticker, [])
        return {
            "decisions": decisions[-5:],
            "notes": notes,
        }

    def to_context(self) -> str:
        """Format long-term memory for injection into the system prompt."""
        lines = ["=== ARIA LONG-TERM MEMORY ==="]

        # Recent session summaries
        sessions = self.recent_sessions(3)
        if sessions:
            lines.append("\nRECENT SESSION SUMMARIES:")
            for s in reversed(sessions):
                ts = s["ts"][:10]
                lines.append(f"  [{ts}] {s['summary'][:200]}")
                if s.get("key_tickers"):
                    lines.append(f"    Tickers discussed: {', '.join(s['key_tickers'])}")

        # Recent decisions
        decisions = self.recent_decisions(8)
        if decisions:
            lines.append("\nRECENT TRADE THESES DISCUSSED:")
            for d in reversed(decisions):
                ts = d["ts"][:10]
                lines.append(
                    f"  [{ts}] {d['ticker']} — {d['direction'].upper()} — "
                    f"{d['confidence']} conviction — Regime: {d['regime']}"
                )
                lines.append(f"    Reasoning: {d['reasoning'][:150]}")

        # Learned patterns
        patterns = self._data["learned_patterns"][-5:]
        if patterns:
            lines.append("\nLEARNED PATTERNS:")
            for p in patterns:
                lines.append(f"  • {p['pattern']}")

        if len(lines) == 1:
            lines.append("\n  [No long-term memory yet — this is ARIA's first session]")

        return "\n".join(lines)
