"""
scripts/capture_brain_baseline.py
=================================
Capture the pre-swap brain baseline, or compare two of them
(docs/ARIA_NEXT_SESSION.md step 3).

    # before you touch the brain — you cannot go back for these
    venv/Scripts/python.exe scripts/capture_brain_baseline.py

    # after the swap
    venv/Scripts/python.exe scripts/capture_brain_baseline.py
    venv/Scripts/python.exe scripts/capture_brain_baseline.py --compare \
        data/baselines/brain_baseline_A.json data/baselines/brain_baseline_B.json

With no --compare arguments the second form uses the two most recent files.

Runs the real pipeline and real debates on five fixed tickers, so expect
minutes, not seconds. Nothing is written to the prediction log: a baseline is
measurement, not a call.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.inference import baseline as B


def do_capture(args) -> int:
    print(f"Capturing the brain baseline on {', '.join(args.tickers)}")
    print("(real pipeline + real debates — this takes minutes)\n")
    result = B.capture(tuple(args.tickers),
                       structured_attempts=args.structured_attempts,
                       skip_debates=args.no_debates,
                       skip_structured=args.no_structured)

    for m in result["modules"]:
        state = (f"{m['reporting']}/{m['total']} reporting, "
                 f"{m['abstained']} abstained" if m["ok"] else f"FAILED {m['error']}")
        print(f"  modules  {m['ticker']:<6} {state}  ({m['elapsed_ms']}ms)")
    for d in result["debates"]:
        state = (f"{d['verdict']} conviction {d['conviction']} via {d['llm']}"
                 if d["ok"] else f"FAILED {d['error']}")
        print(f"  debate   {d['ticker']:<6} {state}  ({d['elapsed_ms']}ms)")
    for s in result["structured"]:
        rate = s["failure_rate"]
        rate_s = "no reply arrived" if rate is None else f"{rate:.0%} unparseable"
        print(f"  format   {s['probe']:<14} {s['parsed']}/{s['attempts']} parsed, "
              f"{rate_s}")

    lat = result["latency"]
    if lat.get("samples"):
        print(f"\n  latency  p50 {lat['p50_ms']}ms  p95 {lat['p95_ms']}ms  "
              f"projected cycle {lat['projected_cycle_ms']}ms vs a "
              f"{lat['tick_minutes']}-minute tick "
              f"({'fits' if lat['fits_the_tick'] else 'DOES NOT FIT'})")

    if result["modules"] and not any(m["ok"] for m in result["modules"]):
        print("\nWARNING: no module run succeeded. This file records an outage, "
              "not a baseline — do not compare against it.")
    if result["debates"] and not any(d["ok"] for d in result["debates"]):
        print("\nWARNING: no debate completed. Same caveat.")

    path = B.save(result)
    print(f"\nsaved: {path}")
    print("\nAfter any brain change, module abstention and judge conviction must")
    print("come back IDENTICAL. --compare says so in one line.")
    return 0


def do_compare(args) -> int:
    if args.compare:
        a, b = Path(args.compare[0]), Path(args.compare[1])
    else:
        files = sorted(B.BASELINE_DIR.glob("brain_baseline_*.json"))
        if len(files) < 2:
            print("need two baselines to compare; found "
                  f"{len(files)} in {B.BASELINE_DIR}", file=sys.stderr)
            return 2
        a, b = files[-2], files[-1]
        print(f"comparing {a.name} → {b.name}\n")
    result = B.compare(B.load(a), B.load(b))
    print(B.format_comparison(result))
    return 1 if result["verdict"] == "MISWIRED" else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", nargs="+", default=list(B.FIXED_TICKERS),
                    help="override the fixed five (a baseline compared "
                         "against a different universe measures the universe)")
    ap.add_argument("--structured-attempts", type=int, default=6)
    ap.add_argument("--no-debates", action="store_true")
    ap.add_argument("--no-structured", action="store_true")
    ap.add_argument("--compare", nargs="*", metavar="FILE",
                    help="compare two baselines (default: the two most recent)")
    args = ap.parse_args(argv)
    return do_compare(args) if args.compare is not None else do_capture(args)


if __name__ == "__main__":
    raise SystemExit(main())
