"""
src/evolution/
==============
Evolutionary strategy discovery with a bar that knows how hard you searched.

    from src.evolution import run_campaign
    run_campaign(population=200, generations=15)

Breed thousands of strategies from quant_lab's templates, score them on
purged walk-forward folds, then make the best sit an exam on a holdout the
search never touched — and require them to beat the Sharpe that the luckiest
of that many worthless strategies would have posted.

Most campaigns return no survivors. That is the point: see statistics.py.

NOTHING HERE TRADES. It produces candidates; the human approves.
"""
from .backtest import net_returns, sharpe_of
from .genome import Genome, SPACE, crossover, mutate, random_genome
from .lab import (SURVIVAL_THRESHOLD, Campaign, Candidate, Evolution, history,
                  run_campaign)
from .statistics import (deflated_sharpe, expected_max_sharpe,
                         purged_walk_forward)
from .universe import describe as describe_universe
from .universe import load as load_universe

__all__ = [
    "Campaign",
    "Candidate",
    "Evolution",
    "Genome",
    "SPACE",
    "SURVIVAL_THRESHOLD",
    "crossover",
    "describe_universe",
    "deflated_sharpe",
    "expected_max_sharpe",
    "history",
    "load_universe",
    "mutate",
    "net_returns",
    "purged_walk_forward",
    "random_genome",
    "run_campaign",
    "sharpe_of",
]
