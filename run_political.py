"""
run_political.py
================
Standalone runner for the Political Intelligence Layer.

Run from project root:
    python run_political.py

Options:
    python run_political.py --export-obsidian
    python run_political.py --show-sources
    python run_political.py --show-matrix
    python run_political.py --status Trade Candidate

This does NOT modify main.py — it is an independent entry point.

To integrate into main.py instead, add this single line:
    from src.political.political_main_integration import run_political_layer
    run_political_layer()

Trading Intelligence System — Phase 4
Research and educational purposes only.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run_political")


def main():
    parser = argparse.ArgumentParser(
        description="Political Intelligence Layer — Trading Intelligence System Phase 4"
    )
    parser.add_argument(
        "--export-obsidian",
        action="store_true",
        help="Also generate Obsidian Markdown reports",
    )
    parser.add_argument(
        "--show-sources",
        action="store_true",
        help="Print data source status table",
    )
    parser.add_argument(
        "--show-matrix",
        action="store_true",
        help="Print confirmation matrix to console",
    )
    parser.add_argument(
        "--status",
        type=str,
        default=None,
        help="Filter output by status: 'Trade Candidate', 'Research Candidate', 'Watchlist Only', 'Avoid'",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Do not write output files (dry run)",
    )
    args = parser.parse_args()

    # ── Show sources ─────────────────────────
    if args.show_sources:
        from src.political.political_sources import list_sources_status
        print("\n" + "=" * 80)
        print("POLITICAL DATA SOURCES STATUS")
        print("=" * 80)
        print(f"{'Source':<30} {'Type':<25} {'API':>5} {'Manual':>8} {'Reliability':>12}")
        print("-" * 80)
        for s in list_sources_status():
            api = "✅" if s["api_available"] else "❌"
            manual = "✅" if s["manual_import_supported"] else "❌"
            print(
                f"{s['name'][:30]:<30} {s['type'][:25]:<25} "
                f"{api:>5} {manual:>8} {s['reliability']:>11}%"
            )
        print("=" * 80)
        print("\nAll sources use publicly available, legal disclosures only.")
        print("Set api_key in configs/political_sources.yaml to enable live API sources.\n")
        return

    # ── Run pipeline ─────────────────────────
    print("\n" + "=" * 80)
    print("TRADING INTELLIGENCE SYSTEM — Phase 4")
    print("Political Portfolio / Political Disclosure Intelligence Layer")
    print("=" * 80)
    print("RESEARCH AND EDUCATIONAL PURPOSES ONLY")
    print("No automatic trade execution. No broker connection.")
    print("Political signals = 10% of final score.")
    print("Trade Candidates require: Technical + Macro + Risk confirmation.")
    print("=" * 80 + "\n")

    from src.political.political_main_integration import run_political_layer
    candidates = run_political_layer(verbose=(args.show_matrix or True))

    # ── Filter output ─────────────────────────
    if args.status:
        filtered = [c for c in candidates if c.get("trade_status") == args.status]
        print(f"\n{'─'*60}")
        print(f"  {args.status}: {len(filtered)} ticker(s)")
        print(f"{'─'*60}")
        for c in filtered:
            print(f"  {c.get('ticker', 'N/A'):<8}  Score: {c.get('final_trade_candidate_score', 0):.1f}  "
                  f"Action: {c.get('action', 'neutral').upper()}  "
                  f"Confidence: {c.get('confidence', 'Low')}")
        print()

    # ── Summary ──────────────────────────────
    statuses = {}
    for c in candidates:
        s = c.get("trade_status", "Unknown")
        statuses[s] = statuses.get(s, 0) + 1

    print("\n" + "─" * 60)
    print("  OUTPUT SUMMARY")
    print("─" * 60)
    print(f"  Total candidates processed: {len(candidates)}")
    for status, count in sorted(statuses.items(), key=lambda x: x[0]):
        emoji = {
            "Trade Candidate": "🟢",
            "Research Candidate": "🟡",
            "Watchlist Only": "🔵",
            "Avoid": "🔴",
        }.get(status, "⚪")
        print(f"  {emoji}  {status}: {count}")

    print("\n  Output files:")
    print("    ✓ data/political/watchlist.json")
    print("    ✓ data/political/political_signals.json")
    print("    ✓ data/trade_candidates.json")

    # ── Obsidian export ───────────────────────
    if args.export_obsidian:
        print("\n  Generating Obsidian reports...")
        from src.political.obsidian_political_export import run_obsidian_export
        obsidian_files = run_obsidian_export()
        print("  Obsidian reports:")
        for f in obsidian_files:
            print(f"    ✓ {f}")

    print("\n  ⚠  IMPORTANT:")
    print("     Political disclosures are delayed (STOCK Act: 45-day window).")
    print("     'Public political disclosure activity detected.'")
    print("     'Added to watchlist. Trade candidate only if market confirmation agrees.'")
    print()


if __name__ == "__main__":
    main()
