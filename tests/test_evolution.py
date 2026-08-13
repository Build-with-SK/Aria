"""
tests/test_evolution.py
=======================
The lab's one real promise: it does not hand you a coin flip.

The headline test is test_pure_noise_produces_no_survivors — breed hundreds of
strategies against random walks, where by construction there is nothing to
find, and confirm nothing survives. A search that cannot fail this test is a
data-mining machine with a progress bar.

The fast backtester is also checked cell-for-cell against quant_lab's loop,
so the speed-up can never quietly become a different strategy.
"""
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from src.evolution import (SPACE, crossover, deflated_sharpe,  # noqa: E402
                           expected_max_sharpe, mutate, purged_walk_forward,
                           random_genome)
from src.evolution.backtest import fast_positions, sharpe_of  # noqa: E402
from src.evolution.genome import Genome  # noqa: E402
from src.evolution.lab import Evolution, _parse_key  # noqa: E402


def _prices(days=900, seed=0, drift=0.0, cols=6):
    """Geometric random walks — no signal, by construction."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-01", periods=days)
    data = {}
    for c in range(cols):
        steps = rng.normal(drift, 0.015, days)
        data[f"S{c}"] = 100 * np.exp(np.cumsum(steps))
    return pd.DataFrame(data, index=idx)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from src.evolution import lab
    monkeypatch.setattr(lab, "LAB_DIR", tmp_path)
    monkeypatch.setattr(lab, "CAMPAIGNS", tmp_path / "campaigns.jsonl")


# ══════════════════════════════════════ the headline claim

def test_pure_noise_produces_no_survivors():
    """Hundreds of strategies bred on random walks must yield nothing.

    This is the whole thesis. On noise there is nothing to find, so any
    survivor is the search fooling itself — which is exactly what happens
    when you keep the best of 4,000 and call one out-of-sample pass proof.
    """
    closes = _prices(seed=7)
    lab = Evolution(closes, seed=1, population=60, generations=6)
    campaign = lab.run()

    assert campaign.evaluated > 100, "the search must really have tried many"
    assert campaign.survivors == [], (
        f"noise produced {len(campaign.survivors)} 'survivors' — "
        "the deflation is not working"
    )
    assert "no survivors" in campaign.note
    assert campaign.luck_threshold > 0, "luck must have a positive bar"


def test_the_luck_bar_rises_with_the_number_of_trials():
    """Trying more strategies must make the bar harder, not easier."""
    rng = random.Random(4)
    sharpes = [rng.gauss(0, 1.0) for _ in range(500)]

    few = expected_max_sharpe(sharpes, n_trials=10)
    many = expected_max_sharpe(sharpes, n_trials=4000)
    assert many > few > 0
    assert many > 3.0, "with 4,000 trials luck alone reaches a high Sharpe"


def test_a_strong_result_from_few_trials_beats_a_stronger_one_from_many():
    """The same Sharpe means less when more strategies were tried."""
    rng = random.Random(2)
    spread = [rng.gauss(0, 1.0) for _ in range(200)]

    honest = deflated_sharpe(2.0, spread, n_observations=1000, n_trials=5)
    mined = deflated_sharpe(2.0, spread, n_observations=1000, n_trials=100_000)

    assert honest["probability"] > mined["probability"]
    assert honest["beats_luck"] and not mined["beats_luck"]
    assert "lucky search" in mined["verdict"]


def test_negative_skew_is_penalised():
    """A Sharpe earned by selling tail risk is worth less than the same
    number earned symmetrically.

    Deliberately near the bar and on a short sample: far above it every
    probability saturates at 1.0 and the comparison stops meaning anything.
    """
    rng = random.Random(3)
    spread = [rng.gauss(0, 1.0) for _ in range(100)]
    threshold = expected_max_sharpe(spread, n_trials=100)

    symmetric = deflated_sharpe(threshold + 0.3, spread, 120,
                                skew=0.0, kurtosis=3.0)
    tail_risk = deflated_sharpe(threshold + 0.3, spread, 120,
                                skew=-2.0, kurtosis=12.0)

    assert 0 < tail_risk["probability"] < symmetric["probability"] < 1.0


def test_deflated_sharpe_reports_the_bar_not_just_the_verdict():
    spread = [0.1, 0.4, -0.2, 0.9, 0.3]
    out = deflated_sharpe(1.8, spread, n_observations=500)
    assert set(out) >= {"probability", "threshold", "observed", "n_trials", "verdict"}
    assert out["observed"] == 1.8


def test_insufficient_data_says_so_rather_than_guessing():
    out = deflated_sharpe(2.0, [0.1, 0.2], n_observations=2)
    assert out["probability"] is None
    assert out["verdict"] == "insufficient data"


# ══════════════════════════════════════ leakage

def test_walk_forward_never_tests_before_it_trains():
    for train, test in purged_walk_forward(1000, n_folds=5, embargo=10):
        assert test.start >= train.stop, "a test window must follow its training"


def test_embargo_leaves_a_real_gap():
    splits = purged_walk_forward(1000, n_folds=5, embargo=10)
    assert splits
    for train, test in splits:
        assert test.start - train.stop >= 10, "adjacent days leak into each other"


def test_walk_forward_declines_when_there_is_too_little_history():
    assert purged_walk_forward(10, n_folds=5) == []


def test_holdout_is_never_seen_during_evolution():
    closes = _prices(days=800, seed=11)
    lab = Evolution(closes, seed=3, population=10, generations=2)
    assert len(lab.holdout_closes) > 0
    assert len(lab.evolve_closes) + len(lab.holdout_closes) == len(closes)
    # Fitness must be computable from the evolution window alone.
    assert lab.evolve_closes.index.max() < lab.holdout_closes.index.min()


# ══════════════════════════════════════ the fast backtester

@pytest.mark.parametrize("template,params", [
    ("mean_reversion_z", {"window": 20, "entry_z": -2.0, "exit_z": 0.0}),
    ("mean_reversion_z", {"window": 45, "entry_z": -1.2, "exit_z": 0.6}),
    ("rsi_reversal", {"period": 14, "buy_below": 30, "sell_above": 70}),
    ("rsi_reversal", {"period": 9, "buy_below": 20, "sell_above": 80}),
])
def test_fast_positions_match_the_original_loop(template, params):
    """The speed-up must be a speed-up, never a different strategy."""
    from src.brain.quant_lab import _positions

    closes = _prices(days=400, seed=5, cols=4)
    slow = _positions(template, params, closes)
    fast = fast_positions(template, params, closes)
    pd.testing.assert_frame_equal(slow, fast, check_dtype=False)


def test_sharpe_of_a_dead_strategy_is_none_not_zero():
    """A strategy that never traded has no Sharpe. Scoring it zero would let
    it outrank genuine losers and drift through selection doing nothing."""
    flat = pd.Series([0.0] * 200)
    assert sharpe_of(flat) is None
    assert sharpe_of(pd.Series([0.01] * 5)) is None


# ══════════════════════════════════════ the genome

def test_random_genomes_stay_inside_their_bounds():
    rng = random.Random(0)
    for _ in range(300):
        g = random_genome(rng)
        for name, (lo, hi, _kind) in SPACE[g.template].items():
            assert lo <= g.params[name] <= hi, f"{g.template}.{name} out of bounds"


def test_mutation_stays_inside_bounds():
    rng = random.Random(1)
    for _ in range(300):
        g = mutate(random_genome(rng), rng, rate=1.0)
        for name, (lo, hi, _kind) in SPACE[g.template].items():
            assert lo <= g.params[name] <= hi


def test_nonsense_orderings_are_repaired_not_scored():
    """fast >= slow is not a bad strategy, it is not a strategy."""
    rng = random.Random(2)
    for _ in range(200):
        child = crossover(random_genome(rng, "ma_cross"),
                          random_genome(rng, "ma_cross"), rng)
        assert child.params["fast"] < child.params["slow"]

    for _ in range(200):
        child = crossover(random_genome(rng, "rsi_reversal"),
                          random_genome(rng, "rsi_reversal"), rng)
        assert child.params["buy_below"] < child.params["sell_above"]

    for _ in range(200):
        child = crossover(random_genome(rng, "mean_reversion_z"),
                          random_genome(rng, "mean_reversion_z"), rng)
        assert child.params["exit_z"] > child.params["entry_z"]


def test_crossover_across_templates_picks_one_rather_than_averaging():
    rng = random.Random(3)
    a = random_genome(rng, "breakout")
    b = random_genome(rng, "rsi_reversal")
    child = crossover(a, b, rng)
    assert child.template in ("breakout", "rsi_reversal")
    assert set(child.params) == set(SPACE[child.template])


def test_genome_key_round_trips():
    rng = random.Random(9)
    for _ in range(50):
        g = random_genome(rng)
        back = _parse_key(g.key())
        assert back.template == g.template
        assert back.params == g.params


def test_identical_genomes_are_counted_once():
    """The trial count is what the deflation depends on; double-counting a
    re-evaluated genome would inflate it and make the bar dishonestly high."""
    closes = _prices(days=600, seed=13)
    lab = Evolution(closes, seed=5, population=8, generations=1)
    g = Genome("ma_cross", {"fast": 10, "slow": 50})
    lab.fitness(g)
    lab.fitness(g)
    assert len([k for k in lab.trials if k == g.key()]) == 1


# ══════════════════════════════════════ reporting

def test_campaign_is_persisted_and_readable():
    from src.evolution import lab as lab_module

    closes = _prices(days=700, seed=21)
    Evolution(closes, seed=2, population=12, generations=2).run()
    rows = lab_module.history()
    assert rows and "evaluated" in rows[0]


def test_campaign_reports_no_survivors_as_a_success_not_an_error():
    closes = _prices(days=700, seed=31)
    campaign = Evolution(closes, seed=8, population=20, generations=3).run()
    assert campaign.finished_at
    assert "success" in campaign.note or campaign.survivors


def test_too_little_history_is_reported_not_crashed():
    closes = _prices(days=30, seed=1)
    campaign = Evolution(closes, seed=1, population=10, generations=2).run()
    assert campaign.survivors == []
    assert "not enough history" in campaign.note


def test_the_lab_cannot_trade():
    """The brain proposes, the human approves — and the lab only researches."""
    import ast

    for name in ("lab.py", "genome.py", "backtest.py", "statistics.py"):
        tree = ast.parse((Path(__file__).parent.parent / "src" / "evolution" / name)
                         .read_text(encoding="utf-8"))
        referenced = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
                     {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        forbidden = {"approve_and_execute", "OrderManager", "submit_order",
                     "place_order", "AlpacaBroker", "IBKRBroker"}
        assert not (referenced & forbidden), f"{name} must not reach execution"
