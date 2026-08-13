"""
src/evolution/lab.py
====================
THE BREEDING LAB — thousands of strategies, and a bar that knows it.

The loop is the familiar one: generate a population, score it, breed the
winners, mutate, inject fresh blood, repeat. What matters is what happens
after, because the search itself is the easy part and the reason most such
labs quietly produce nothing.

    THE PROBLEM.   Evaluate 4,000 strategies on noise and the best will post a
    Sharpe near 3 — not because it works, but because 4,000 coins were
    flipped. Sit the winners an exam on unseen data and a slice pass that too,
    for the same reason. Every step feels rigorous and the conclusion is still
    wrong, which is what makes this failure mode expensive.

    THE FIX.       Count every strategy ever evaluated, and require survivors
    to beat the Sharpe the LUCKIEST of that many worthless strategies would
    have posted — then convert the margin into a probability that corrects for
    the spread of trial results and the strategy's own skew and fat tails.
    That is the deflated Sharpe ratio, and it is the difference between
    "found something" and "searched hard".

Three layers of separation, each doing a different job:

    fitness folds   purged walk-forward INSIDE the evolution window, so even
                    the number evolution optimises is out-of-sample and the
                    search cannot win by memorising one regime
    holdout         the tail of history, never touched until a campaign ends
    deflation       the bar the holdout result must clear, set by how many
                    strategies were tried to find it

A campaign that returns nothing is the expected outcome and is reported as
success, not failure. NOTHING HERE TRADES: it produces candidates, the human
approves, exactly as everything else in this system.
"""
from __future__ import annotations

import json
import logging
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .backtest import moments, net_returns, sharpe_of
from .genome import Genome, crossover, mutate, random_genome
from .statistics import deflated_sharpe, purged_walk_forward

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
LAB_DIR = ROOT / "data" / "evolution"
CAMPAIGNS = LAB_DIR / "campaigns.jsonl"

# A survivor must clear this probability that its true Sharpe is positive,
# AFTER being charged for the number of strategies tried. 0.95 is strict on
# purpose: the cost of a false survivor is real money and the cost of a false
# rejection is one more campaign.
SURVIVAL_THRESHOLD = 0.95
HOLDOUT_FRACTION = 0.25


@dataclass
class Candidate:
    genome_key: str
    template: str
    params: dict
    fitness: float                       # mean Sharpe across purged folds
    holdout_sharpe: float | None = None
    deflated: dict = field(default_factory=dict)
    survived: bool = False


@dataclass
class Campaign:
    started_at: str
    generations: int
    population: int
    evaluated: int                       # DISTINCT genomes — the trial count
    holdout_days: int
    survivors: list = field(default_factory=list)
    best_in_sample: list = field(default_factory=list)
    luck_threshold: float = 0.0
    finished_at: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class Evolution:
    """One campaign: breed, then judge honestly."""

    def __init__(self, closes, seed: int | None = None,
                 population: int = 120, generations: int = 12,
                 elite: int = 6, immigrants: int = 12):
        self.closes = closes
        self.rng = random.Random(seed)
        self.population_size = population
        self.generations = generations
        self.elite = elite
        self.immigrants = immigrants

        # Every distinct genome ever scored, and its fitness. This IS the
        # trial count the deflation depends on — undercount it and the whole
        # exercise becomes the thing it exists to prevent.
        self.trials: dict[str, float] = {}

        split = int(len(closes) * (1 - HOLDOUT_FRACTION))
        self.evolve_closes = closes.iloc[:split]
        self.holdout_closes = closes.iloc[split:]
        self.folds = purged_walk_forward(len(self.evolve_closes), n_folds=4)

    # ── scoring ──────────────────────────────────────────────────────────

    def fitness(self, genome: Genome) -> float | None:
        """Mean Sharpe across purged walk-forward folds.

        Averaging across folds rather than scoring one long window is what
        stops a strategy winning by being spectacular in one regime and
        useless everywhere else. A genome that cannot produce a Sharpe in
        most folds is discarded rather than scored zero.
        """
        cached = self.trials.get(genome.key())
        if cached is not None:
            return cached
        if not self.folds:
            return None

        scores = []
        for _, test in self.folds:
            window = self.evolve_closes.iloc[test.start:test.stop]
            value = sharpe_of(net_returns(genome, window))
            if value is not None:
                scores.append(value)

        if len(scores) < max(2, len(self.folds) // 2):
            self.trials[genome.key()] = float("-inf")
            return None

        score = sum(scores) / len(scores)
        self.trials[genome.key()] = score
        return score

    # ── the loop ─────────────────────────────────────────────────────────

    def _tournament(self, scored: list[tuple[Genome, float]], k: int = 3) -> Genome:
        picks = [self.rng.choice(scored) for _ in range(k)]
        return max(picks, key=lambda pair: pair[1])[0]

    def run(self) -> Campaign:
        started = datetime.now(timezone.utc).isoformat()
        campaign = Campaign(
            started_at=started, generations=self.generations,
            population=self.population_size, evaluated=0,
            holdout_days=len(self.holdout_closes),
        )
        if not self.folds:
            campaign.note = (
                f"not enough history to evolve: {len(self.evolve_closes)} rows "
                "left after the holdout split"
            )
            campaign.finished_at = datetime.now(timezone.utc).isoformat()
            return campaign

        population = [random_genome(self.rng) for _ in range(self.population_size)]

        for generation in range(self.generations):
            scored = []
            for genome in population:
                value = self.fitness(genome)
                if value is not None:
                    scored.append((genome, value))

            if not scored:
                population = [random_genome(self.rng)
                              for _ in range(self.population_size)]
                continue

            scored.sort(key=lambda pair: pair[1], reverse=True)
            logger.info("gen %d/%d — best fitness %.2f over %d distinct trials",
                        generation + 1, self.generations, scored[0][1], len(self.trials))

            survivors = [g for g, _ in scored[:self.elite]]
            children = list(survivors)

            # Fresh random genomes every generation: without them the
            # population converges on one template's neighbourhood and the
            # search stops being a search. This is the reel's "adding brand
            # new strategies", and it is also what keeps the trial count
            # honest — the space really is being explored.
            children += [random_genome(self.rng) for _ in range(self.immigrants)]

            while len(children) < self.population_size:
                child = crossover(self._tournament(scored),
                                  self._tournament(scored), self.rng)
                children.append(mutate(child, self.rng))
            population = children

        campaign.evaluated = len(self.trials)
        return self._judge(campaign)

    # ── the exam ─────────────────────────────────────────────────────────

    def _judge(self, campaign: Campaign) -> Campaign:
        """Sit the best candidates an exam on data evolution never saw."""
        finite = {k: v for k, v in self.trials.items() if v > float("-inf")}
        trial_sharpes = list(finite.values())
        ranked = sorted(finite.items(), key=lambda kv: kv[1], reverse=True)[:10]

        by_key = {}
        for genome_key, _ in ranked:
            by_key[genome_key] = _parse_key(genome_key)

        holdout_n = len(self.holdout_closes)
        for genome_key, fit in ranked:
            genome = by_key[genome_key]
            if genome is None:
                continue
            rets = net_returns(genome, self.holdout_closes)
            observed = sharpe_of(rets)
            skew, kurt = moments(rets)

            verdict = deflated_sharpe(
                observed_sharpe=observed if observed is not None else float("nan"),
                trial_sharpes=trial_sharpes,
                n_observations=holdout_n,
                skew=skew, kurtosis=kurt,
                n_trials=len(finite),
            )
            candidate = Candidate(
                genome_key=genome_key, template=genome.template,
                params=genome.params, fitness=round(fit, 4),
                holdout_sharpe=round(observed, 4) if observed is not None else None,
                deflated=verdict,
                survived=bool(verdict.get("probability") is not None
                              and verdict["probability"] >= SURVIVAL_THRESHOLD),
            )
            campaign.best_in_sample.append(asdict(candidate))
            if candidate.survived:
                campaign.survivors.append(asdict(candidate))
            campaign.luck_threshold = verdict.get("threshold", 0.0)

        campaign.finished_at = datetime.now(timezone.utc).isoformat()
        if not campaign.survivors:
            campaign.note = (
                f"no survivors — with {len(finite)} trials, luck alone reaches a "
                f"Sharpe of {campaign.luck_threshold:.2f} and nothing beat it "
                "convincingly. This is the expected result and it is a success: "
                "the lab declined to hand you a coin flip."
            )
        else:
            campaign.note = (
                f"{len(campaign.survivors)} of {len(finite)} strategies cleared a "
                f"luck threshold of {campaign.luck_threshold:.2f} on data they had "
                "never seen."
            )
        _persist(campaign)
        return campaign


def _parse_key(key: str) -> Genome | None:
    """Rebuild a genome from its key. Kept next to Genome.key()."""
    try:
        template, _, rest = key.partition("(")
        params = {}
        for chunk in rest.rstrip(")").split(","):
            if not chunk:
                continue
            name, _, value = chunk.partition("=")
            if value.lstrip("-").replace(".", "", 1).isdigit():
                params[name] = float(value) if "." in value else int(value)
            else:
                params[name] = value          # categorical, e.g. direction
        return Genome(template, params)
    except (ValueError, AttributeError):
        logger.warning("could not parse genome key %r", key)
        return None


def _persist(campaign: Campaign) -> None:
    LAB_DIR.mkdir(parents=True, exist_ok=True)
    with CAMPAIGNS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(campaign.as_dict(), ensure_ascii=False, default=str) + "\n")


def run_campaign(closes=None, seed: int | None = None, population: int = 120,
                 generations: int = 12) -> dict:
    """Run one campaign. Loads quant_lab's cached basket when given no data."""
    if closes is None:
        from src.brain.quant_lab import get_lab
        closes = get_lab()._get_closes()
    lab = Evolution(closes, seed=seed, population=population,
                    generations=generations)
    return lab.run().as_dict()


def history(limit: int = 20) -> list[dict]:
    if not CAMPAIGNS.exists():
        return []
    rows = []
    for line in CAMPAIGNS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows[-limit:][::-1]
