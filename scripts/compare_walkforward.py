"""
scripts/compare_walkforward.py
==============================
Compare two walk-forward runs, arm A against arm B.

    python scripts/compare_walkforward.py before.json after.json

Exists so that "the change helped" is a number somebody else can check rather
than an impression. Prints per-module hit rate, edge over base, information
coefficient and the DISCOUNTED sample behind each, and refuses to call a
difference an improvement unless at least one arm cleared significance.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def fmt(v, pct=True, width=7):
    if v is None:
        return " " * (width - 1) + "-"
    return f"{v:>{width}.1%}" if pct else f"{v:>+{width}.3f}"


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    a = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    b = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    ra, rb = a.get("results", {}), b.get("results", {})

    names = sorted(set(ra) | set(rb))
    print(f"A = {sys.argv[1]}")
    print(f"B = {sys.argv[2]}\n")
    header = (f"{'module':<22}{'calls A':>8}{'calls B':>8}{'n_eff A':>8}{'n_eff B':>8}"
              f"{'hit A':>8}{'hit B':>8}{'edge A':>8}{'edge B':>8}{'IC A':>8}{'IC B':>8}"
              f"  significant")
    print(header)
    print("-" * len(header))

    verdicts = []
    for n in names:
        x, y = ra.get(n, {}), rb.get(n, {})
        sig = []
        if x.get("significant"):
            sig.append("A")
        if y.get("significant"):
            sig.append("B")
        print(f"{n:<22}{x.get('n_calls', 0):>8}{y.get('n_calls', 0):>8}"
              f"{x.get('n_effective', 0):>8}{y.get('n_effective', 0):>8}"
              f"{fmt(x.get('hit_rate'), width=8)}{fmt(y.get('hit_rate'), width=8)}"
              f"{fmt(x.get('edge_over_base'), width=8)}{fmt(y.get('edge_over_base'), width=8)}"
              f"{fmt(x.get('information_coefficient'), pct=False, width=8)}"
              f"{fmt(y.get('information_coefficient'), pct=False, width=8)}"
              f"  {','.join(sig) or 'neither'}")
        verdicts.append((n, x, y, bool(sig)))

    print("\nREAD THIS BEFORE READING THE TABLE")
    print("-" * 34)
    spoke = [n for n, x, y, _ in verdicts
             if not x.get("n_calls") and y.get("n_calls")]
    silent = [n for n, x, y, _ in verdicts
              if x.get("n_calls") and not y.get("n_calls")]
    if spoke:
        print(f"* Modules that went from silent to speaking: {', '.join(spoke)}")
    if silent:
        print(f"* Modules that went from speaking to silent: {', '.join(silent)}")
    any_sig = [n for n, _, _, s in verdicts if s]
    if any_sig:
        print(f"* Cleared significance in at least one arm: {', '.join(any_sig)}")
    else:
        print("* NOTHING here cleared significance in either arm. A module that")
        print("  now speaks is a module that now speaks — it is not evidence that")
        print("  it speaks WELL. Coverage improved; skill is unproven.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
