"""
ARIA Session — manages multi-turn conversation with Claude,
tool dispatch, memory injection, and Obsidian writes.
"""

import json
import base64
import asyncio
from datetime import datetime
from pathlib import Path

import sys
import anthropic

from core.system_prompt import build_system_prompt
from core.display import print_aria, print_tool_use, print_error
from tools.ticker import get_ticker_data
from tools.scenario import run_scenario
from tools.options import get_options_greeks
from tools.debate import run_cognitive_debate
from tools.political import get_political_data
from tools.contradictions import check_contradictions
from tools.macro import get_macro_data
from tools.news import get_news_sentiment
from tools.chart import encode_chart_image
from memory.working_memory import WorkingMemory

# Add parent path for src.cognitive imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
try:
    from src.cognitive.outcome_tracker import OutcomeTracker
    from src.cognitive.mistake_analyzer import MistakeAnalyzer
    LEARNING_AVAILABLE = True
except ImportError:
    LEARNING_AVAILABLE = False


TOOL_DEFINITIONS = [
    {
        "name": "get_ticker_data",
        "description": (
            "Pull current price, OHLCV data, technical indicators, ML signal score, "
            "and strategy breakdown for any ticker. Always call this before making "
            "any trade-related claim about a specific ticker."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Ticker symbol e.g. AAPL, SPY, BTC-USD"},
                "period": {"type": "string", "description": "Data period: 1d, 5d, 1mo, 3mo, 6mo, 1y", "default": "3mo"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "run_scenario",
        "description": (
            "Run GS-Quant-style risk scenarios against a ticker or portfolio. "
            "Scenarios: rate_shock, equity_crash, vol_spike, credit_widening, "
            "soft_landing, stagflation, dollar_surge."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "scenario": {
                    "type": "string",
                    "enum": ["rate_shock", "equity_crash", "vol_spike", "credit_widening",
                             "soft_landing", "stagflation", "dollar_surge", "all"],
                },
            },
            "required": ["ticker", "scenario"],
        },
    },
    {
        "name": "get_options_greeks",
        "description": "Calculate Black-Scholes option price and Greeks (delta, gamma, theta, vega, rho) for any option.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "strike": {"type": "number", "description": "Strike price"},
                "expiry_days": {"type": "integer", "description": "Days to expiry"},
                "option_type": {"type": "string", "enum": ["call", "put"]},
                "implied_vol": {"type": "number", "description": "Implied volatility as decimal e.g. 0.25 for 25%"},
            },
            "required": ["ticker", "strike", "expiry_days", "option_type"],
        },
    },
    {
        "name": "run_cognitive_debate",
        "description": (
            "Trigger a structured multi-agent Bull vs Bear vs Risk vs Quant vs Macro "
            "debate on a ticker. Returns five distinct perspectives with supporting evidence. "
            "Use this when forming a high-conviction view or when signals contradict."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "context": {"type": "string", "description": "Optional: specific question or angle to debate"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_political_data",
        "description": "Get congressional trading activity and political intelligence for a ticker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "check_contradictions",
        "description": (
            "Scan for signal conflicts across technical, ML, macro, sentiment, and options "
            "dimensions for a ticker. Always run this before giving a high-conviction view."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_macro_data",
        "description": "Fetch current macroeconomic indicators: Fed funds rate, CPI, unemployment, GDP, yield curve, DXY.",
        "input_schema": {
            "type": "object",
            "properties": {
                "indicators": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of indicators: fed_rate, cpi, unemployment, gdp, yield_curve, dxy, vix. Use ['all'] for everything.",
                },
            },
            "required": ["indicators"],
        },
    },
    {
        "name": "get_news_sentiment",
        "description": "Get recent news headlines and NLP sentiment analysis for a ticker or macro topic.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Ticker symbol or topic e.g. 'NVDA' or 'Fed interest rates'"},
                "days": {"type": "integer", "description": "Number of days of news to fetch", "default": 3},
            },
            "required": ["query"],
        },
    },
    {
        "name": "record_signal",
        "description": (
            "Record a trade signal you are issuing so the system can track whether you were right. "
            "Call this every time you give a directional view with conviction — LONG, SHORT, or NEUTRAL. "
            "This is how ARIA learns from experience: outcomes are checked automatically in 3, 7, 14, and 30 days."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Ticker symbol"},
                "direction": {"type": "string", "enum": ["long", "short", "neutral"], "description": "Your directional call"},
                "confidence": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"], "description": "Conviction level"},
                "score": {"type": "number", "description": "Signal score -100 to 100"},
                "regime": {"type": "string", "description": "Current market regime e.g. 'bull_trend', 'bear_collapse', 'risk_off'"},
                "reasoning": {"type": "string", "description": "Your core reasoning in 1-3 sentences — this becomes the postmortem record"},
            },
            "required": ["ticker", "direction", "confidence", "regime", "reasoning"],
        },
    },
    {
        "name": "recall_past_situations",
        "description": (
            "Search ARIA's experience memory for past situations similar to the current setup. "
            "Returns past signals issued under similar regime + score conditions, with their outcomes. "
            "Use this before issuing a new signal to check: 'have I been here before, and was I right?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "regime": {"type": "string", "description": "Current regime"},
                "score": {"type": "number", "description": "Current signal score"},
            },
            "required": ["ticker", "regime", "score"],
        },
    },
    {
        "name": "read_my_notes",
        "description": (
            "Read the user's personal Obsidian notes about a specific ticker or topic. "
            "Surfaces what the user has written in their own digital brain — research, ideas, concerns. "
            "Use this to understand the user's existing thinking before adding your analysis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Ticker to search for in the user's vault"},
            },
            "required": ["ticker"],
        },
    },
]


class ARIASession:
    def __init__(self, api_key, newsapi_key, fred_key, bridge=None, debug=False):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.newsapi_key = newsapi_key
        self.fred_key = fred_key
        self.bridge = bridge
        self.debug = debug
        self.conversation = []
        self.working_memory = WorkingMemory()
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.chart_content = None

        # Experience learning system
        self.outcome_tracker = None
        self.mistake_analyzer = None
        if LEARNING_AVAILABLE:
            base_path = Path(__file__).parent.parent.parent
            self.outcome_tracker = OutcomeTracker(base_path=str(base_path))
            self.mistake_analyzer = MistakeAnalyzer(self.outcome_tracker)
            # Resolve any pending outcomes on startup
            newly_resolved = self.outcome_tracker.resolve_pending_outcomes()
            if newly_resolved and bridge:
                for outcome in newly_resolved:
                    bridge.write_postmortem(outcome)
                    verdict = outcome.get("final_verdict", "?")
                    if verdict == "MISS":
                        # Auto-append lesson to vault
                        ticker = outcome.get("ticker", "")
                        regime = outcome.get("regime", "")
                        first_outcome = next(iter(outcome.get("outcomes", {}).values()), {})
                        move = first_outcome.get("pct_move", "?")
                        bridge.append_lesson(
                            f"Called {outcome.get('direction','?').upper()} on {ticker} "
                            f"in {regime} regime (score {outcome.get('score','?')}) — "
                            f"actual move was {move}%. Revisit signal weighting for this regime.",
                            category="regime",
                        )
                print(f"[ARIA] {len(newly_resolved)} outcomes resolved — postmortems written to vault.")

    async def boot(self, initial_ticker=None, chart_path=None):
        """Load memory from vault, inject into context."""
        if self.bridge:
            long_term = self.bridge.read_long_term_memory()
            recent = self.bridge.read_recent_memory()
            self.working_memory.load_from_vault(long_term, recent)
            if long_term or recent:
                print(f"[ARIA] Memory loaded — {len(self.working_memory.past_decisions)} past decisions, "
                      f"{len(self.working_memory.learned_patterns)} patterns")

        if chart_path:
            await self.analyse_chart(chart_path)

        if initial_ticker:
            await self.chat(f"Give me a full read on {initial_ticker.upper()} — signal score, contradictions, and your calibrated view.")

    async def chat(self, user_message: str, image_data: dict = None):
        """Main chat loop with tool use."""
        # Build message content
        content = []
        if image_data:
            content.append({"type": "image", "source": image_data})
        content.append({"type": "text", "text": user_message})

        self.conversation.append({"role": "user", "content": content if image_data else user_message})

        # Build mistake analysis context for this session
        mistake_context = ""
        if self.mistake_analyzer:
            mistake_context = self.mistake_analyzer.to_context()

        system = build_system_prompt(
            self.working_memory,
            obsidian_bridge=self.bridge,
            mistake_context=mistake_context,
        )

        # Agentic loop — keep going until no more tool calls
        while True:
            try:
                response = self.client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=4096,
                    system=system,
                    messages=self.conversation,
                    tools=TOOL_DEFINITIONS,
                )
            except Exception as e:
                print_error(f"API error: {e}")
                return

            # Collect text + tool use blocks
            text_blocks = []
            tool_blocks = []

            for block in response.content:
                if block.type == "text":
                    text_blocks.append(block.text)
                elif block.type == "tool_use":
                    tool_blocks.append(block)

            # Print any text ARIA produced
            if text_blocks:
                full_text = "\n".join(text_blocks)
                print_aria(full_text)
                # Extract any trade signals or decisions for working memory
                self.working_memory.update_from_aria_response(full_text)

            # Append assistant turn to conversation
            self.conversation.append({"role": "assistant", "content": response.content})

            # If no tool calls, we're done
            if not tool_blocks or response.stop_reason == "end_turn":
                break

            # Execute tools
            tool_results = []
            for tool_block in tool_blocks:
                if self.debug:
                    print_tool_use(tool_block.name, tool_block.input)

                result = await self._dispatch_tool(tool_block.name, tool_block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": json.dumps(result, default=str),
                })

            # Feed tool results back
            self.conversation.append({"role": "user", "content": tool_results})

        # Auto-save notable analysis to vault
        if self.bridge and text_blocks:
            self._maybe_write_to_vault("\n".join(text_blocks))

    async def _dispatch_tool(self, name: str, inputs: dict) -> dict:
        """Route tool calls to the appropriate handler."""
        try:
            if name == "get_ticker_data":
                return await get_ticker_data(inputs["ticker"], inputs.get("period", "3mo"))
            elif name == "run_scenario":
                return await run_scenario(inputs["ticker"], inputs["scenario"])
            elif name == "get_options_greeks":
                return await get_options_greeks(
                    inputs["ticker"], inputs["strike"], inputs["expiry_days"],
                    inputs["option_type"], inputs.get("implied_vol")
                )
            elif name == "run_cognitive_debate":
                return await run_cognitive_debate(
                    inputs["ticker"], inputs.get("context", ""),
                    self.client
                )
            elif name == "get_political_data":
                return await get_political_data(inputs["ticker"])
            elif name == "check_contradictions":
                return await check_contradictions(inputs["ticker"])
            elif name == "get_macro_data":
                return await get_macro_data(inputs["indicators"], self.fred_key)
            elif name == "get_news_sentiment":
                return await get_news_sentiment(inputs["query"], inputs.get("days", 3), self.newsapi_key)

            elif name == "record_signal":
                if self.outcome_tracker:
                    signal_id = self.outcome_tracker.record_signal_issued(
                        ticker=inputs["ticker"],
                        direction=inputs["direction"],
                        confidence=inputs["confidence"],
                        score=inputs.get("score", 0),
                        regime=inputs["regime"],
                        reasoning=inputs["reasoning"],
                    )
                    return {
                        "status": "recorded",
                        "signal_id": signal_id,
                        "message": (
                            f"Signal logged. I'll check back in 3, 7, 14, and 30 days to see "
                            f"if this call was right. The outcome will be saved as a postmortem "
                            f"in your Obsidian vault under ARIA/journal/postmortems/."
                        ),
                        "pending_signals": self.outcome_tracker.pending_count(),
                    }
                return {"status": "skipped", "reason": "outcome_tracker not available"}

            elif name == "recall_past_situations":
                if self.outcome_tracker:
                    similar = self.outcome_tracker.situation_fingerprint(
                        ticker=inputs["ticker"],
                        regime=inputs["regime"],
                        score=inputs.get("score", 0),
                    )
                    if not similar:
                        return {
                            "status": "no_history",
                            "message": "No similar past situations found in experience memory yet. This is a new territory.",
                        }
                    result = []
                    for s in similar:
                        outcomes_summary = {
                            k: f"{v.get('pct_move','?')}% ({v.get('verdict','?')})"
                            for k, v in s.get("outcomes", {}).items()
                        }
                        result.append({
                            "ticker": s.get("ticker"),
                            "date": s.get("issued_at", "")[:10],
                            "direction": s.get("direction"),
                            "confidence": s.get("confidence"),
                            "regime": s.get("regime"),
                            "score": s.get("score"),
                            "reasoning_then": s.get("reasoning", "")[:200],
                            "outcomes": outcomes_summary,
                            "final_verdict": s.get("final_verdict", "pending"),
                        })
                    return {"similar_past_situations": result, "count": len(result)}
                return {"status": "skipped", "reason": "outcome_tracker not available"}

            elif name == "read_my_notes":
                if self.bridge:
                    notes = self.bridge.read_ticker_notes(inputs["ticker"])
                    return {"ticker": inputs["ticker"], "vault_notes": notes}
                return {"status": "skipped", "reason": "no vault connected"}

            else:
                return {"error": f"Unknown tool: {name}"}
        except Exception as e:
            return {"error": str(e), "tool": name}

    async def analyse_chart(self, chart_path: str):
        """Encode chart image and send to ARIA for vision analysis."""
        path = Path(chart_path)
        if not path.exists():
            print_error(f"Chart not found: {chart_path}")
            return

        image_data = encode_chart_image(path)
        if not image_data:
            print_error("Could not encode chart image.")
            return

        print(f"[ARIA] Analysing chart: {path.name}")
        await self.chat(
            "Analyse this chart. Identify: trend direction, key support/resistance levels, "
            "notable patterns (breakouts, head & shoulders, flags, wedges, divergences), "
            "volume behaviour, and your read on the setup quality. Be specific about price levels.",
            image_data=image_data,
        )

        # Save chart analysis to vault
        if self.bridge:
            self.bridge.write_chart_read(path.name, self.conversation[-2]["content"] if len(self.conversation) >= 2 else "")

    async def run_debate(self, ticker: str):
        """Run a full cognitive debate and display."""
        await self.chat(f"Run a full cognitive debate on {ticker}. I want to hear all five perspectives.")

    async def run_scenario_menu(self, ticker: str):
        """Interactive scenario selection."""
        print("\nScenarios:")
        scenarios = ["rate_shock", "equity_crash", "vol_spike", "credit_widening",
                     "soft_landing", "stagflation", "dollar_surge", "all"]
        for i, s in enumerate(scenarios, 1):
            print(f"  {i}. {s}")
        choice = input("  Select [1-8]: ").strip()
        try:
            scenario = scenarios[int(choice) - 1]
            await self.chat(f"Run the {scenario} scenario for {ticker}.")
        except (ValueError, IndexError):
            print_error("Invalid selection.")

    async def show_watchlist(self):
        """Show watchlist from vault."""
        if self.bridge:
            watchlist = self.bridge.read_watchlist()
            if watchlist:
                print("\n[ARIA] Watchlist:")
                for item in watchlist:
                    print(f"  {item}")
            else:
                print("[ARIA] No watchlist found in vault.")
        else:
            print("[ARIA] No vault connected.")

    def show_memory_summary(self):
        """Print current working memory."""
        print("\n[ARIA] Working Memory:")
        print(f"  Past decisions: {len(self.working_memory.past_decisions)}")
        print(f"  Learned patterns: {len(self.working_memory.learned_patterns)}")
        print(f"  Macro regime: {self.working_memory.macro_regime or 'unknown'}")
        print(f"  Tickers in focus: {', '.join(self.working_memory.tickers_in_focus) or 'none'}")
        if self.working_memory.open_questions:
            print(f"  Open questions: {len(self.working_memory.open_questions)}")

    def _maybe_write_to_vault(self, aria_response: str):
        """Write significant analysis to vault automatically."""
        # Only write if response is substantive (>200 chars, not just a greeting)
        if len(aria_response) < 200:
            return
        keywords = ["signal", "trade", "buy", "sell", "resistance", "support",
                    "bullish", "bearish", "scenario", "risk", "position", "entry", "exit"]
        if not any(k in aria_response.lower() for k in keywords):
            return
        self.bridge.write_session_note(self.session_id, aria_response)

    def save_session(self, bridge):
        """Persist full session summary to vault."""
        bridge.write_session_summary(
            session_id=self.session_id,
            conversation=self.conversation,
            working_memory=self.working_memory,
        )
        # Promote learnings to long-term memory
        bridge.promote_to_long_term(self.working_memory)

    def clear_conversation(self):
        """Reset conversation but keep memory."""
        self.conversation = []
