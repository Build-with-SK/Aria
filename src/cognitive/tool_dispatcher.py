"""
tool_dispatcher.py — ARIA Tool Dispatcher
Routes ARIA's tool calls to the right TIS modules.
This is the bridge between Claude's tool-use API and the actual Python backend.
"""

import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

from .working_memory import WorkingMemory
from .contradiction_engine import ContradictionEngine


class ToolDispatcher:
    """
    Dispatches tool calls from ARIA (Claude) to the underlying TIS systems.
    Each method here corresponds to a tool definition passed to the Claude API.
    """

    def __init__(self, base_path: str = None):
        if base_path is None:
            base_path = Path(__file__).parent.parent.parent
        self.base_path = Path(base_path)
        self.working_memory = WorkingMemory(str(self.base_path))
        self.contradiction_engine = ContradictionEngine()
        self.working_memory.refresh()

        # Lazy imports to avoid circular deps / missing optional packages at import time
        self._scenario_engine = None
        self._signal_aggregator = None

    # ── Tool definitions (Anthropic tool-use schema) ─────────────────────────

    @staticmethod
    def get_tool_definitions() -> list:
        """Returns the tool schema list to pass to the Claude API messages.create() call."""
        return [
            {
                "name": "get_ticker_data",
                "description": (
                    "Get the current signal score, ML prediction, strategy breakdown, "
                    "and political flags for a specific ticker."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string", "description": "Stock ticker symbol, e.g. NVDA"}
                    },
                    "required": ["ticker"],
                },
            },
            {
                "name": "run_scenario",
                "description": (
                    "Run a risk scenario against the current portfolio. "
                    "Scenario types: rate_shock, equity_crash, vol_spike, credit_widening, "
                    "soft_landing, stagflation."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "scenario_type": {
                            "type": "string",
                            "enum": ["rate_shock", "equity_crash", "vol_spike", "credit_widening", "soft_landing", "stagflation"],
                        },
                        "magnitude": {
                            "type": "number",
                            "description": "bps for rate_shock/credit_widening, % for equity_crash, VIX points for vol_spike. Omit for soft_landing/stagflation.",
                        },
                    },
                    "required": ["scenario_type"],
                },
            },
            {
                "name": "get_options_greeks",
                "description": "Calculate Black-Scholes price and Greeks for an option.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "spot": {"type": "number"},
                        "strike": {"type": "number"},
                        "days_to_expiry": {"type": "integer"},
                        "iv": {"type": "number", "description": "Implied vol as decimal, e.g. 0.25 for 25%"},
                        "option_type": {"type": "string", "enum": ["call", "put"]},
                    },
                    "required": ["ticker", "spot", "strike", "days_to_expiry", "iv", "option_type"],
                },
            },
            {
                "name": "run_cognitive_debate",
                "description": (
                    "Trigger the full Vertus-style cognitive debate on a ticker: "
                    "Bull Analyst, Bear Analyst, Risk Officer, Quant View, Macro Strategist, then synthesis."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {"ticker": {"type": "string"}},
                    "required": ["ticker"],
                },
            },
            {
                "name": "get_political_data",
                "description": "Get congressional trading / political intelligence data for a ticker.",
                "input_schema": {
                    "type": "object",
                    "properties": {"ticker": {"type": "string"}},
                    "required": ["ticker"],
                },
            },
            {
                "name": "check_contradictions",
                "description": (
                    "Check for contradictions in the current signals — either for a specific "
                    "ticker or portfolio-wide. Use this before giving high-conviction views."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string", "description": "Optional. Omit for portfolio-wide check."}
                    },
                    "required": [],
                },
            },
            {
                "name": "refresh_tis",
                "description": (
                    "Re-run the full TIS pipeline (python main.py) to get fresh signals. "
                    "This is SLOW (can take minutes) — only use if explicitly asked to refresh data."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
        ]

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def dispatch(self, tool_name: str, tool_input: dict) -> str:
        """Routes a tool call to the right handler. Returns a string result for Claude."""
        handlers = {
            "get_ticker_data": self._get_ticker_data,
            "run_scenario": self._run_scenario,
            "get_options_greeks": self._get_options_greeks,
            "run_cognitive_debate": self._run_cognitive_debate,
            "get_political_data": self._get_political_data,
            "check_contradictions": self._check_contradictions,
            "refresh_tis": self._refresh_tis,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return f"[ERROR] Unknown tool: {tool_name}"
        try:
            return handler(tool_input)
        except Exception as e:
            return f"[ERROR] Tool '{tool_name}' failed: {e}"

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _get_ticker_data(self, inp: dict) -> str:
        ticker = inp["ticker"].upper()
        ctx = self.working_memory.get_ticker_context(ticker)
        signal = ctx.get("signal", {})
        if not signal:
            return f"No signal data found for {ticker}. It may not be in the 82-ticker universe."

        lines = [f"=== {ticker} ==="]
        lines.append(f"Score: {signal.get('score', 'N/A')} | Direction: {signal.get('direction', 'N/A')}")
        lines.append(f"ML Prediction: {signal.get('ml_prediction', 'N/A')} (confidence: {signal.get('ml_confidence', 'N/A')})")
        if signal.get("strategies"):
            lines.append("Strategy breakdown:")
            for strat, detail in signal["strategies"].items():
                lines.append(f"  {strat}: {detail}")
        if ctx.get("political"):
            lines.append(f"Political: {ctx['political']}")
        if ctx.get("trade_candidate"):
            lines.append(f"Trade candidate category: {ctx['trade_candidate'].get('category')}")
        return "\n".join(lines)

    def _run_scenario(self, inp: dict) -> str:
        if self._scenario_engine is None:
            from src.gs_quant_bridge.scenario_engine import ScenarioEngine
            self._scenario_engine = ScenarioEngine(str(self.base_path))

        scenario_type = inp["scenario_type"]
        magnitude = inp.get("magnitude")

        params = {}
        if scenario_type in ("rate_shock",) and magnitude is not None:
            params["bps"] = magnitude
        elif scenario_type == "equity_crash" and magnitude is not None:
            params["pct"] = magnitude
        elif scenario_type == "vol_spike" and magnitude is not None:
            params["vix_move"] = magnitude
        elif scenario_type == "credit_widening" and magnitude is not None:
            params["bps"] = magnitude

        result = self._scenario_engine.run_custom_scenario(scenario_type, **params)
        if result is None:
            return f"[ERROR] Unknown scenario type: {scenario_type}"
        return self._scenario_engine.format_report(result)

    def _get_options_greeks(self, inp: dict) -> str:
        from src.gs_quant_bridge.options_analytics import BlackScholes

        T = inp["days_to_expiry"] / 365
        bs = BlackScholes(
            spot=inp["spot"],
            strike=inp["strike"],
            time_to_expiry=T,
            risk_free_rate=0.05,
            implied_vol=inp["iv"],
        )
        greeks = bs.price_and_greeks(inp["option_type"])
        return (
            f"=== {inp['ticker']} {inp['option_type'].upper()} "
            f"${inp['strike']} exp {inp['days_to_expiry']}d ===\n"
            f"{greeks.summary()}\n"
            f"Intrinsic: ${greeks.intrinsic:.2f} | Time Value: ${greeks.time_value:.2f}"
        )

    def _run_cognitive_debate(self, inp: dict) -> str:
        """
        Note: the actual debate generation (calling Claude 5x for each perspective)
        happens in aria_core.py since it needs the Claude client. This handler
        just returns the context needed to run it, signalling aria_core to proceed.
        """
        ticker = inp["ticker"].upper()
        ctx = self.working_memory.get_ticker_context(ticker)
        contradiction_report = self.contradiction_engine.analyse_ticker(ticker, self.working_memory)

        return (
            f"[DEBATE_CONTEXT_READY] ticker={ticker}\n"
            f"Signal context: {ctx.get('signal', {})}\n"
            f"{contradiction_report.to_context()}"
        )

    def _get_political_data(self, inp: dict) -> str:
        ticker = inp["ticker"].upper()
        ctx = self.working_memory.get_ticker_context(ticker)
        political = ctx.get("political", {})
        political_signal = ctx.get("political_signal", {})
        if not political and not political_signal:
            return f"No political/congressional trading data found for {ticker}."
        return f"=== {ticker} POLITICAL INTELLIGENCE ===\nWatchlist: {political}\nSignal: {political_signal}"

    def _check_contradictions(self, inp: dict) -> str:
        ticker = inp.get("ticker")
        if ticker:
            report = self.contradiction_engine.analyse_ticker(ticker.upper(), self.working_memory)
        else:
            report = self.contradiction_engine.analyse_portfolio(self.working_memory)
        return report.to_context()

    def _refresh_tis(self, inp: dict) -> str:
        main_py = self.base_path / "main.py"
        if not main_py.exists():
            return "[ERROR] main.py not found — cannot refresh."
        try:
            result = subprocess.run(
                [sys.executable, str(main_py)],
                cwd=str(self.base_path),
                capture_output=True,
                text=True,
                timeout=600,
            )
            self.working_memory.refresh()
            tail = "\n".join(result.stdout.strip().split("\n")[-15:])
            return f"TIS refresh complete.\n\nLast output lines:\n{tail}"
        except subprocess.TimeoutExpired:
            return "[ERROR] TIS refresh timed out after 10 minutes."
        except Exception as e:
            return f"[ERROR] TIS refresh failed: {e}"
