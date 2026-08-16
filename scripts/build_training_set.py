"""
scripts/build_training_set.py
=============================
Build the corpora that make the model hers (docs/ARIA_NEXT_SESSION.md step 5).

    # what could be built right now, and what is missing
    venv/Scripts/python.exe scripts/build_training_set.py --readiness

    # the corpora themselves (refuses loudly when the sample is short)
    venv/Scripts/python.exe scripts/build_training_set.py --corpus outcomes
    venv/Scripts/python.exe scripts/build_training_set.py --corpus voice

    # a training run, rendered but never submitted
    venv/Scripts/python.exe scripts/build_training_set.py --corpus outcomes --prepare-run

`outcomes` is empty today — zero predictions have resolved — and this will say
so with the count it wanted and the count it has. That is the expected output
for now, not an error to work around. Nothing here may be synthesised.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.training import dataset as D
from src.training import qlora


def show_readiness() -> int:
    r = D.readiness()
    print("Training-set readiness\n" + "=" * 60)
    for name in ("outcomes", "voice"):
        block = r.get(name) or {}
        mark = "ready" if block.get("ready") else "NOT READY"
        print(f"\n{name:<10} {block.get('have')} / {block.get('want')}   {mark}")
        print(f"           {block.get('note', '')}")
    print("\n" + "=" * 60)
    print("READY" if r["ready"] else f"BLOCKED — {r['blocker']}")
    return 0 if r["ready"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--readiness", action="store_true")
    ap.add_argument("--corpus", choices=("outcomes", "voice"))
    ap.add_argument("--limit", type=int, default=0,
                    help="voice only: stop after N notes")
    ap.add_argument("--prepare-run", action="store_true",
                    help="render a training run for the corpus just built")
    ap.add_argument("--target", choices=("cluster", "local"), default="cluster")
    args = ap.parse_args(argv)

    if args.readiness or not args.corpus:
        return show_readiness()

    try:
        corpus = (D.build_outcomes() if args.corpus == "outcomes"
                  else D.build_voice(limit=args.limit))
    except D.NotReady as e:
        print(f"NOT READY — {e}", file=sys.stderr)
        if e.corpus.startswith("outcomes"):
            print("\nThis is the honest state, not a bug. The outcomes corpus "
                  "fills at the speed of the paper loop, and it is never "
                  "backfilled (invariant 5).", file=sys.stderr)
        return 1

    if args.corpus == "outcomes":
        leaks = D.label_leakage(corpus["train"] + corpus["holdout"])
        if leaks:
            print(f"REFUSING TO WRITE — {len(leaks)} example(s) contain the "
                  f"answer in the prompt: {leaks[:5]}", file=sys.stderr)
            return 2

    manifest = D.write(corpus)
    print(json.dumps(manifest, indent=2, default=str))
    if manifest["contains_personal_data"]:
        print("\nSENSITIVE: this corpus, and anything trained on it, stays off "
              "shared and university storage.")

    if args.prepare_run:
        try:
            run = qlora.prepare_run(manifest, target=args.target)
        except PermissionError as e:
            print(f"\nno training job rendered:\n{e}", file=sys.stderr)
            return 1
        print(f"\nrun prepared: {run['dir']}\nnext: {run['next']}")
        for n in run["notes"]:
            print(f"  note: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
