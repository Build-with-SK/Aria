"""
tests/test_training_promotion.py
================================
The promotion gate (docs/ARIA_NEXT_SESSION.md step 6).

"Adopting a worse model because it is newer is the failure mode; guard against
it." Every test here is a version of that sentence.

The one that matters most is `test_two_identical_models_do_not_promote`: if
the gate promoted on "the new number is smaller", it would adopt a new adapter
about half the time when the two models are the same model. That is not a
hypothetical — it is what any threshold-free comparison does on forty rows.
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.training import adapters as A
from src.training import evaluate as E


def holdout(n: int, seed: int = 1) -> list[dict]:
    rng = random.Random(seed)
    return [{"meta": {"id": f"h{i}", "at": f"2026-06-{(i % 28) + 1:02d}",
                      "realised_return": rng.choice([0.02, -0.015])}}
            for i in range(n)]


def predictions(rows, *, skill: float, seed: int = 7) -> list[float]:
    """P(up) with a given amount of edge. skill=0 is a coin flip; skill=1 is
    perfect foresight."""
    rng = random.Random(seed)
    out = []
    for r in rows:
        truth = 1 if r["meta"]["realised_return"] > 0 else 0
        p = 0.5 + skill * (0.45 if truth else -0.45)
        out.append(min(max(p + rng.uniform(-0.03, 0.03), 0.01), 0.99))
    return out


@pytest.fixture
def registry(tmp_path):
    return tmp_path / "registry.json"


def register(registry, version="v1", **kw):
    A.register(A.Adapter(version=version, path=f"/adapters/{version}", **kw),
               path=registry)


# ── reading a probability out of a reply ────────────────────────────────────

def test_a_direction_with_a_percentage_parses():
    assert E.parse_probability("UP. 70% confident.") == pytest.approx(0.70)
    assert E.parse_probability("DOWN, 80% confident") == pytest.approx(0.20)


def test_a_direction_with_no_number_gets_a_default_not_a_guess():
    p = E.parse_probability("UP over the horizon.")
    assert 0.5 < p < 1.0


def test_an_abstention_is_not_a_coin_flip():
    """Scoring an abstention as 0.5 hands the model the Brier score of a hedge
    it never made — and flatters exactly the model that learned to say
    nothing."""
    assert E.parse_probability("ABSTAIN — the readings do not support a call") is None
    assert E.parse_probability("NO_TRADE") is None


def test_an_unparseable_reply_is_none_not_a_number():
    assert E.parse_probability("Interesting question! Markets are complex.") is None
    assert E.parse_probability("It could go UP or DOWN from here.") is None
    assert E.parse_probability("") is None


def test_a_zero_return_is_dropped_rather_than_assigned_a_side():
    assert E.outcome_of({"realised_return": 0}) is None
    assert E.outcome_of({"realised_return": 0.01}) == 1
    assert E.outcome_of({"realised_return": -0.01}) == 0


# ── scoring ─────────────────────────────────────────────────────────────────

def test_a_perfect_forecaster_scores_near_zero_brier():
    rows = holdout(60)
    s = E.score(predictions(rows, skill=1.0), rows)
    assert s.brier < 0.02 and s.accuracy == 1.0 and s.n == 60


def test_a_coin_flip_scores_near_a_quarter():
    rows = holdout(60)
    s = E.score(predictions(rows, skill=0.0), rows)
    assert 0.2 < s.brier < 0.3


def test_abstentions_are_counted_not_scored():
    rows = holdout(10)
    probs = [None] * 4 + predictions(rows, skill=1.0)[4:]
    s = E.score(probs, rows)
    assert s.abstained == 4 and s.n == 6


def test_mismatched_lengths_are_refused():
    with pytest.raises(ValueError):
        E.score([0.5, 0.5], holdout(3))


# ── the comparison ──────────────────────────────────────────────────────────

def test_two_identical_models_do_not_promote():
    """THE test. A gate that promotes on 'smaller number wins' adopts a new
    adapter about half the time when nothing has changed."""
    rows = holdout(120)
    same = predictions(rows, skill=0.3)
    c = E.compare(list(same), list(same), rows)
    assert c.verdict == "indistinguishable"
    assert not c.candidate_wins


def test_a_retrain_with_no_real_edge_does_not_promote():
    """The realistic version of the same test: a retrain that produced a
    slightly different model of the SAME quality. Different numbers, no better
    — and on any given sample one of them will look better by chance."""
    rows = holdout(120)
    incumbent = predictions(rows, skill=0.35, seed=11)
    candidate = predictions(rows, skill=0.35, seed=99)
    c = E.compare(candidate, incumbent, rows)
    assert c.verdict in ("indistinguishable", "worse")
    assert not c.candidate_wins


def test_the_noise_gate_holds_across_many_resamples():
    """Run the identical-quality comparison over many random holdouts. If the
    gate promoted on 'smaller number wins' this would pass roughly half the
    time; it must essentially never promote."""
    promotions = 0
    for seed in range(40):
        rows = holdout(80, seed=seed)
        c = E.compare(predictions(rows, skill=0.35, seed=seed * 2 + 1),
                      predictions(rows, skill=0.35, seed=seed * 2 + 2), rows)
        promotions += int(c.candidate_wins)
    assert promotions == 0, f"{promotions}/40 promotions on identical quality"


def test_a_genuinely_better_adapter_wins():
    rows = holdout(200)
    c = E.compare(predictions(rows, skill=0.9), predictions(rows, skill=0.1), rows)
    assert c.verdict == "better"
    assert c.improvement > E.MIN_MATERIAL_IMPROVEMENT
    assert c.ci_low > 0


def test_a_worse_adapter_is_named_as_worse():
    rows = holdout(200)
    c = E.compare(predictions(rows, skill=0.1), predictions(rows, skill=0.9), rows)
    assert c.verdict == "worse"
    assert "WORSE" in c.describe()


def test_a_tiny_but_real_improvement_is_not_worth_a_swap():
    """Significance is a bar anyone clears by collecting more rows. The
    improvement also has to be big enough to be worth changing what she runs
    on."""
    rows = holdout(400)
    incumbent = predictions(rows, skill=0.50, seed=3)
    candidate = [min(max(p + (0.002 if p > 0.5 else -0.002), 0.01), 0.99)
                 for p in incumbent]
    c = E.compare(candidate, incumbent, rows)
    assert c.verdict != "better"
    assert any("worth swapping" in r for r in c.reasons)


def test_too_few_rows_is_insufficient_not_a_pass():
    rows = holdout(10)
    c = E.compare(predictions(rows, skill=0.9), predictions(rows, skill=0.1), rows)
    assert c.verdict == "insufficient"
    assert not c.candidate_wins


def test_scoring_is_paired_so_abstaining_wins_nothing():
    """If the candidate abstains on the hard names and is scored on what is
    left, it wins by choosing its own exam."""
    rows = holdout(120)
    incumbent = predictions(rows, skill=0.4)
    candidate = [None if i % 2 else incumbent[i] for i in range(len(rows))]
    c = E.compare(candidate, incumbent, rows)
    assert c.n_paired == 60                   # only the rows both committed to
    assert c.verdict == "indistinguishable"
    assert any("abstained on" in r for r in c.reasons)


def test_the_bootstrap_is_reproducible():
    rows = holdout(100)
    a, b = predictions(rows, skill=0.7), predictions(rows, skill=0.3)
    assert E.compare(a, b, rows).to_dict() == E.compare(a, b, rows).to_dict()


# ── the registry and the gate ───────────────────────────────────────────────

def test_nothing_serving_means_the_base_model_and_says_so(registry):
    s = A.status(path=registry)
    assert s["serving"] is None and s["serving_is_base_model"] is True
    assert "honest state" in s["note"]


def test_registering_is_not_promoting(registry):
    register(registry, "v1")
    assert A.serving(path=registry) is None
    assert "v1" in A.status(path=registry)["registered"]


def test_versions_are_immutable(registry):
    register(registry, "v1")
    with pytest.raises(ValueError, match="already registered"):
        register(registry, "v1")


def test_an_unregistered_adapter_cannot_be_decided_on(registry):
    rows = holdout(200)
    c = E.compare(predictions(rows, skill=0.9), predictions(rows, skill=0.1), rows)
    d = A.decide("never-registered", c, path=registry)
    assert d.promote is False
    assert any("not registered" in r for r in d.reasons)


def test_a_first_adapter_still_has_to_beat_the_base_model(registry):
    """With no incumbent the baseline is the model it was meant to improve —
    'nothing is serving' is not a free pass."""
    register(registry, "v1")
    rows = holdout(200)
    tie = predictions(rows, skill=0.4)
    d = A.decide("v1", E.compare(list(tie), list(tie), rows), path=registry)
    assert d.promote is False
    assert any("base model" in r for r in d.reasons)


def test_a_tie_goes_to_the_incumbent(registry):
    register(registry, "v1")
    rows = holdout(200)
    tie = predictions(rows, skill=0.5)
    d = A.decide("v1", E.compare(list(tie), list(tie), rows), path=registry)
    assert d.promote is False
    assert any("tie goes to the incumbent" in r for r in d.reasons)


def test_promoting_a_held_decision_is_refused(registry):
    register(registry, "v1")
    held = A.Decision(promote=False, version="v1", against="base",
                      reasons=["indistinguishable"])
    with pytest.raises(PermissionError, match="the gate held"):
        A.promote(held, approved_by="Soundariyan", path=registry)


def test_a_promotion_names_the_person(registry):
    register(registry, "v1")
    approved = A.Decision(promote=True, version="v1", against="base")
    with pytest.raises(PermissionError, match="approved by a person"):
        A.promote(approved, approved_by="", path=registry)


def test_a_won_decision_promotes_and_keeps_the_previous(registry):
    register(registry, "v1")
    register(registry, "v2")
    A.promote(A.Decision(promote=True, version="v1", against="base"),
              approved_by="Soundariyan", path=registry)
    A.promote(A.Decision(promote=True, version="v2", against="v1"),
              approved_by="Soundariyan", path=registry)

    assert A.serving(path=registry).version == "v2"
    assert A.previous(path=registry).version == "v1"
    assert len(A.history(path=registry)) == 2
    assert A.history(path=registry)[-1]["approved_by"] == "Soundariyan"


def test_rollback_puts_the_previous_one_back(registry):
    register(registry, "v1")
    register(registry, "v2")
    A.promote(A.Decision(promote=True, version="v1", against="base"),
              approved_by="S", path=registry)
    A.promote(A.Decision(promote=True, version="v2", against="v1"),
              approved_by="S", path=registry)

    A.rollback(approved_by="S", reason="prose got worse in practice", path=registry)
    assert A.serving(path=registry).version == "v1"
    assert A.previous(path=registry).version == "v2"   # forward again if needed


def test_rollback_with_nothing_behind_it_is_refused(registry):
    register(registry, "v1")
    with pytest.raises(ValueError, match="no previous adapter"):
        A.rollback(approved_by="S", path=registry)


def test_held_candidates_are_recorded(registry):
    """The held ones are the evidence the gate does something."""
    register(registry, "v1")
    A.record_hold(A.Decision(promote=False, version="v1", against="base",
                             reasons=["indistinguishable"]), path=registry)
    entries = A.history(path=registry)
    assert entries and entries[-1]["action"] == "hold"
    assert A.serving(path=registry) is None


def test_a_corrupt_registry_does_not_read_as_a_clean_install(registry, tmp_path):
    """It would look exactly like a fresh machine, and invite a promotion on
    top of whatever was really there."""
    registry.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unreadable"):
        A.load(registry)


def test_the_decision_reads_as_a_sentence(registry):
    register(registry, "v1")
    rows = holdout(200)
    c = E.compare(predictions(rows, skill=0.9), predictions(rows, skill=0.1), rows)
    text = A.decide("v1", c, path=registry).describe()
    assert text.startswith("PROMOTE v1 over")
