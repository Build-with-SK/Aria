"""
src/evolution/genome.py
=======================
The searchable space: a strategy as a thing that can be bred.

A genome is one of quant_lab's templates plus its parameters. Nothing here
invents new maths — it makes the EXISTING templates searchable, so evolution
explores parameter space rather than inventing indicators nobody has tested.

Bounds are deliberately wide but not absurd. A lookback of 3 days on daily
bars is noise and a lookback of 700 days does not fit in three years of
history; letting the search waste generations on either is not open-
mindedness, it is a slower search.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

# template -> {param: (low, high, kind)} where kind is "int" or "float"
SPACE: dict[str, dict[str, tuple]] = {
    "momentum_topn": {
        "lookback": (20, 252, "int"),
        "top_n": (1, 10, "int"),
    },
    "ma_cross": {
        "fast": (5, 60, "int"),
        "slow": (30, 250, "int"),
    },
    "mean_reversion_z": {
        "window": (10, 120, "int"),
        "entry_z": (-3.5, -0.5, "float"),
        "exit_z": (-0.5, 1.5, "float"),
    },
    "rsi_reversal": {
        "period": (5, 40, "int"),
        "buy_below": (10, 45, "int"),
        "sell_above": (55, 90, "int"),
    },
    "breakout": {
        "window": (10, 150, "int"),
        "exit_ma": (5, 100, "int"),
    },
}

TEMPLATES = tuple(SPACE)

# Portfolio construction, orthogonal to the signal. Every genome carries these
# whatever its template, because "which way may I face" and "how big may I be"
# are not properties of a moving-average crossover — they are decisions taken
# on top of one. Keeping them separate multiplies the search space instead of
# doubling the number of templates.
OVERLAY: dict[str, tuple] = {
    # long: the original behaviour, 0/1 positions.
    # long_short: the same signal mapped symmetrically to -1/0/+1.
    "direction": (("long", "long_short"), None, "choice"),

    # Annualised volatility to size the book at. 0.0 means off — kept as a
    # reachable value so the search can decide targeting is not worth it
    # rather than being forced to use it.
    "vol_target": (0.0, 0.30, "float"),
    "vol_lookback": (20, 120, "int"),
}

# Leverage is capped rather than searched. As realised vol approaches zero the
# ratio target/realised approaches infinity, and a strategy that discovers it
# can borrow without limit during the calmest month of the sample will always
# win a backtest and always blow up live.
MAX_LEVERAGE = 3.0


def full_space(template: str) -> dict[str, tuple]:
    """Signal parameters plus the overlay every genome carries."""
    return {**SPACE[template], **OVERLAY}


@dataclass(frozen=True)
class Genome:
    template: str
    params: dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        """Stable identity, so the same strategy is never counted twice."""
        bits = ",".join(f"{k}={self.params[k]}" for k in sorted(self.params))
        return f"{self.template}({bits})"

    def __hash__(self) -> int:
        return hash(self.key())


def _draw(low, high, kind, rng: random.Random):
    if kind == "choice":
        return rng.choice(low)          # `low` holds the options
    if kind == "int":
        return rng.randint(low, high)
    return round(rng.uniform(low, high), 3)


def _repair(genome: Genome) -> Genome:
    """Fix orderings that are meaningless rather than merely bad.

    A crossover can produce fast >= slow, or an RSI that buys above where it
    sells. Those are not strategies with poor fitness — they are nonsense, and
    letting them into the population wastes evaluations proving it.
    """
    p = dict(genome.params)
    if genome.template == "ma_cross" and p["fast"] >= p["slow"]:
        p["fast"], p["slow"] = min(p["fast"], p["slow"]), max(p["fast"], p["slow"])
        if p["fast"] == p["slow"]:
            p["slow"] = min(250, p["fast"] + 10)
    if genome.template == "rsi_reversal" and p["buy_below"] >= p["sell_above"]:
        p["buy_below"], p["sell_above"] = (min(p["buy_below"], p["sell_above"]),
                                           max(p["buy_below"], p["sell_above"]))
        if p["buy_below"] == p["sell_above"]:
            p["sell_above"] = min(90, p["buy_below"] + 10)
    if genome.template == "mean_reversion_z" and p["exit_z"] <= p["entry_z"]:
        p["exit_z"] = p["entry_z"] + 0.5

    # A long_short mean-reversion genome is symmetric about zero, so its
    # neutral band is ±|exit_z|. If that band swallows the entry level the
    # strategy can never open a position — not a weak genome, an inert one.
    if (genome.template == "mean_reversion_z"
            and p.get("direction") == "long_short"
            and abs(p["exit_z"]) >= abs(p["entry_z"])):
        p["exit_z"] = round(abs(p["entry_z"]) * 0.5, 3)

    if "vol_target" in p and p["vol_target"] < 0.01:
        p["vol_target"] = 0.0           # snap noise to a clean "off"
    return Genome(genome.template, p)


def random_genome(rng: random.Random, template: str | None = None) -> Genome:
    template = template or rng.choice(TEMPLATES)
    params = {name: _draw(lo, hi, kind, rng)
              for name, (lo, hi, kind) in full_space(template).items()}
    return _repair(Genome(template, params))


def mutate(genome: Genome, rng: random.Random, rate: float = 0.3) -> Genome:
    """Jitter parameters by a fraction of their range.

    Gaussian rather than uniform-resample: a good strategy's neighbours are
    usually also good, and re-rolling a parameter from scratch throws away
    everything the search has learned about where it should be. Categorical
    genes have no neighbours, so those do flip outright.
    """
    params = dict(genome.params)
    for name, (lo, hi, kind) in full_space(genome.template).items():
        if rng.random() >= rate:
            continue
        if kind == "choice":
            params[name] = rng.choice(lo)
            continue
        span = (hi - lo) * 0.2
        value = params.get(name, lo) + rng.gauss(0, span)
        value = max(lo, min(hi, value))
        params[name] = int(round(value)) if kind == "int" else round(value, 3)
    return _repair(Genome(genome.template, params))


def crossover(a: Genome, b: Genome, rng: random.Random) -> Genome:
    """Breed two genomes.

    Same template: mix parameters, sometimes blending rather than picking, so
    offspring can land between the parents instead of only on them. Different
    templates: there is no meaningful way to average a breakout with an RSI,
    so one parent's template wins outright and takes its parameters — the
    crossover degenerates to selection, which is honest.
    """
    if a.template != b.template:
        return mutate(rng.choice([a, b]), rng, rate=0.2)

    params = {}
    for name, (lo, hi, kind) in full_space(a.template).items():
        source = a if rng.random() < 0.5 else b
        params[name] = source.params.get(name, _draw(lo, hi, kind, rng))
        # Categorical genes cannot be averaged — "half long, half long_short"
        # is not a direction — so only numeric genes blend.
        if kind != "choice" and rng.random() < 0.3:
            mixed = (a.params.get(name, params[name])
                     + b.params.get(name, params[name])) / 2
            params[name] = int(round(mixed)) if kind == "int" else round(mixed, 3)
    return _repair(Genome(a.template, params))
