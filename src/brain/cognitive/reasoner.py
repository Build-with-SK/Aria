"""
src/brain/cognitive/reasoner.py
===============================
REASONING LOOP — the core of the brain.

The brain thinks in explicit steps: not a single LLM call but a loop
that builds understanding incrementally. Each step uses Ollama to
produce a structured thought, then feeds that thought into the next step.

Thinking steps per cycle:
1. ORIENT    — "What is the overall market situation right now?"
2. FOCUS     — "Which assets deserve attention and why?"
3. RECALL    — "Have I seen this before? What happened?"
4. ANALYSE   — "For each focused asset: what's the case for and against?"
5. DECIDE    — "What should I do? Am I confident enough to propose a trade?"
6. REFLECT   — "What did I miss? What could go wrong?"
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import dataclass, field

from .perception import PerceptionSnapshot
from .working_memory import WorkingMemory

logger = logging.getLogger(__name__)

OLLAMA_BASE = "http://localhost:11434"

VALID_ACTIONS = {"PROPOSE_BUY", "PROPOSE_SELL", "MONITOR", "SKIP"}

# ── Frontier-consult config (shared with the API) ────────────────────────────
from pathlib import Path as _Path
_ROOT = _Path(__file__).parent.parent.parent.parent
_CONSULT_FILE = _ROOT / "data" / "brain_consult.json"
_CONSULT_LOG = _ROOT / "data" / "brain_consult_log.jsonl"

_CONSULT_DEFAULTS = {
    "enabled": False,
    "frontier_model": "claude-sonnet-4-6",
    "daily_call_cap": 120,          # hard cap so a loop can't run up a bill
}


def _consult_config() -> dict:
    cfg = dict(_CONSULT_DEFAULTS)
    if _CONSULT_FILE.exists():
        try:
            cfg.update(json.loads(_CONSULT_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def set_consult_config(updates: dict) -> dict:
    cfg = _consult_config()
    for k, v in updates.items():
        if k in _CONSULT_DEFAULTS:
            cfg[k] = v
    _CONSULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONSULT_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def _consult_calls_today() -> int:
    from datetime import date
    if not _CONSULT_LOG.exists():
        return 0
    today, n = date.today().isoformat(), 0
    for line in _CONSULT_LOG.read_text(encoding="utf-8").splitlines():
        try:
            if json.loads(line).get("at", "")[:10] == today:
                n += 1
        except Exception:
            continue
    return n


def _consult_budget_left(cfg: dict) -> bool:
    return _consult_calls_today() < cfg.get("daily_call_cap", 120)


def _consult_log(step: str):
    from datetime import datetime
    _CONSULT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _CONSULT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(), "step": step}) + "\n")


def consult_status() -> dict:
    cfg = _consult_config()
    return {**cfg, "calls_today": _consult_calls_today(),
            "budget_left": _consult_budget_left(cfg)}


@dataclass
class TradeDecision:
    ticker:     str
    action:     str      # PROPOSE_BUY | PROPOSE_SELL | MONITOR | SKIP
    conviction: int      # 0-100
    reason:     str


@dataclass
class ReasoningResult:
    cycle_id:        str
    thinking_steps:  list                          # [{step, thought, conclusion}]
    focus_tickers:   list = field(default_factory=list)
    trade_decisions: list = field(default_factory=list)   # list[TradeDecision]
    orientation:     str  = ""
    reflection:      str  = ""
    full_monologue:  str  = ""
    brains:          dict = field(default_factory=dict)   # step -> local | frontier


class ReasoningLoop:
    """Multi-step chain-of-thought reasoning using a local LLM."""

    # Steps that benefit most from a stronger brain when consult is enabled.
    HARD_STEPS = ("ORIENT", "ANALYSE", "DECIDE", "REFLECT")

    def __init__(self, model: str = "qwen2.5-coder:7b", vault=None,
                 peer_context: str = ""):
        self.model = model
        self.vault = vault   # optional VaultIndex — the user's Obsidian knowledge
        self.peer_context = peer_context   # lessons from peer minds (e.g. ATLAS)
        self.brains: dict = {}   # step -> "local" | "frontier" (for the UI)
        self._consult = _consult_config()

    def run_cycle(
        self,
        perception: PerceptionSnapshot,
        memory,                       # LongTermMemory (duck-typed; may be None)
        wm: WorkingMemory,
    ) -> ReasoningResult:
        """Runs the full 6-step thinking cycle."""

        # Step 1: ORIENT — overall market situation
        orientation = self._think(
            f"""You are ARIA, a trading intelligence brain.
CURRENT MARKET STATE:
{perception.macro}
SIGNAL SUMMARY: {len(perception.signals)} assets.
Top alerts: {perception.alerts or 'none'}
Regime: {perception.macro.regime}

Step 1 — ORIENTATION: In 3-4 sentences, describe the overall market character right now.
What is the dominant theme? What is the risk environment? Be specific with numbers.""",
            step="ORIENT",
        )
        wm.add_thought("ORIENT", orientation, self._extract_conclusion(orientation))
        wm.current_hypothesis = orientation

        # Step 2: FOCUS — decide which tickers to analyse deeply
        top_bull = [s.ticker for s in perception.top_opportunities if s.composite_score > 0][:8]
        top_bear = [s.ticker for s in perception.top_opportunities if s.composite_score < 0][:8]
        focus_response = self._think(
            f"""Given this market orientation:
{orientation}

Top bullish signals: {top_bull}
Top bearish signals: {top_bear}
High-priority alerts: {perception.alerts}

Step 2 — FOCUS: List exactly 3-5 tickers worth deep analysis this cycle.
For each, one sentence why it's interesting. Format: TICKER: reason""",
            step="FOCUS",
        )
        focus_tickers = self._parse_tickers(focus_response, valid=set(perception.signals))
        if not focus_tickers:
            # Fall back to alerts / top opportunities so the cycle never stalls
            focus_tickers = (perception.alerts or
                             [s.ticker for s in perception.top_opportunities])[:4]
        wm.focus_tickers = focus_tickers
        wm.add_thought("FOCUS", focus_response, f"Focusing on: {focus_tickers}")

        # Step 3: RECALL — search long-term memory for similar situations
        past_memories = []
        if memory is not None:
            try:
                recall_query = (f"{perception.macro.regime} regime, VIX {perception.macro.vix}, "
                                f"tickers: {focus_tickers}")
                past_memories = memory.recall(recall_query, n=3)
            except Exception as e:
                logger.warning(f"RECALL failed: {e}")
                wm.warnings.append(f"Memory recall unavailable: {e}")
        recall_context = self._format_memories(past_memories)

        # Also consult the user's Obsidian vault — their skills and knowledge
        if self.vault is not None:
            try:
                vault_ctx = self.vault.context_for(
                    f"trading strategy {perception.macro.regime} {' '.join(focus_tickers)}",
                    n=2, max_chars=600)
                if vault_ctx:
                    recall_context += f"\n{vault_ctx}"
            except Exception as e:
                logger.warning(f"Vault recall failed: {e}")

        if self.peer_context:
            recall_context += f"\nFROM MY PEER ATLAS (the user's personal AI):\n{self.peer_context[:600]}"

        wm.add_thought("RECALL", recall_context,
                       f"Found {len(past_memories)} relevant past cycles"
                       + (" + vault knowledge" if self.vault else "")
                       + (" + peer lessons" if self.peer_context else ""))

        # Step 4: ANALYSE — deep dive on each focused ticker (cap 4 for token budget)
        analyses = {}
        for ticker in focus_tickers[:4]:
            sig = perception.signals.get(ticker)
            ml  = perception.ml.get(ticker)
            if not sig:
                continue
            ml_desc = (f"{ml.overall_signal} ({ml.overall_bullish:.0%} bullish)"
                       if ml else "N/A")
            analysis = self._think(
                f"""Analysing {ticker}:
Signal score: {sig.composite_score:+.1f} | Action: {sig.action} | Confidence: {sig.confidence}
Trend: {sig.trend_score:.0f} | Momentum: {sig.momentum_score:.0f} | Volatility: {sig.volatility_score:.0f}
ML: {ml_desc}
Drivers: {sig.drivers[:2]}
Risks: {sig.risks[:2]}
Stop: ${sig.stop_loss:.2f} | Target: ${sig.take_profit:.2f} | Current: ${sig.current_price:.2f}
Past context: {recall_context[:300]}

Step 4 — ANALYSE {ticker}:
- Is the signal credible? Do technicals and ML agree?
- What's the key risk that could invalidate this?
- Is this a good setup relative to what I've seen before?
- Confidence: HIGH / MEDIUM / LOW. One sentence each.""",
                step=f"ANALYSE_{ticker}",
            )
            analyses[ticker] = analysis
            wm.add_thought(f"ANALYSE_{ticker}", analysis,
                           self._extract_conclusion(analysis))

        # Step 5: DECIDE — commit to action or hold
        decision = self._think(
            f"""Based on all analysis:
Orientation: {wm.current_hypothesis[:200]}
Analyses completed: {list(analyses.keys())}
Past memory context: {recall_context[:200]}

Step 5 — DECIDE: For each analysed ticker, state your decision:
Format strictly as:
TICKER | ACTION | CONVICTION(0-100) | REASON(one sentence)

ACTION must be one of: PROPOSE_BUY | PROPOSE_SELL | MONITOR | SKIP
Only propose if CONVICTION >= 65 AND you have a clear thesis.
If macro regime is bearish/crisis, require CONVICTION >= 80 for buys.""",
            step="DECIDE",
        )
        trade_decisions = self._parse_decisions(decision, valid=set(perception.signals))
        wm.add_thought("DECIDE", decision,
                       f"Decisions: {[(d.ticker, d.action, d.conviction) for d in trade_decisions]}")

        # Step 6: REFLECT — sanity check
        reflect = self._think(
            f"""Decisions made: {decision}
Step 6 — REFLECT: In 2-3 sentences:
What is the biggest thing I might be wrong about?
Is there anything I haven't considered?
Am I being overconfident or underconfident?""",
            step="REFLECT",
        )
        wm.add_thought("REFLECT", reflect, self._extract_conclusion(reflect))

        return ReasoningResult(
            cycle_id=wm.cycle_id,
            thinking_steps=wm.reasoning_steps,
            focus_tickers=focus_tickers,
            trade_decisions=trade_decisions,
            orientation=orientation,
            reflection=reflect,
            full_monologue=wm.summarize(),
            brains=dict(self.brains),
        )

    # ── LLM plumbing ─────────────────────────────────────────────────────

    def _think(self, prompt: str, step: str) -> str:
        """One reasoning step. Routes hard steps to the frontier when the
        consult switch is on and budget remains; everything else stays local."""
        base = step.split("_")[0].upper()
        if (self._consult.get("enabled") and base in self.HARD_STEPS
                and _consult_budget_left(self._consult)):
            try:
                text = self._frontier(prompt)
                self.brains[step] = "frontier"
                _consult_log(step)
                return text
            except Exception as e:
                logger.warning(f"Frontier consult failed at {step} ({e}) — local fallback")
        self.brains[step] = "local"
        return self._local(prompt, step)

    def _local(self, prompt: str, step: str) -> str:
        """Single STANDARD-tier local call via the inference router
        (retries + circuit breaker). Raises RuntimeError when local
        inference is unavailable — the brain cycle handles that."""
        try:
            from src.inference.router import get_router
            return get_router().complete_with(
                "ollama", self.model,
                [{"role": "user", "content": prompt}],
                max_tokens=400, temperature=0.4, timeout=120).text
        except Exception as e:
            logger.error(f"Ollama call failed at step {step}: {e}")
            raise RuntimeError(f"Ollama unavailable during {step}: {e}") from e

    def _frontier(self, prompt: str) -> str:
        """One hard step on the frontier model via the router's DEEP path.
        Only the step prompt travels. The consult budget cap in _think is
        the routing policy; failure here falls back to _local."""
        from src.inference.router import get_router
        return get_router().complete_with(
            "anthropic", self._consult.get("frontier_model", "claude-sonnet-4-6"),
            [{"role": "user", "content": prompt}],
            system="You are ARIA, a sharp, precise trading intelligence brain. "
                   "Answer the step concisely with specific numbers. Research only.",
            max_tokens=500, timeout=60).text

    # ── parsing helpers ──────────────────────────────────────────────────

    def _parse_tickers(self, text: str, valid: set) -> list:
        """Extract known ticker symbols from FOCUS step output, in order."""
        candidates = re.findall(r'\b([A-Z][A-Z0-9\-\=\^\.]{0,9})\b', text)
        seen, out = set(), []
        for c in candidates:
            if c in valid and c not in seen:
                seen.add(c)
                out.append(c)
        return out[:5]

    def _parse_decisions(self, text: str, valid: set) -> list:
        """Parse DECIDE output lines: TICKER | ACTION | CONVICTION | REASON."""
        decisions = []
        for line in text.splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                continue
            ticker = parts[0].strip("*` ").upper()
            action = parts[1].strip("*` ").upper().replace(" ", "_")
            if ticker not in valid or action not in VALID_ACTIONS:
                continue
            m = re.search(r"\d{1,3}", parts[2])
            conviction = max(0, min(100, int(m.group()))) if m else 0
            reason = parts[3] if len(parts) > 3 else ""
            decisions.append(TradeDecision(ticker=ticker, action=action,
                                           conviction=conviction, reason=reason))
        return decisions

    def _format_memories(self, memories: list) -> str:
        if not memories:
            return "No relevant past experience found."
        return "\n".join(
            f"- [{m.timestamp.date()}] {m.cycle_summary[:100]}" for m in memories
        )

    def _extract_conclusion(self, text: str) -> str:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        return lines[-1][:100] if lines else ""
