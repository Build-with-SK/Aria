"""
scripts/bench_reflex.py
=======================
Latency benchmark for the reflex fast lane. Arms a synthetic playbook on a
liquid paper ticker with a trigger just below the live price (so it fires at
once), runs the pipeline N times, and prints the per-stage latency breakdown.

    venv\\Scripts\\python.exe scripts\\bench_reflex.py [TICKER] [N]

Paper-only; never runs against a live account (the execute stage's own gates
enforce that). Set reflex_on_llm_fail=proceed in desk_config for a pure-latency
run when the Anthropic key isn't configured.
"""
import sys
import statistics
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass

from src.desk.config import load_config
from src.desk.reflex import ReflexEngine, Playbook, save_playbooks
from src.desk.auto_executor import account_snapshot, get_order_manager
from datetime import datetime, timedelta


def main():
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    cfg = load_config()
    eng = ReflexEngine(cfg)
    mgr = get_order_manager()
    if not (mgr and mgr._alpaca and mgr._alpaca.is_connected()):
        print("broker not connected — cannot benchmark"); return
    price = eng._quote(ticker, mgr)
    if price <= 0:
        print(f"no quote for {ticker}"); return
    account = account_snapshot()
    print(f"benchmarking {ticker} @ {price:.2f}, {n} runs "
          f"(equity ${account.get('equity', 0):,.0f})\n")

    stages = {}
    totals = []
    for i in range(n):
        pb = Playbook(
            ticker=ticker, side="buy", trigger_price=round(price * 0.999, 2),
            stop=round(price * 0.97, 2), target=round(price * 1.03, 2),
            conviction=70, debate_id="", source="bench",
            armed_at=datetime.now().isoformat(),
            expires_at=(datetime.now() + timedelta(days=1)).isoformat()).to_dict()
        eng._last_price[ticker] = price * 0.99   # force a cross
        out = eng._fire(pb, price, account, mgr)
        lat = out.get("latency_ms") or {}
        for k, v in lat.items():
            stages.setdefault(k, []).append(v)
        totals.append(lat.get("total", 0))
        print(f"  run {i+1:2d}: {out.get('stage'):9s} "
              f"total={lat.get('total','?')}ms  {out.get('reason','')[:50]}")

    print("\nper-stage median (ms):")
    for k in ("validate", "risk", "veto", "submit", "total"):
        if stages.get(k):
            print(f"  {k:9s} {statistics.median(stages[k]):.0f}")
    if totals:
        print(f"\ntotal: median {statistics.median(totals):.0f}ms, "
              f"max {max(totals)}ms, target ≤3500ms")


if __name__ == "__main__":
    main()
