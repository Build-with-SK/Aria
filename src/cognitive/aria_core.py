"""
aria_core.py — THE BRAIN
ARIA's main cognitive engine. Connects to the Claude API, manages conversation
history, injects working/long-term memory, dispatches tool calls, and runs the
Vertus-style cognitive debate.
"""

import os
import re
import sys
from pathlib import Path
from typing import Optional, List, Dict

try:
    import anthropic
except ImportError:
    anthropic = None

from .working_memory import WorkingMemory
from .long_term_memory import LongTermMemory
from .metacognition import MetacognitionEngine
from .contradiction_engine import ContradictionEngine
from .tool_dispatcher import ToolDispatcher
from .obsidian_export import ObsidianExporter


MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 2048
MAX_TOOL_ITERATIONS = 6


SYSTEM_PROMPT_TEMPLATE = """You are ARIA (Adaptive Reasoning Intelligence Architecture), the cognitive AI layer
of a professional Trading Intelligence System.

Your character:
- You think and speak like a veteran quantitative hedge fund analyst with 15+ years experience
- You have complete knowledge of the TIS's current state (injected below as working memory)
- You are HONEST about uncertainty — you never pretend confidence you don't have
- You are RISK-FIRST — you always consider downside before upside
- You question your own conclusions (metacognition is built into how you think)
- You cite specific signals and scores when making claims
- You flag contradictions — when signals disagree, you explain why rather than averaging
- You do NOT recommend automatic trade execution — all outputs are research signals only
- You speak conversationally but with precision when it matters
- You push back when the user is being emotional or chasing performance
- You remember past conversations and reference them naturally when relevant

Your cognitive toolkit (use the provided tools):
- get_ticker_data: pull signal score, ML prediction, strategy breakdown for any ticker
- run_scenario: run GS-Quant-style risk scenarios (rate shock, equity crash, vol spike, credit widening, soft landing, stagflation)
- get_options_greeks: Black-Scholes pricing and Greeks for any option
- run_cognitive_debate: trigger the full Bull/Bear/Risk/Quant/Macro debate on a ticker
- get_political_data: congressional trading / political intelligence for a ticker
- check_contradictions: scan for signal conflicts before giving high-conviction views
- refresh_tis: re-run the full pipeline for fresh data (slow — only if explicitly asked)

When asked about a trade, you ALWAYS:
1. State the current signal score and direction
2. Check for contradictions with macro/ML/political signals (use check_contradictions tool)
3. Give your calibrated view with confidence level (High/Medium/Low)
4. State what would change your view
5. Remind that this is research only — not a trade execution recommendation

{WORKING_MEMORY}

{LONG_TERM_MEMORY}
"""


class AriaCore:
    """
    The main ARIA brain. Wraps the Claude API with TIS-aware context,
    tool dispatch, and the Vertus-style cognitive debate.
    """

    def __init__(self, base_path: str = None, api_key: str = None, vault_path: str = None, auto_export_obsidian: bool = True):
        if base_path is None:
            base_path = Path(__file__).parent.parent.parent
        self.base_path = Path(base_path)

        if anthropic is None:
            raise ImportError(
                "The 'anthropic' package is required. Install it with: pip install anthropic"
            )

        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

        self.working_memory = WorkingMemory(str(self.base_path))
        self.long_term_memory = LongTermMemory(str(self.base_path))
        self.metacognition = MetacognitionEngine()
        self.contradiction_engine = ContradictionEngine()
        self.tool_dispatcher = ToolDispatcher(str(self.base_path))
        self.obsidian = ObsidianExporter(str(self.base_path), vault_path=vault_path)
        self.auto_export_obsidian = auto_export_obsidian

        self.conversation_history: List[Dict] = []
        self._session_tickers_discussed: set = set()

        self.working_memory.refresh()

    # ── System prompt construction ──────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        return SYSTEM_PROMPT_TEMPLATE.format(
            WORKING_MEMORY=self.working_memory.to_context(),
            LONG_TERM_MEMORY=self.long_term_memory.to_context(),
        )

    # ── Ticker / intent extraction ───────────────────────────────────────────

    def _extract_ticker(self, text: str) -> Optional[str]:
        """Naive ticker extraction: look for 1-5 uppercase letter tokens that
        match known tickers in working memory, falling back to regex."""
        known_tickers = set(self.working_memory.all_signals().keys())
        words = re.findall(r"\b[A-Za-z]{1,5}\b", text)
        for w in words:
            if w.upper() in known_tickers:
                return w.upper()
        # Fallback: any all-caps 2-5 letter token
        for w in words:
            if w.isupper() and 2 <= len(w) <= 5:
                return w
        return None

    def _wants_debate(self, text: str) -> bool:
        triggers = ["debate", "devil's advocate", "devils advocate", "bull and bear",
                    "full analysis", "cognitive debate", "all perspectives"]
        lower = text.lower()
        return any(t in lower for t in triggers)

    def _wants_scenario(self, text: str) -> bool:
        triggers = ["scenario", "what if", "stress test", "shock", "spike", "crash"]
        lower = text.lower()
        return any(t in lower for t in triggers)

    # ── Tool loop ─────────────────────────────────────────────────────────────

    def _run_tool_loop(self, messages: List[Dict], system_prompt: str) -> str:
        """
        Sends messages to Claude with tool definitions, handles any tool_use
        blocks by dispatching to ToolDispatcher, and loops until Claude returns
        a final text response (or MAX_TOOL_ITERATIONS is hit).
        """
        tools = self.tool_dispatcher.get_tool_definitions()

        for _ in range(MAX_TOOL_ITERATIONS):
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                messages=messages,
                tools=tools,
            )

            if response.stop_reason != "tool_use":
                # Final text response
                text_parts = [b.text for b in response.content if b.type == "text"]
                return "\n".join(text_parts).strip()

            # Handle tool use — append assistant turn, then tool results
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result_text = self.tool_dispatcher.dispatch(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    })

            messages.append({"role": "user", "content": tool_results})

        return "[ARIA hit max tool iterations — try rephrasing your question.]"

    # ── Streaming version ─────────────────────────────────────────────────────

    def ask_streaming(self, user_message: str):
        """
        Generator that yields text chunks as they stream from Claude.
        Handles tool calls transparently (tool calls happen non-streamed internally,
        final response streams token by token).
        """
        self.working_memory.refresh()
        system_prompt = self._build_system_prompt()

        ticker = self._extract_ticker(user_message)
        if ticker:
            self._session_tickers_discussed.add(ticker)

        self.conversation_history.append({"role": "user", "content": user_message})
        messages = list(self.conversation_history)
        tools = self.tool_dispatcher.get_tool_definitions()

        # First, resolve any tool calls non-streamed (tool loop)
        for _ in range(MAX_TOOL_ITERATIONS):
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                messages=messages,
                tools=tools,
            )

            if response.stop_reason != "tool_use":
                final_text = "\n".join(b.text for b in response.content if b.type == "text").strip()
                self.conversation_history.append({"role": "assistant", "content": final_text})
                # Yield it as one chunk if we're not truly streaming the final call
                yield final_text
                return

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result_text = self.tool_dispatcher.dispatch(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    })
            messages.append({"role": "user", "content": tool_results})

        yield "[ARIA hit max tool iterations — try rephrasing your question.]"

    # ── Main entry point (non-streaming) ─────────────────────────────────────

    def ask(self, user_message: str) -> str:
        """
        Main conversational entry point. Refreshes memory, detects intent,
        runs the appropriate flow (normal chat / debate / scenario), and
        records the interaction in long-term memory.
        """
        self.working_memory.refresh()
        system_prompt = self._build_system_prompt()

        ticker = self._extract_ticker(user_message)
        if ticker:
            self._session_tickers_discussed.add(ticker)

        if ticker and self._wants_debate(user_message):
            return self.debate_ticker(ticker)

        self.conversation_history.append({"role": "user", "content": user_message})
        messages = list(self.conversation_history)

        response_text = self._run_tool_loop(messages, system_prompt)
        self.conversation_history.append({"role": "assistant", "content": response_text})

        return response_text

    # ── Cognitive debate orchestration ───────────────────────────────────────

    def debate_ticker(self, ticker: str) -> str:
        """
        Runs the full Vertus-style five-perspective cognitive debate on a ticker.
        Makes 6 sequential Claude API calls: bull, bear, risk, quant, macro, synthesis.
        """
        ticker = ticker.upper()
        self._session_tickers_discussed.add(ticker)

        ctx = self.working_memory.get_ticker_context(ticker)
        if not ctx.get("signal"):
            return f"I don't have signal data for {ticker} — it may not be in the tracked universe of 82 tickers."

        context_str = (
            f"Signal: {ctx.get('signal')}\n"
            f"Political: {ctx.get('political')}\n"
            f"Trade candidate: {ctx.get('trade_candidate')}"
        )
        regime = self.working_memory.get_meta().get("regime", "Unknown")
        score = ctx.get("signal", {}).get("score")

        def call(prompt: str) -> str:
            resp = self.client.messages.create(
                model=MODEL,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            return "\n".join(b.text for b in resp.content if b.type == "text").strip()

        bull = call(self.metacognition.bull_analyst_prompt(ticker, context_str))
        bear = call(self.metacognition.bear_analyst_prompt(ticker, context_str))
        risk = call(self.metacognition.risk_officer_prompt(ticker, context_str))
        quant = call(self.metacognition.quant_view_prompt(ticker, context_str))
        macro = call(self.metacognition.macro_strategist_prompt(ticker, context_str, regime))

        # Contradiction check feeds into synthesis
        contradiction_report = self.contradiction_engine.analyse_ticker(ticker, self.working_memory)

        synthesis_prompt = self.metacognition.synthesis_prompt(ticker, bull, bear, risk, quant, macro, score)
        synthesis_prompt += f"\n\n=== CONTRADICTION CHECK ===\n{contradiction_report.to_context()}"
        synthesis = call(synthesis_prompt)

        full_debate = (
            f"=== ARIA COGNITIVE DEBATE: {ticker} ===\n\n"
            f"[BULL ANALYST]\n{bull}\n\n"
            f"[BEAR ANALYST]\n{bear}\n\n"
            f"[RISK OFFICER]\n{risk}\n\n"
            f"[QUANT ANALYST]\n{quant}\n\n"
            f"[MACRO STRATEGIST]\n{macro}\n\n"
            f"[CONTRADICTION CHECK]\n{contradiction_report.to_context()}\n\n"
            f"[SYNTHESIS]\n{synthesis}\n"
        )

        # Record in long-term memory
        direction_match = re.search(r"DIRECTION[:\s]+(\w+)", synthesis, re.IGNORECASE)
        conviction_match = re.search(r"CONVICTION[:\s]+(\w+)", synthesis, re.IGNORECASE)
        direction = direction_match.group(1) if direction_match else "NEUTRAL"
        conviction = conviction_match.group(1) if conviction_match else "MEDIUM"

        self.long_term_memory.record_decision_discussed(
            ticker=ticker,
            direction=direction,
            confidence=conviction,
            reasoning=synthesis[:400],
            regime=regime,
        )

        # Add a condensed version to conversation history (full debate is long)
        self.conversation_history.append({
            "role": "user",
            "content": f"[User requested cognitive debate on {ticker}]"
        })
        self.conversation_history.append({
            "role": "assistant",
            "content": f"[Ran full cognitive debate on {ticker}. Synthesis: {synthesis[:300]}]"
        })

        if self.auto_export_obsidian:
            export_result = self.obsidian.export_debate(ticker, full_debate, conviction=conviction, direction=direction)
            full_debate += "\n" + self.obsidian.status_message(export_result)

        return full_debate

    # ── Scenario parsing from natural language ───────────────────────────────

    def run_scenario_from_text(self, user_message: str) -> str:
        """Parses a natural-language scenario request and runs it via the tool dispatcher."""
        from src.gs_quant_bridge.scenario_engine import ScenarioEngine

        engine = ScenarioEngine(str(self.base_path))
        lower = user_message.lower()

        bps_match = re.search(r"([+-]?\d+)\s*bps", lower)
        pct_match = re.search(r"([+-]?\d+)\s*%", lower)
        vix_match = re.search(r"vix.*?(\d+)", lower)

        if "rate" in lower:
            bps = int(bps_match.group(1)) if bps_match else 100
            result = engine.rate_shock(bps)
        elif "crash" in lower or "equity" in lower or "correction" in lower:
            pct = int(pct_match.group(1)) if pct_match else -20
            result = engine.equity_crash(pct)
        elif "vol" in lower or "vix" in lower:
            vix_move = int(vix_match.group(1)) if vix_match else 20
            result = engine.vol_spike(vix_move)
        elif "credit" in lower:
            bps = int(bps_match.group(1)) if bps_match else 200
            result = engine.credit_widening(bps)
        elif "soft landing" in lower:
            result = engine.soft_landing()
        elif "stagflation" in lower:
            result = engine.stagflation()
        else:
            return engine.format_all_scenarios()

        report = engine.format_report(result)

        if self.auto_export_obsidian:
            export_result = self.obsidian.export_scenario(result.name, report)
            report += "\n" + self.obsidian.status_message(export_result)

        return report

    # ── Session management ────────────────────────────────────────────────────

    def end_session(self, summary: str = None):
        """Call at the end of a chat session to persist a summary to long-term memory."""
        if not summary:
            if not self.conversation_history:
                return
            # Generate a quick summary via Claude
            transcript = "\n".join(
                f"{m['role']}: {m['content'] if isinstance(m['content'], str) else '[tool interaction]'}"
                for m in self.conversation_history[-20:]
            )
            try:
                resp = self.client.messages.create(
                    model=MODEL,
                    max_tokens=200,
                    messages=[{
                        "role": "user",
                        "content": f"Summarise this trading conversation in 2-3 sentences for future reference:\n\n{transcript}"
                    }],
                )
                summary = "\n".join(b.text for b in resp.content if b.type == "text").strip()
            except Exception:
                summary = "Session ended — summary generation failed."

        self.long_term_memory.record_session_summary(
            summary=summary,
            key_tickers=list(self._session_tickers_discussed),
        )

        if self.auto_export_obsidian:
            export_result = self.obsidian.export_session(summary, list(self._session_tickers_discussed))
            return self.obsidian.status_message(export_result)
        return None
