#!/usr/bin/env python3
"""
ARIA — Adaptive Reasoning Intelligence Architecture
Cognitive AI layer for Trading Intelligence System
CLI entry point
"""

import os
import sys
import asyncio
import argparse
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / "config" / ".env")

from core.session import ARIASession
from core.display import print_banner, print_help
from memory.obsidian_bridge import ObsidianBridge


def parse_args():
    p = argparse.ArgumentParser(description="ARIA Trading Intelligence CLI")
    p.add_argument("--vault", type=str, help="Path to Obsidian vault (overrides .env)")
    p.add_argument("--ticker", type=str, help="Start session focused on a specific ticker")
    p.add_argument("--debug", action="store_true", help="Show tool calls and raw responses")
    p.add_argument("--no-memory", action="store_true", help="Disable Obsidian memory (ephemeral session)")
    p.add_argument("--chart", type=str, help="Path to chart image to analyse on startup")
    return p.parse_args()


async def main():
    args = parse_args()

    vault_path = args.vault or os.getenv("OBSIDIAN_VAULT_PATH")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    newsapi_key = os.getenv("NEWSAPI_KEY", "")
    fred_key = os.getenv("FRED_API_KEY", "")

    if not api_key:
        print("[ARIA] ANTHROPIC_API_KEY not set. Add it to config/.env")
        sys.exit(1)

    print_banner()

    # Initialise Obsidian bridge
    bridge = None
    if not args.no_memory and vault_path:
        bridge = ObsidianBridge(vault_path)
        bridge.ensure_aria_structure()
        print(f"[ARIA] Vault connected: {vault_path}")
    elif not args.no_memory:
        print("[ARIA] No vault path set — running without Obsidian memory")
        print("       Set OBSIDIAN_VAULT_PATH in config/.env to enable memory\n")

    # Boot session
    session = ARIASession(
        api_key=api_key,
        newsapi_key=newsapi_key,
        fred_key=fred_key,
        bridge=bridge,
        debug=args.debug,
    )

    await session.boot(
        initial_ticker=args.ticker,
        chart_path=args.chart,
    )

    print_help()

    # Main REPL loop
    while True:
        try:
            user_input = input("\n[You] ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[ARIA] Session ended. Good trading.")
            if bridge:
                session.save_session(bridge)
            break

        if not user_input:
            continue

        cmd = user_input.lower()

        if cmd in ("/exit", "/quit", "/q"):
            print("[ARIA] Saving session to vault...")
            if bridge:
                session.save_session(bridge)
            print("[ARIA] Session saved. Good trading.")
            break

        if cmd == "/help":
            print_help()
            continue

        if cmd == "/memory":
            if bridge:
                session.show_memory_summary()
            else:
                print("[ARIA] No vault connected.")
            continue

        if cmd.startswith("/chart "):
            chart_path = user_input[7:].strip()
            await session.analyse_chart(chart_path)
            continue

        if cmd == "/debate":
            ticker = input("  Ticker to debate: ").strip().upper()
            await session.run_debate(ticker)
            continue

        if cmd == "/scenario":
            ticker = input("  Ticker for scenario: ").strip().upper()
            await session.run_scenario_menu(ticker)
            continue

        if cmd == "/watchlist":
            await session.show_watchlist()
            continue

        if cmd == "/save":
            if bridge:
                session.save_session(bridge)
                print("[ARIA] Session saved.")
            else:
                print("[ARIA] No vault connected.")
            continue

        if cmd == "/clear":
            session.clear_conversation()
            print("[ARIA] Conversation cleared. Memory retained.")
            continue

        # Normal conversation
        await session.chat(user_input)


if __name__ == "__main__":
    asyncio.run(main())
