"""
ARIA Working Memory
Tracks session state and injects relevant context into the system prompt.
"""

import re
from datetime import datetime
from typing import List, Optional


class WorkingMemory:
    def __init__(self):
        self.past_decisions: List[dict] = []
        self.learned_patterns: List[str] = []
        self.macro_regime: Optional[str] = None
        self.tickers_in_focus: List[str] = []
        self.open_questions: List[str] = []
        self.trade_journal: List[dict] = []
        self.risk_events: List[str] = []
        self.session_notes: List[str] = []
        self.loaded_at = datetime.now().isoformat()

    def load_from_vault(self, long_term: str, recent: str):
        """Parse vault memory files and populate working memory."""
        if long_term:
            self._parse_long_term(long_term)
        if recent:
            self._parse_recent(recent)

    def _parse_long_term(self, content: str):
        """Extract structured data from long-term memory markdown."""
        lines = content.split("\n")
        section = None
        for line in lines:
            line = line.strip()
            if "## past decisions" in line.lower():
                section = "decisions"
            elif "## patterns" in line.lower() or "## learned" in line.lower():
                section = "patterns"
            elif "## macro" in line.lower():
                section = "macro"
            elif "## risk" in line.lower():
                section = "risk"
            elif line.startswith("- ") and section == "decisions":
                self.past_decisions.append({"note": line[2:], "source": "long_term"})
            elif line.startswith("- ") and section == "patterns":
                self.learned_patterns.append(line[2:])
            elif line.startswith("**regime**") or "regime:" in line.lower():
                self.macro_regime = line.split(":", 1)[-1].strip().strip("*")
            elif line.startswith("- ") and section == "risk":
                self.risk_events.append(line[2:])

    def _parse_recent(self, content: str):
        """Extract recent session context."""
        lines = content.split("\n")
        section = None
        for line in lines:
            line = line.strip()
            if "## tickers" in line.lower() or "## watchlist" in line.lower():
                section = "tickers"
            elif "## open" in line.lower():
                section = "open"
            elif "## notes" in line.lower():
                section = "notes"
            elif line.startswith("- ") and section == "tickers":
                ticker = re.search(r"\b[A-Z]{1,5}\b", line[2:])
                if ticker and ticker.group() not in self.tickers_in_focus:
                    self.tickers_in_focus.append(ticker.group())
            elif line.startswith("- ") and section == "open":
                self.open_questions.append(line[2:])
            elif line.startswith("- ") and section == "notes":
                self.session_notes.append(line[2:])

    def update_from_aria_response(self, response: str):
        """Extract learnings from ARIA's response text."""
        # Extract tickers mentioned
        tickers = re.findall(r'\b([A-Z]{1,5})\b(?=\s+(?:signal|trade|setup|support|resistance|looks|is))', response)
        for t in tickers:
            if t not in self.tickers_in_focus and t not in ["ARIA", "NLP", "ML", "RSI", "MACD", "ATR", "GDP", "CPI"]:
                self.tickers_in_focus.append(t)

        # Extract macro regime mentions
        regime_patterns = [
            r"(risk.on|risk.off|stagflation|soft landing|hard landing|rate hike|rate cut|QT|QE)",
        ]
        for pat in regime_patterns:
            match = re.search(pat, response, re.IGNORECASE)
            if match and not self.macro_regime:
                self.macro_regime = match.group(1)

        # Detect open questions or unresolved items
        if "need more data" in response.lower() or "missing" in response.lower():
            if len(self.open_questions) < 10:
                self.open_questions.append(f"Data gap noted: {response[:80]}...")

    def add_decision(self, ticker: str, direction: str, rationale: str, confidence: str):
        """Manually log a trade decision."""
        self.past_decisions.append({
            "ticker": ticker,
            "direction": direction,
            "rationale": rationale,
            "confidence": confidence,
            "timestamp": datetime.now().isoformat(),
            "source": "session",
        })

    def to_prompt_injection(self) -> str:
        """Format working memory for injection into system prompt."""
        sections = []

        if self.past_decisions:
            recent = self.past_decisions[-5:]  # last 5 decisions only
            lines = "\n".join(
                f"  - {d.get('ticker', '?')} | {d.get('direction', '?')} | "
                f"{d.get('rationale', d.get('note', ''))[:80]} | conf: {d.get('confidence', '?')}"
                for d in recent
            )
            sections.append(f"VAULT MEMORY — PAST DECISIONS (most recent 5):\n{lines}")

        if self.learned_patterns:
            lines = "\n".join(f"  - {p}" for p in self.learned_patterns[:8])
            sections.append(f"VAULT MEMORY — LEARNED PATTERNS:\n{lines}")

        if self.macro_regime:
            sections.append(f"VAULT MEMORY — MACRO REGIME: {self.macro_regime}")

        if self.tickers_in_focus:
            sections.append(f"VAULT MEMORY — TICKERS IN FOCUS: {', '.join(self.tickers_in_focus[:10])}")

        if self.open_questions:
            lines = "\n".join(f"  - {q}" for q in self.open_questions[:5])
            sections.append(f"VAULT MEMORY — OPEN QUESTIONS:\n{lines}")

        if self.risk_events:
            lines = "\n".join(f"  - {r}" for r in self.risk_events[:5])
            sections.append(f"VAULT MEMORY — KNOWN RISK EVENTS:\n{lines}")

        if not sections:
            return ""

        header = "=" * 60
        return f"{header}\nWORKING MEMORY (from Obsidian DigitalBrain vault)\n{header}\n" + \
               "\n\n".join(sections) + f"\n{header}"

    def to_dict(self) -> dict:
        """Serialise for vault storage."""
        return {
            "past_decisions": self.past_decisions,
            "learned_patterns": self.learned_patterns,
            "macro_regime": self.macro_regime,
            "tickers_in_focus": self.tickers_in_focus,
            "open_questions": self.open_questions,
            "trade_journal": self.trade_journal,
            "risk_events": self.risk_events,
            "session_notes": self.session_notes,
            "loaded_at": self.loaded_at,
        }
