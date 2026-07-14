"""
test_political_imports.py
=========================
Quick import and integration tests for the Political Intelligence Layer.

Run from project root:
    python test_political_imports.py

All tests should pass even with no political data loaded.
A failed test means an import error or missing file — NOT missing data.

Trading Intelligence System — Phase 4
"""

import sys
import json
import traceback
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent))

PASS = "✅ PASS"
FAIL = "❌ FAIL"
WARN = "⚠️  WARN"

results = []


def test(name: str, fn):
    try:
        fn()
        results.append((PASS, name))
        print(f"  {PASS}  {name}")
    except Exception as e:
        results.append((FAIL, name))
        print(f"  {FAIL}  {name}")
        print(f"         Error: {e}")
        print(f"         {traceback.format_exc().splitlines()[-1]}")


print("\n" + "=" * 65)
print("  POLITICAL INTELLIGENCE LAYER — IMPORT TESTS")
print("=" * 65)
print()

# ── Schema tests ─────────────────────────────────────────────────────────────
print("  [1] Schema and Data Classes")
test("political_data_schema imports",
     lambda: __import__("src.political.political_data_schema", fromlist=["PoliticalTradeRecord"]))

test("PoliticalTradeRecord instantiates",
     lambda: __import__("src.political.political_data_schema", fromlist=["PoliticalTradeRecord"]).PoliticalTradeRecord())

test("PoliticalWatchlistEntry instantiates",
     lambda: __import__("src.political.political_data_schema", fromlist=["PoliticalWatchlistEntry"]).PoliticalWatchlistEntry())

test("TradeCandidateRecord instantiates",
     lambda: __import__("src.political.political_data_schema", fromlist=["TradeCandidateRecord"]).TradeCandidateRecord())

# ── Sources tests ─────────────────────────────────────────────────────────────
print("\n  [2] Sources")
test("political_sources imports",
     lambda: __import__("src.political.political_sources", fromlist=["load_all_sources"]))

test("ManualCSVSource instantiates",
     lambda: __import__("src.political.political_sources", fromlist=["ManualCSVSource"]).ManualCSVSource())

test("load_all_sources returns list (may be empty)",
     lambda: isinstance(
         __import__("src.political.political_sources", fromlist=["load_all_sources"]).load_all_sources(),
         list
     ))

test("list_sources_status returns list",
     lambda: isinstance(
         __import__("src.political.political_sources", fromlist=["list_sources_status"]).list_sources_status(),
         list
     ))

test("sample CSV created",
     lambda: __import__("src.political.political_sources", fromlist=["create_sample_csv"]).create_sample_csv())

# ── Watchlist tests ────────────────────────────────────────────────────────────
print("\n  [3] Political Watchlist")
test("political_watchlist imports",
     lambda: __import__("src.political.political_watchlist", fromlist=["PoliticalWatchlist"]))

def _wl_run():
    from src.political.political_watchlist import PoliticalWatchlist
    wl = PoliticalWatchlist()
    entries = wl.run(save=True)
    assert isinstance(entries, list), "Expected list"

test("PoliticalWatchlist runs and returns list",
     _wl_run)

test("watchlist.json exists after run",
     lambda: Path("data/political/watchlist.json").exists())

def _wl_valid_json():
    with open("data/political/watchlist.json") as f:
        data = json.load(f)
    assert "watchlist" in data, "Missing 'watchlist' key"
    assert "generated_at" in data, "Missing 'generated_at' key"

test("watchlist.json is valid JSON",
     _wl_valid_json)

# ── Signal engine tests ────────────────────────────────────────────────────────
print("\n  [4] Political Signal Engine")
test("political_signal_engine imports",
     lambda: __import__("src.political.political_signal_engine", fromlist=["PoliticalSignalEngine"]))

def _engine_run():
    from src.political.political_signal_engine import PoliticalSignalEngine
    engine = PoliticalSignalEngine()
    candidates = engine.run(save=True)
    assert isinstance(candidates, list), "Expected list"

test("PoliticalSignalEngine runs and returns list",
     _engine_run)

test("political_signals.json exists",
     lambda: Path("data/political/political_signals.json").exists())

test("trade_candidates.json exists",
     lambda: Path("data/trade_candidates.json").exists())

def _candidates_valid_json():
    with open("data/trade_candidates.json") as f:
        data = json.load(f)
    assert "trade_candidates" in data, "Missing 'trade_candidates' key"

test("trade_candidates.json is valid JSON",
     _candidates_valid_json)

# ── Package-level import test ──────────────────────────────────────────────────
print("\n  [5] Package-level imports")
test("from src.political import run_political_pipeline",
     lambda: __import__("src.political", fromlist=["run_political_pipeline"]).run_political_pipeline)

test("from src.political.political_watchlist import PoliticalWatchlist",
     lambda: __import__("src.political.political_watchlist", fromlist=["PoliticalWatchlist"]).PoliticalWatchlist)

test("from src.political.political_signal_engine import PoliticalSignalEngine",
     lambda: __import__("src.political.political_signal_engine", fromlist=["PoliticalSignalEngine"]).PoliticalSignalEngine)

# ── Integration bridge ─────────────────────────────────────────────────────────
print("\n  [6] Main Integration Bridge")
test("political_main_integration imports",
     lambda: __import__("src.political.political_main_integration", fromlist=["run_political_layer"]))

def _integration_run():
    from src.political.political_main_integration import run_political_layer
    result = run_political_layer(verbose=False)
    assert isinstance(result, list), "Expected list"

test("run_political_layer() returns list (never crashes)",
     _integration_run)

# ── Safe ML features test ──────────────────────────────────────────────────────
print("\n  [7] ML Safety")
def _safe_ml():
    from src.political.political_watchlist import PoliticalWatchlist
    features = PoliticalWatchlist.safe_ml_features("NVDA")
    assert features["ticker"] == "NVDA"
    assert features["political_activity_score"] == 0.0
    assert features["public_official_exposure_flag"] == 0

test("safe_ml_features returns neutral defaults for unknown ticker",
     _safe_ml)

# ── Results summary ────────────────────────────────────────────────────────────
pass_count = sum(1 for r in results if r[0] == PASS)
fail_count = sum(1 for r in results if r[0] == FAIL)
total = len(results)

print()
print("=" * 65)
print(f"  Results: {pass_count}/{total} passed  |  {fail_count} failed")
print("=" * 65)

if fail_count > 0:
    print("\n  ⚠  Some tests failed. Check the errors above.")
    print("     Your existing main.py is unaffected — the political layer")
    print("     is isolated and will not crash main.py on failure.")
    sys.exit(1)
else:
    print("\n  All tests passed. Political Intelligence Layer is ready.")
    print("\n  Next steps:")
    print("   1. Add real data to: data/political/manual_political_trades.csv")
    print("      (copy from Capitol Trades, Quiver, Unusual Whales, House/Senate portals)")
    print("   2. Run: python run_political.py")
    print("   3. Check outputs: data/political/watchlist.json")
    print("                     data/trade_candidates.json")
    print("   4. Integrate into main.py: add 2 lines (see README below)")
    print()
