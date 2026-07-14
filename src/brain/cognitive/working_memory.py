"""
src/brain/cognitive/working_memory.py
=====================================
WORKING MEMORY — what the brain is currently thinking about.
A scratchpad that lives only for the duration of one reasoning cycle.
Reset at the start of every cycle; only its summary is persisted
to long-term memory afterwards.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class WorkingMemory:
    cycle_id:           str                 # uuid — unique per reasoning cycle
    started_at:         datetime
    focus_tickers:      list = field(default_factory=list)   # actively considered tickers
    current_hypothesis: str  = ""           # what the brain currently thinks is happening
    reasoning_steps:    list = field(default_factory=list)   # [{step, thought, conclusion}]
    candidate_trades:   list = field(default_factory=list)   # trades considered before committing
    context_summary:    str  = ""           # compressed version of what the brain knows
    warnings:           list = field(default_factory=list)   # reasons to be cautious

    def add_thought(self, step: str, thought: str, conclusion: str):
        """Append one step of internal monologue."""
        self.reasoning_steps.append({
            "step": step,
            "thought": thought,
            "conclusion": conclusion,
            "at": datetime.now().isoformat(),
        })

    def summarize(self) -> str:
        """Compact text summary of the current working state."""
        lines = [f"Cycle {self.cycle_id} started {self.started_at.isoformat()}"]
        if self.current_hypothesis:
            lines.append(f"Hypothesis: {self.current_hypothesis[:200]}")
        if self.focus_tickers:
            lines.append(f"Focus: {', '.join(self.focus_tickers)}")
        for s in self.reasoning_steps:
            concl = s["conclusion"] or s["thought"][:120]
            lines.append(f"[{s['step']}] {concl}")
        if self.warnings:
            lines.append(f"Warnings: {'; '.join(self.warnings)}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """JSON-serializable view for the API."""
        return {
            "cycle_id":           self.cycle_id,
            "started_at":         self.started_at.isoformat(),
            "focus_tickers":      self.focus_tickers,
            "current_hypothesis": self.current_hypothesis,
            "reasoning_steps":    self.reasoning_steps,
            "candidate_trades":   self.candidate_trades,
            "context_summary":    self.context_summary,
            "warnings":           self.warnings,
        }
