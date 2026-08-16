"""
scripts/preregistered_test.py
=============================
One fixed hypothesis, every market, no searching.

The genome below is the exact winner of the NSE campaign. It was chosen by a
1,703-trial search on Indian data and NOTHING has been tuned since. Running it
unchanged on other markets is therefore a real out-of-sample test with a trial
count of one, which is the only honest way to ask "does this generalise?"

What would NOT be honest, and is the reason this script hardcodes the genome
rather than taking parameters: running a search on each new market, picking
that market's winner, and presenting the collection as replication. That is
the same data-mining the lab exists to refuse, wearing a passport.

    venv\\Scripts\\python.exe scripts\\preregistered_test.py

Every panel is survivorship-biased, which inflates long-only results. A
failure here is trustworthy; a success is suggestive and no more.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── THE HYPOTHESIS. Formed on NSE 2005-2026. Do not edit to fit a result. ──
HYPOTHESIS = {
    "template": "rsi_reversal",
    "params": {
        "buy_below": 25, "sell_above": 79, "period": 19,
        "direction": "long", "vol_target": 0.118, "vol_lookback": 63,
    },
}

PANELS = ["nse", "sp500", "europe", "japan", "asiapac", "americas"]


def main() -> int:
    from src.evolution import Genome, deflated_sharpe
    from src.evolution.backtest import (max_drawdown, moments, net_returns,
                                        sharpe_of)
    from src.evolution.universe import PANELS as KNOWN
    from src.evolution.universe import describe, load

    genome = Genome(HYPOTHESIS["template"], HYPOTHESIS["params"])
    print(f"pre-registered hypothesis: {genome.key()}")
    print("formed on NSE; every other market below is out-of-sample, n_trials=1\n")

    header = (f"{'market':10} {'names':>5} {'days':>5} {'span':>19} "
              f"{'Sharpe':>7} {'maxDD':>7} {'p(SR>0)':>8}")
    print(header)
    print("-" * len(header))

    results = []
    for name in PANELS:
        if name not in KNOWN:
            continue
        try:
            panel = load(name)
        except FileNotFoundError:
            print(f"{name:10} (not fetched)")
            continue

        rets = net_returns(genome, panel)
        sharpe = sharpe_of(rets)
        if sharpe is None:
            print(f"{name:10} no usable return series")
            continue
        skew, kurt = moments(rets)
        verdict = deflated_sharpe(sharpe, [sharpe], n_observations=len(rets),
                                  skew=skew, kurtosis=kurt, n_trials=1)
        meta = describe(name, panel)
        span = f"{meta['start']}..{meta['end'][:4]}"
        print(f"{name:10} {meta['names']:5d} {meta['days']:5d} {span:>19} "
              f"{sharpe:7.3f} {max_drawdown(rets)*100:6.1f}% "
              f"{str(verdict['probability']):>8}")
        results.append((name, sharpe, max_drawdown(rets)))

    if results:
        out_of_sample = [s for n, s, _ in results if n != "nse"]
        print()
        print(f"markets tested out-of-sample: {len(out_of_sample)}")
        if out_of_sample:
            positive = sum(1 for s in out_of_sample if s > 0)
            mean = sum(out_of_sample) / len(out_of_sample)
            print(f"positive Sharpe in {positive}/{len(out_of_sample)}, "
                  f"mean {mean:.3f}")
            print("\nA consistent, modest positive across independent markets is "
                  "evidence the effect is real.\nIt is NOT evidence it is large "
                  "enough to trade — see the luck bars in the campaigns.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
