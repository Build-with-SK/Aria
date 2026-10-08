"""
src/intelligence/multiple_testing.py
====================================
The charge for looking at many things and keeping the best one.

Model selection here tests a family of hypotheses — one per (model, slice) the
router or the promotion gate examines. At p < 0.05 one test in twenty clears
the bar on noise, so a single p-value is never read on its own:

    holm   family-wise error control (Holm-Bonferroni, uniformly more powerful
           than Bonferroni, no independence assumption). Used for PROMOTION and
           ROUTING, where one false positive puts a model in front of capital.
    bh     false-discovery-rate control (Benjamini-Hochberg). Used only for
           research summaries, where a reader knows some findings are false.

Strategy-level selection keeps its own, stronger charge: the deflated Sharpe
ratio in src/evolution/statistics.py (per-period units; corrected 2026-10-06).
It is not reimplemented or modified here.
"""
from __future__ import annotations

from typing import Mapping


def holm(pvalues: Mapping[str, float]) -> dict[str, float]:
    """Holm-adjusted p-values, monotone, capped at 1."""
    items = sorted(((k, float(p)) for k, p in pvalues.items()), key=lambda kv: kv[1])
    m = len(items)
    out, running = {}, 0.0
    for i, (k, p) in enumerate(items):
        adj = min(1.0, (m - i) * p)
        running = max(running, adj)
        out[k] = round(running, 6)
    return out


def bh(pvalues: Mapping[str, float]) -> dict[str, float]:
    """Benjamini-Hochberg q-values, monotone, capped at 1."""
    items = sorted(((k, float(p)) for k, p in pvalues.items()), key=lambda kv: kv[1])
    m = len(items)
    out, running = {}, 1.0
    for i in range(m - 1, -1, -1):
        k, p = items[i]
        running = min(running, p * m / (i + 1))
        out[k] = round(min(1.0, running), 6)
    return out


def significant(pvalues: Mapping[str, float], *, alpha: float = 0.05,
                method: str = "holm") -> dict[str, bool]:
    adj = holm(pvalues) if method == "holm" else bh(pvalues)
    return {k: v < alpha for k, v in adj.items()}


def family_note(n_tests: int, alpha: float = 0.05) -> str:
    return (f"{n_tests} hypotheses tested; about {n_tests * alpha:.1f} would clear "
            f"p<{alpha} by chance alone, so each is judged on its Holm-adjusted value")
