"""
tests/test_weightfit.py
=======================
PHASE 3 — the out-of-sample harness for the ensemble's family weights.

It cannot run yet: there are not enough resolved calls, and there will not be
for months. That is precisely why it is tested now — the first real fit will
happen unattended, and the guarantees that matter (refuses below the sample
floor, never scores in-sample, never applies itself) have to hold on that day
without anyone re-reading the code.

The tests seed synthetic resolved calls to exercise the machinery. A synthetic
result says nothing about whether the weights are good; it says the harness
does what it claims when the data arrives.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.v5 import weightfit                     # noqa: E402
from src.v5.ensemble import FAMILY_WEIGHTS       # noqa: E402


def make_calls(n, *, informative_family="price", start=None, noise=0.0):
    """Resolved calls where one family actually predicts the outcome.

    Every call has scores from several families; only `informative_family`
    carries signal. A working fit should find it — but the assertions below are
    about protocol, not about whether it does.
    """
    import random
    rng = random.Random(7)
    start = start or datetime(2025, 1, 1)
    out = []
    for i in range(n):
        correct = rng.random() > 0.4
        signal = 60 if correct else -60
        if rng.random() < noise:
            signal = -signal
        mods = [{"module": f"{informative_family}_a",
                 "family": informative_family, "net": signal, "abstained": False}]
        for fam in ("macro", "behavioural", "volatility", "quant",
                    "fundamental", "machine"):
            mods.append({"module": f"{fam}_a", "family": fam,
                         "net": rng.uniform(-40, 40), "abstained": False})
        out.append({
            "id": f"p{i}", "resolved": True, "correct": correct,
            "at": (start + timedelta(days=i)).isoformat(timespec="seconds"),
            "ticker": "AAPL", "direction": "bull", "modules": mods,
        })
    return out


@pytest.fixture
def seeded(monkeypatch):
    def seed(entries):
        from src.v5 import learning
        monkeypatch.setattr(learning, "load_predictions",
                            lambda limit=None: entries)
    return seed


# ── the refusal, which is the current behaviour ──────────────────────────────

def test_refuses_to_fit_on_an_empty_record(seeded):
    seeded([])
    out = weightfit.propose()
    assert out["fitted"] is False
    assert out["recommendation"] == "keep the current weights"
    assert out["current_weights"] == FAMILY_WEIGHTS
    assert out["readiness"]["resolved_calls"] == 0
    assert out["readiness"]["shortfall"] == weightfit.MIN_RESOLVED


def test_refuses_just_below_the_threshold_and_says_by_how_much(seeded):
    seeded(make_calls(weightfit.MIN_RESOLVED - 1))
    state = weightfit.readiness()
    assert state["ready"] is False
    assert state["shortfall"] == 1
    assert str(weightfit.MIN_RESOLVED) in state["reason"]


def test_unresolved_predictions_are_not_evidence(seeded):
    calls = make_calls(300)
    for c in calls:
        c["resolved"] = False
    seeded(calls)
    assert weightfit.readiness()["resolved_calls"] == 0


def test_predictions_without_module_detail_are_not_evidence(seeded):
    """They cannot be re-scored under different weights, so counting them would
    overstate the sample the fit actually has."""
    calls = make_calls(300)
    for c in calls:
        c["modules"] = []
    seeded(calls)
    assert weightfit.readiness()["resolved_calls"] == 0


def test_a_thin_family_blocks_the_fit(seeded):
    """Fitting a weight for a family with three observations is not a fit."""
    calls = make_calls(300)
    for c in calls:
        c["modules"] = [m for m in c["modules"] if m["family"] != "macro"]
    seeded(calls)
    state = weightfit.readiness()
    assert state["ready"] is False
    assert "macro" in state["families_below_minimum"]


# ── the protocol, once there IS data ─────────────────────────────────────────

def test_it_runs_once_the_sample_arrives(seeded):
    seeded(make_calls(weightfit.MIN_RESOLVED + weightfit.BLOCK_SIZE * 4))
    out = weightfit.propose()
    assert out["fitted"] is True
    assert len(out["oos_blocks"]) >= weightfit.MIN_OOS_BLOCKS
    assert set(out["proposed_weights"]) == set(FAMILY_WEIGHTS)
    assert abs(sum(out["proposed_weights"].values()) - 1.0) < 0.01


def test_every_score_is_out_of_sample(seeded):
    """The whole point. A block's test window must start after its training
    window ends — no overlap, no shuffling, no fitting on the future."""
    seeded(make_calls(weightfit.MIN_RESOLVED + weightfit.BLOCK_SIZE * 4))
    out = weightfit.propose()
    calls = weightfit.resolved_calls()
    for block in out["oos_blocks"]:
        train_end = calls[block["train_n"] - 1]["at"][:10]
        assert block["test_from"] >= train_end, (
            f"test window starts at {block['test_from']}, inside training data "
            f"that runs to {train_end}")


def test_calls_are_ordered_by_time_never_shuffled(seeded):
    scrambled = make_calls(300)
    scrambled.reverse()
    seeded(scrambled)
    ordered = weightfit.resolved_calls()
    assert ordered == sorted(ordered, key=lambda c: c["at"])


def test_the_proposal_is_measured_against_the_current_weights(seeded):
    """"Better than nothing" is not the bar; "better than what is running" is."""
    seeded(make_calls(weightfit.MIN_RESOLVED + weightfit.BLOCK_SIZE * 4))
    out = weightfit.propose()
    assert "oos_brier_current" in out and "oos_brier_proposed" in out
    for block in out["oos_blocks"]:
        assert block["brier_prior"] is not None
        assert block["brier_fitted"] is not None
    assert out["beats_current"] in (True, False)
    if not out["beats_current"]:
        assert out["recommendation"] == "keep the current weights"


def test_a_single_lucky_block_cannot_carry_the_verdict(seeded):
    """Adoption needs a majority of blocks, not one spectacular one."""
    seeded(make_calls(weightfit.MIN_RESOLVED + weightfit.BLOCK_SIZE * 4))
    out = weightfit.propose()
    wins, total = out["blocks_won"].split("/")
    if out["beats_current"]:
        assert int(wins) > int(total) / 2


def test_fitted_weights_are_shrunk_towards_the_prior(seeded):
    """An unconstrained fit on a few hundred points will happily delete a whole
    family. Shrinkage is what stops one quarter from doing that."""
    calls = make_calls(weightfit.MIN_RESOLVED + weightfit.BLOCK_SIZE * 4)
    seeded(calls)
    fitted = weightfit._fit(weightfit.resolved_calls(), dict(FAMILY_WEIGHTS))
    for fam, prior in FAMILY_WEIGHTS.items():
        assert fitted[fam] > 0, f"{fam} was zeroed out"
        assert abs(fitted[fam] - prior) <= max(0.5, prior * 4)


def test_nothing_is_ever_applied(seeded):
    """The harness proposes. A human decides. The live weights are untouched
    by any call into this module."""
    before = dict(FAMILY_WEIGHTS)
    seeded(make_calls(weightfit.MIN_RESOLVED + weightfit.BLOCK_SIZE * 4))
    out = weightfit.propose()
    from src.v5.ensemble import FAMILY_WEIGHTS as after
    assert after == before
    assert out["applies_anything"] is False
    import inspect
    src = inspect.getsource(weightfit)
    assert "FAMILY_WEIGHTS[" not in src        # no item assignment anywhere
    assert "save_weights" not in src


def test_brier_rewards_being_right_confidently():
    """Sanity on the metric everything else is scored with."""
    call_right = {"direction": "bull", "correct": True,
                  "modules": [{"family": "price", "net": 90, "module": "p"}]}
    call_wrong = {"direction": "bull", "correct": False,
                  "modules": [{"family": "price", "net": 90, "module": "p"}]}
    w = dict(FAMILY_WEIGHTS)
    assert weightfit.brier([call_right], w) < weightfit.brier([call_wrong], w)
    assert weightfit.brier([], w) is None
