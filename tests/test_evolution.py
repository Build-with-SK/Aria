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
from src.evolution.backtest import (fast_positions, net_returns,  # noqa: E402
                                    sharpe_of, signed_positions)
from src.evolution.genome import (MAX_LEVERAGE, OVERLAY, Genome,  # noqa: E402
                                  full_space)
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
    assert set(child.params) == set(full_space(child.template))


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


# ══════════════════════════════════════ shorts

def test_long_only_genomes_are_untouched_by_the_widening():
    """The overlay must not change what a long-only genome does."""
    closes = _prices(days=400, seed=17, cols=4)
    params = {"window": 20, "entry_z": -2.0, "exit_z": 0.0, "direction": "long"}
    with_overlay = signed_positions("mean_reversion_z", params, closes)
    original = fast_positions("mean_reversion_z", params, closes)
    pd.testing.assert_frame_equal(with_overlay, original)
    assert (original >= 0).all().all()


@pytest.mark.parametrize("template,params", [
    ("mean_reversion_z", {"window": 20, "entry_z": -1.5, "exit_z": 0.3}),
    ("rsi_reversal", {"period": 14, "buy_below": 30, "sell_above": 70}),
    ("ma_cross", {"fast": 10, "slow": 50}),
    ("breakout", {"window": 30, "exit_ma": 20}),
    ("momentum_topn", {"lookback": 60, "top_n": 2}),
])
def test_long_short_actually_goes_short(template, params):
    closes = _prices(days=700, seed=23, cols=6)
    pos = signed_positions(template, {**params, "direction": "long_short"}, closes)
    assert (pos < 0).any().any(), f"{template} never took a short"
    assert (pos > 0).any().any(), f"{template} never took a long"
    assert pos.abs().max().max() <= 1.0, "positions must stay within -1..+1"


def test_market_neutral_book_is_not_secretly_levered():
    """Three longs against three shorts sums to zero. Normalising by the
    SIGNED sum would divide by ~0 and hand the strategy huge leverage in
    exactly the state it was built to hold."""
    idx = pd.bdate_range("2022-01-01", periods=5)
    pos = pd.DataFrame(
        [[1.0, 1.0, 1.0, -1.0, -1.0, -1.0]] * 5,
        index=idx, columns=[f"S{i}" for i in range(6)])
    gross = pos.abs().sum(axis=1)
    weights = pos.div(gross.replace(0, 1), axis=0)
    assert weights.abs().sum(axis=1).round(6).eq(1.0).all(), "gross exposure must be 1x"
    assert abs(weights.sum(axis=1).iloc[0]) < 1e-9, "and the book stays neutral"


def test_a_short_book_profits_when_prices_fall():
    """Direction has to mean something in the returns, not just the signs."""
    idx = pd.bdate_range("2022-01-01", periods=300)
    falling = pd.DataFrame({"S0": 100 * (0.999 ** np.arange(300))}, index=idx)

    long_only = Genome("ma_cross", {"fast": 5, "slow": 20, "direction": "long",
                                    "vol_target": 0.0, "vol_lookback": 60})
    both_ways = Genome("ma_cross", {"fast": 5, "slow": 20,
                                    "direction": "long_short",
                                    "vol_target": 0.0, "vol_lookback": 60})
    assert net_returns(both_ways, falling).sum() > net_returns(long_only, falling).sum()


# ══════════════════════════════════════ vol targeting

def test_vol_targeting_does_not_peek_at_the_future():
    """The sizing at time t must not depend on returns after t.

    This is the bug that makes every backtest beautiful and every live
    account poor, and it is invisible in the results.
    """
    closes = _prices(days=500, seed=29, cols=4)
    genome = Genome("ma_cross", {"fast": 10, "slow": 40, "direction": "long",
                                 "vol_target": 0.10, "vol_lookback": 40})
    baseline = net_returns(genome, closes)

    tampered = closes.copy()
    cut = 400
    tampered.iloc[cut:] *= 1.5          # rewrite the future only

    after = net_returns(genome, tampered)
    pd.testing.assert_series_equal(
        baseline.iloc[:cut - 1], after.iloc[:cut - 1],
        check_names=False,
        obj="returns before the tampering must be unchanged")


def test_leverage_is_capped_when_volatility_collapses():
    """As realised vol approaches zero the target/realised ratio approaches
    infinity. A search will find that corner if it is left open."""
    idx = pd.bdate_range("2022-01-01", periods=400)
    # A near-straight line: realised vol is tiny but not zero.
    calm = pd.DataFrame(
        {"S0": 100 * (1 + 1e-6 * np.arange(400)),
         "S1": 100 * (1 + 1.1e-6 * np.arange(400))}, index=idx)

    from src.evolution.backtest import _vol_target

    genome = Genome("ma_cross", {"fast": 5, "slow": 20, "direction": "long",
                                 "vol_target": 0.20, "vol_lookback": 30})
    rets = net_returns(genome, calm)
    assert np.isfinite(rets).all(), "leverage must never become inf or nan"

    # Assert on the sizing itself rather than inferring it from returns, which
    # also carry turnover costs.
    pos = signed_positions("ma_cross", genome.params, calm)
    weights = pos.div(pos.abs().sum(axis=1).replace(0, 1), axis=0)
    daily = calm.pct_change().fillna(0.0)
    sized = _vol_target(weights, daily, 0.20, lookback=30)

    assert np.isfinite(sized.to_numpy()).all()
    assert sized.abs().sum(axis=1).max() <= MAX_LEVERAGE + 1e-9, (
        "gross exposure must never exceed the leverage cap"
    )


def test_vol_targeting_moves_realised_volatility_toward_the_target():
    closes = _prices(days=900, seed=31, cols=6)
    base = {"fast": 10, "slow": 40, "direction": "long", "vol_lookback": 60}

    quiet = net_returns(Genome("ma_cross", {**base, "vol_target": 0.05}), closes)
    loud = net_returns(Genome("ma_cross", {**base, "vol_target": 0.25}), closes)

    quiet_vol = float(quiet.tail(400).std() * np.sqrt(252))
    loud_vol = float(loud.tail(400).std() * np.sqrt(252))
    assert quiet_vol < loud_vol, "a lower target must produce a smaller book"


def test_vol_target_can_be_switched_off_and_zero_means_off():
    closes = _prices(days=400, seed=33, cols=4)
    off = Genome("ma_cross", {"fast": 10, "slow": 40, "direction": "long",
                              "vol_target": 0.0, "vol_lookback": 60})
    rets = net_returns(off, closes)
    assert rets.abs().sum() > 0, "with targeting off the book still trades"


def test_tiny_vol_targets_snap_to_off_rather_than_to_a_dust_position():
    rng = random.Random(5)
    g = mutate(Genome("ma_cross", {"fast": 10, "slow": 40, "direction": "long",
                                   "vol_target": 0.001, "vol_lookback": 60}),
               rng, rate=0.0)
    assert g.params["vol_target"] == 0.0


# ══════════════════════════════════════ the widened search

def test_overlay_genes_are_present_and_within_bounds():
    rng = random.Random(11)
    for _ in range(300):
        g = random_genome(rng)
        assert g.params["direction"] in ("long", "long_short")
        assert 0.0 <= g.params["vol_target"] <= 0.30
        assert 20 <= g.params["vol_lookback"] <= 120


def test_genome_key_round_trips_with_categorical_genes():
    rng = random.Random(13)
    for _ in range(100):
        g = random_genome(rng)
        back = _parse_key(g.key())
        assert back is not None
        assert back.params["direction"] == g.params["direction"]
        assert back.params == g.params


def test_long_short_mean_reversion_is_never_inert():
    """A neutral band wider than the entry level can never open a position —
    not a weak genome, an inert one."""
    rng = random.Random(17)
    for _ in range(300):
        g = random_genome(rng, "mean_reversion_z")
        if g.params["direction"] == "long_short":
            assert abs(g.params["exit_z"]) < abs(g.params["entry_z"])


def test_the_widened_space_still_rejects_noise():
    """Shorts and leverage give the search more ways to fool itself. The bar
    has to hold anyway — this is the headline test on the bigger space."""
    closes = _prices(days=900, seed=41)
    campaign = Evolution(closes, seed=3, population=80, generations=6).run()
    assert campaign.evaluated > 150
    assert campaign.survivors == [], (
        f"noise produced {len(campaign.survivors)} survivors on the widened space"
    )


# ══════════════════════════════════════ the drawdown ceiling

def test_max_drawdown_is_measured_as_a_positive_fraction():
    from src.evolution.backtest import max_drawdown

    idx = pd.bdate_range("2022-01-01", periods=4)
    halved = pd.Series([0.0, -0.5, 0.0, 0.0], index=idx)
    assert abs(max_drawdown(halved) - 0.5) < 1e-9
    assert max_drawdown(pd.Series([0.01, 0.01, 0.01], index=idx[:3])) == 0.0


def test_a_deep_drawdown_disqualifies_regardless_of_sharpe(monkeypatch):
    """Sharpe cannot see drawdown. Without a ceiling the search rewards
    leverage — measured on real data, vol targeting nearly doubled holdout
    drawdown to buy 0.12 of Sharpe."""
    from src.evolution import lab as lab_module

    closes = _prices(days=900, seed=51)
    lab = Evolution(closes, seed=1, population=8, generations=1)
    genome = Genome("ma_cross", {"fast": 10, "slow": 40, "direction": "long",
                                 "vol_target": 0.0, "vol_lookback": 60})

    monkeypatch.setattr(lab_module, "max_drawdown", lambda rets: 0.99)
    assert lab.fitness(genome) is None, "a 99% drawdown must not be selectable"
    assert lab.trials[genome.key()] == float("-inf")


def test_a_disqualified_genome_still_counts_as_a_trial():
    """It really was evaluated. Hiding it would deflate the bar survivors
    must clear, which is the exact failure this lab exists to prevent."""
    from src.evolution import lab as lab_module

    closes = _prices(days=900, seed=53)
    lab = Evolution(closes, seed=2, population=8, generations=1)
    genome = Genome("ma_cross", {"fast": 5, "slow": 30, "direction": "long",
                                 "vol_target": 0.0, "vol_lookback": 60})

    original = lab_module.max_drawdown
    lab_module.max_drawdown = lambda rets: 0.99
    try:
        lab.fitness(genome)
    finally:
        lab_module.max_drawdown = original
    assert genome.key() in lab.trials, "disqualified genomes remain trials"


def test_finalists_report_their_holdout_drawdown():
    closes = _prices(days=900, seed=57)
    campaign = Evolution(closes, seed=4, population=30, generations=3).run()
    for row in campaign.best_in_sample:
        assert "holdout_max_dd" in row
        assert row["holdout_max_dd"] is None or row["holdout_max_dd"] >= 0


def test_the_trial_count_includes_disqualified_genomes(monkeypatch):
    """Dropping risk-disqualified genomes from the count would lower the bar
    in proportion to how strict the risk ceiling is — making a tighter risk
    limit look like finding alpha."""
    from src.evolution import lab as lab_module

    closes = _prices(days=1200, seed=61)
    lab = Evolution(closes, seed=6, population=40, generations=3)

    real = lab_module.max_drawdown
    calls = {"n": 0}

    def sometimes_awful(rets):
        calls["n"] += 1
        # Occasionally, so plenty of genomes still survive to be ranked.
        return 0.99 if calls["n"] % 7 == 0 else real(rets)

    monkeypatch.setattr(lab_module, "max_drawdown", sometimes_awful)
    campaign = lab.run()

    disqualified = sum(1 for v in lab.trials.values() if v == float("-inf"))
    assert disqualified > 0, "the fixture must actually disqualify some"
    assert campaign.best_in_sample, "some genomes must still be rankable"
    reported = campaign.best_in_sample[0]["deflated"]["n_trials"]
    assert reported == campaign.evaluated == len(lab.trials)
    assert reported > len(lab.trials) - disqualified


def test_campaign_note_agrees_with_the_reported_trial_count():
    closes = _prices(days=1000, seed=63)
    campaign = Evolution(closes, seed=7, population=30, generations=3).run()
    assert str(campaign.evaluated) in campaign.note


# ══════════════════════════════════════ the universe

def test_universe_declares_its_survivorship_bias(tmp_path, monkeypatch):
    """The caveat must ship with the data, not live only in a docstring.

    Both panels are today's index membership run backwards, so every company
    that failed is missing. A result quoted without that is misleading.
    """
    from src.evolution import universe

    meta = universe.describe("sp500")
    assert meta["survivorship_biased"] is True
    assert "delisted" in meta["caveat"] or "failed" in meta["caveat"]
    # And it must say which direction the bias runs.
    assert "flattered" in meta["caveat"]


def test_universe_describe_reports_the_drawdowns_present():
    from src.evolution import universe

    idx = pd.bdate_range("2005-01-01", periods=600)
    # A panel that halves and recovers.
    path = np.concatenate([np.linspace(100, 50, 300), np.linspace(50, 120, 300)])
    panel = pd.DataFrame({"A": path, "B": path * 1.01}, index=idx)

    meta = universe.describe("nse", panel)
    assert meta["max_drawdown_pct"] < -45
    assert meta["days"] == 600 and meta["names"] == 2
    assert meta["years_below_20pct"], "a 50% fall must be reported"


def test_universe_drops_short_history_rather_than_inventing_prices(tmp_path,
                                                                   monkeypatch):
    """Forward-filling a stock backwards through years it had not listed
    invents a price series, and a strategy will happily trade the invention."""
    from src.evolution import universe

    idx = pd.bdate_range("2005-01-01", periods=500)
    full = pd.Series(np.linspace(100, 200, 500), index=idx)
    late = full.copy()
    late.iloc[:400] = np.nan            # only listed for the last 100 days

    monkeypatch.setattr(universe, "CACHE", tmp_path)
    pd.DataFrame({"OLD": full, "NEW": late}).to_pickle(tmp_path / "prices_long.pkl")

    panel = universe.load("nse")
    assert "OLD" in panel.columns
    assert "NEW" not in panel.columns, "a late lister must be dropped, not padded"


def test_unknown_universe_is_refused():
    from src.evolution import universe

    with pytest.raises(ValueError):
        universe.load("moon")


def test_missing_panel_says_how_to_fetch_it(tmp_path, monkeypatch):
    from src.evolution import universe

    monkeypatch.setattr(universe, "CACHE", tmp_path)
    with pytest.raises(FileNotFoundError) as exc:
        universe.load("sp500")
    assert "fetch" in str(exc.value)


# ══════════════════════════════════════ the cash-parking loophole

def test_sharpe_is_measured_on_excess_returns():
    """Raw-return Sharpe lets a book scale into cash and keep its ratio."""
    from src.evolution.backtest import RISK_FREE_ANNUAL, sharpe_of

    idx = pd.bdate_range("2020-01-01", periods=500)
    rets = pd.Series(np.random.default_rng(0).normal(0.0004, 0.01, 500), index=idx)

    charged = sharpe_of(rets)
    uncharged = sharpe_of(rets, risk_free=0.0)
    assert charged < uncharged, "the risk-free rate must actually be subtracted"
    assert RISK_FREE_ANNUAL > 0


def test_a_book_earning_less_than_cash_scores_negative():
    """1.39% a year while 86% in cash is not a strategy — it is cash with
    extra steps, and it must not outrank a real one."""
    from src.evolution.backtest import sharpe_of

    idx = pd.bdate_range("2020-01-01", periods=800)
    # ~1.4% annual return, very low volatility: the shape the search found.
    near_cash = pd.Series(0.014 / 252 + np.random.default_rng(1)
                          .normal(0, 0.0009, 800), index=idx)
    assert sharpe_of(near_cash) < 0, "below the risk-free rate must be negative"


def test_scaling_a_strategy_down_no_longer_preserves_its_score():
    """Sharpe on raw returns is scale-invariant, which is what made parking in
    cash free. On excess returns, shrinking the book shrinks the excess."""
    from src.evolution.backtest import sharpe_of

    idx = pd.bdate_range("2020-01-01", periods=600)
    full = pd.Series(np.random.default_rng(2).normal(0.0005, 0.012, 600), index=idx)
    tenth = full * 0.1

    assert sharpe_of(full) > sharpe_of(tenth), (
        "a tenth-sized book must not score like a full one"
    )
    # And with no rate charged the old, exploitable invariance is visible.
    assert abs(sharpe_of(full, risk_free=0.0)
               - sharpe_of(tenth, risk_free=0.0)) < 1e-9
