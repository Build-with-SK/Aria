#!/usr/bin/env python3
"""
chat.py — ARIA Terminal Interface
Run this to start a conversation with ARIA.

Usage:
    python chat.py

Commands inside the chat:
    help                    Show this help
    refresh                 Reload TIS data from disk
    debate TICKER           Run the full cognitive debate on a ticker
    scenario <description>  Run a risk scenario, e.g. "scenario rate +100bps"
    history                 Show recent conversation
    quit / exit             End the session (saves summary to long-term memory)
"""

import sys
import os
from pathlib import Path

# Ensure project root is on the path so `src.*` imports work
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from src.cognitive.aria_core import AriaCore
except ImportError as e:
    print(f"\n[ERROR] Could not import ARIA core: {e}")
    print("Make sure you're running this from the trading-intelligence-system root directory")
    print("and that 'pip install anthropic' has been run.\n")
    sys.exit(1)


BANNER = r"""
╔══════════════════════════════════════════════╗
║  ARIA — Adaptive Reasoning Intelligence     ║
║  Trading Intelligence System v4.0           ║
║  Type 'help' for commands, 'quit' to exit   ║
╚══════════════════════════════════════════════╝
"""

HELP_TEXT = """
ARIA COMMANDS:
  help                     Show this help message
  refresh                  Reload TIS data from disk (signals, political, log)
  debate <TICKER>          Run the full 5-perspective cognitive debate (auto-saves to Obsidian)
  scenario <description>   Run a risk scenario (auto-saves to Obsidian)
                            e.g. "scenario rate +100bps"
                            e.g. "scenario equity crash -20%"
                            e.g. "scenario vol spike"
                            e.g. "scenario all"  (runs every scenario, does not auto-save)
  save                     Manually save ARIA's last response to Obsidian
                            (saves under the last-discussed ticker if one was mentioned)
  save <TICKER>            Save the last response under a specific ticker note
  history                  Show recent conversation turns
  quit / exit              End session (saves summary to long-term memory + Obsidian)

Or just talk naturally:
  "What's the market telling you today?"
  "Why is GS your top long?"
  "Should I be in NVDA this week?"
  "What's my biggest risk right now?"
  "Play devil's advocate on that"

OBSIDIAN:
  Debates, scenarios, and session summaries auto-save to your DigitalBrain vault
  under 01 - Trading\\ARIA\\. Default vault location is assumed to be a sibling
  folder of this project (...\\Documents\\DigitalBrain\\). To override, set:
    $env:OBSIDIAN_VAULT_PATH = "C:\\path\\to\\your\\vault"
"""


def check_api_key():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("\n[WARNING] ANTHROPIC_API_KEY environment variable is not set.")
        print("Set it with:")
        print('  PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-your-key-here"')
        print("  Or create a .env file in the project root with:")
        print("    ANTHROPIC_API_KEY=sk-ant-your-key-here")
        print("\nAttempting to continue (the SDK may pick it up another way)...\n")


def print_loading_state(aria: "AriaCore"):
    meta = aria.working_memory.get_meta()
    n_tickers = len(aria.working_memory.all_signals())
    score, _ = aria.working_memory._meta_lookup(meta, ["combined_score", "portfolio_score", "score", "composite_score"])
    regime, _ = aria.working_memory._meta_lookup(meta, ["regime", "macro_regime", "market_regime"])
    var, _ = aria.working_memory._meta_lookup(meta, ["var_1d", "var", "value_at_risk", "var_1d_usd", "portfolio_var"])
    score = score if score is not None else "N/A"
    regime = regime if regime else "N/A"
    var_str = f"${var:,.0f}" if isinstance(var, (int, float)) else (var if var else "N/A")

    print("Loading market state...")
    print(f"✓ {n_tickers} tickers loaded | Score: {score} | Regime: {regime} | VaR: {var_str}")
    if aria.obsidian.vault_exists():
        print(f"✓ Obsidian vault found: {aria.obsidian.vault_path}\n")
    else:
        print(f"⚠ Obsidian vault not found at {aria.obsidian.vault_path} — debates/scenarios won't auto-save.")
        print(f"  Fix: AriaCore(vault_path=\"...\") in chat.py, or set OBSIDIAN_VAULT_PATH.\n")


def main():
    # Try to load .env if python-dotenv is available
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    print(BANNER)
    check_api_key()

    vault_path = os.environ.get("OBSIDIAN_VAULT_PATH")  # Optional override
    try:
        aria = AriaCore(base_path=str(PROJECT_ROOT), vault_path=vault_path)
    except Exception as e:
        print(f"[FATAL] Could not initialise ARIA: {e}")
        sys.exit(1)

    print_loading_state(aria)
    last_response = None
    last_ticker = None
    print("ARIA > ", end="", flush=True)

    while True:
        try:
            user_input = input().strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nEnding session...")
            export_status = aria.end_session()
            if export_status:
                print(export_status)
            print("Session saved. Goodbye.")
            break

        if not user_input:
            print("ARIA > ", end="", flush=True)
            continue

        lower = user_input.lower()

        # ── Commands ──────────────────────────────────────────────────────
        if lower in ("quit", "exit"):
            print("\nEnding session and saving summary to long-term memory...")
            export_status = aria.end_session()
            if export_status:
                print(export_status)
            print("Session saved. Goodbye.")
            break

        elif lower == "help":
            print(HELP_TEXT)

        elif lower == "refresh":
            print("Refreshing TIS data from disk...")
            aria.working_memory.refresh()
            print_loading_state(aria)

        elif lower == "history":
            print("\n--- RECENT CONVERSATION ---")
            for msg in aria.conversation_history[-10:]:
                role = msg["role"].upper()
                content = msg["content"] if isinstance(msg["content"], str) else "[tool interaction]"
                preview = content[:200] + ("..." if len(content) > 200 else "")
                print(f"[{role}] {preview}")
            print("--- END ---\n")

        elif lower.startswith("debate "):
            ticker = user_input.split(" ", 1)[1].strip().upper()
            print(f"\nRunning full cognitive debate on {ticker}... (this calls Claude 6 times, please wait)\n")
            result = aria.debate_ticker(ticker)
            print(result)
            last_response, last_ticker = result, ticker

        elif lower.startswith("scenario"):
            description = user_input[len("scenario"):].strip()
            if not description or description == "all":
                from src.gs_quant_bridge.scenario_engine import ScenarioEngine
                engine = ScenarioEngine(str(PROJECT_ROOT))
                print(engine.format_all_scenarios())
            else:
                print(aria.run_scenario_from_text(description))

        elif lower.startswith("save"):
            ticker_arg = user_input[len("save"):].strip().upper()
            target_ticker = ticker_arg or last_ticker
            if not last_response:
                print("Nothing to save yet — ask ARIA something first.")
            elif target_ticker:
                result = aria.obsidian.export_ticker_note(target_ticker, last_response)
                print(aria.obsidian.status_message(result))
            else:
                result = aria.obsidian.export_session(last_response, list(aria._session_tickers_discussed))
                print(aria.obsidian.status_message(result))

        else:
            # Normal conversational turn
            print()  # blank line before response
            try:
                response = aria.ask(user_input)
                print(response)
                last_response = response
                detected = aria._extract_ticker(user_input)
                if detected:
                    last_ticker = detected
            except Exception as e:
                print(f"[ERROR] {e}")
            print()

        print("ARIA > ", end="", flush=True)


if __name__ == "__main__":
    main()
