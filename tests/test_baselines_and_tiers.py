"""
Baseline comparison, module tiering and the narrative-provenance floor.

These tests matter more than usual because the real prediction log is empty:
until ARIA has run to resolution, synthetic outcomes are the only evidence that
the machinery reports the right thing. So each test constructs a prediction
history with a KNOWN answer — a system that is deliberately better than the
baseline, deliberately worse, deliberately identical — and checks the verdict
comes back right, including the verdicts nobody wants to see.

All offline. `_closes_before` is stubbed everywhere, so no test here touches a
price vendor.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.v5 import baselines, tiers
from src.v5.contract import Evidence, ModuleReport


# ── fixtures ────────────────────────────────────────────────────────────────

def _prediction(i, direction, ret, confidence=0.6, modules=None, resolved=True):
    at = datetime(2026, 1, 5) + timedelta(days=i)
    correct = (ret > 0) if direction == "bull" else (ret < 0)
    return {
        "id": f"p{i}", "at": at.isoformat(timespec="seconds"),
        "ticker": f"T{i % 4}", "direction": direction,
        "p_bull": 0.6, "confidence": confidence, "horizon_days": 21,
        "price_at": 100.0, "resolved": resolved,
        "realised_return": ret, "correct": correct,
        "modules": modules or [],
    }


def _patch_predictions(monkeypatch, entries):
    monkeypatch.setattr("src.v5.learning.load_predictions",
                        lambda limit=None: entries, raising=True)


def _patch_baseline_views(monkeypatch, view):
    """Force every price-based baseline to return `view`."""
    monkeypatch.setattr(baselines, "_sma_cross", lambda t, a: view)
    monkeypatch.setattr(baselines, "_momentum_60d", lambda t, a: view)
    monkeypatch.setattr(baselines, "BASELINES", {
        "sma_20_50": (baselines._sma_cross, "stub"),
        "momentum_60d": (baselines._momentum_60d, "stub"),
        "buy_and_hold": (baselines._always_bull, "stub"),
    })


# ── the refusal to report ───────────────────────────────────────────────────

def test_baselines_refuse_below_minimum_sample(monkeypatch):
    _patch_predictions(monkeypatch, [_prediction(i, "bull", 0.05) for i in range(5)])
    out = baselines.compare()
    assert out["measurable"] is False
    assert out["n_resolved"] == 5
    assert out["baselines"] == []


def test_unresolved_and_neutral_calls_are_never_graded(monkeypatch):
    entries = [_prediction(i, "bull", 0.05, resolved=False) for i in range(30)]
    entries += [_prediction(100 + i, "neutral", 0.05) for i in range(30)]
    _patch_predictions(monkeypatch, entries)
    assert baselines.compare()["measurable"] is False


# ── the comparison itself ───────────────────────────────────────────────────

def test_system_that_beats_the_baseline_is_reported_as_ahead(monkeypatch):
    # ARIA is right every time; the baseline is bearish into rising prices.
    _patch_predictions(monkeypatch, [_prediction(i, "bull", 0.05) for i in range(40)])
    _patch_baseline_views(monkeypatch, "bear")

    out = baselines.compare()
    assert out["measurable"] is True
    assert out["aria"]["hit_rate"] == 1.0

    sma = next(b for b in out["baselines"] if b["baseline"] == "sma_20_50")
    assert sma["verdict"] == "ahead"
    assert sma["n_discordant"] == 40
    assert sma["p_value"] < 0.05


def test_system_that_loses_to_the_baseline_says_so(monkeypatch):
    # ARIA is bearish into rising prices — wrong every time. The baseline is right.
    _patch_predictions(monkeypatch, [_prediction(i, "bear", 0.05) for i in range(40)])
    _patch_baseline_views(monkeypatch, "bull")

    sma = next(b for b in baselines.compare()["baselines"] if b["baseline"] == "sma_20_50")
    assert sma["verdict"] == "behind"
    assert "WORSE" in sma["note"]


def test_agreeing_with_the_baseline_is_undetermined_not_a_win(monkeypatch):
    """The trap this whole module exists to avoid.

    ARIA is right 100% of the time — but so is the baseline, because they made
    identical calls. A naive accuracy comparison would show 100% vs 100% and a
    reader might conclude the 41 modules were doing something. With no
    disagreements there is no evidence either way, and the verdict must say so.
    """
    _patch_predictions(monkeypatch, [_prediction(i, "bull", 0.05) for i in range(40)])
    _patch_baseline_views(monkeypatch, "bull")

    out = baselines.compare()
    assert out["aria"]["hit_rate"] == 1.0
    sma = next(b for b in out["baselines"] if b["baseline"] == "sma_20_50")
    assert sma["verdict"] == "undetermined"
    assert sma["n_discordant"] == 0
    assert out["beats_all_baselines"] is False


def test_headline_reports_the_weakest_baseline_not_the_best(monkeypatch):
    """Beating one baseline out of three is not 'ARIA has edge'."""
    entries = [_prediction(i, "bull", 0.05) for i in range(40)]
    _patch_predictions(monkeypatch, entries)
    # buy_and_hold always agrees (undetermined); sma disagrees and loses.
    monkeypatch.setattr(baselines, "BASELINES", {
        "buy_and_hold": (baselines._always_bull, "stub"),
        "sma_20_50": (lambda t, a: "bear", "stub"),
    })
    out = baselines.compare()
    assert out["beats_all_baselines"] is False
    assert "No measurable edge over" in out["headline"]


def test_baseline_abstentions_are_counted_not_silently_dropped(monkeypatch):
    _patch_predictions(monkeypatch, [_prediction(i, "bull", 0.05) for i in range(40)])
    monkeypatch.setattr(baselines, "BASELINES", {
        # Abstains on half the calls, leaving 20 paired.
        "sma_20_50": (lambda t, a: "bear" if int(t[1:]) % 2 == 0 else None, "stub"),
    })
    sma = baselines.compare()["baselines"][0]
    assert sma["n_paired"] + sma["n_abstained"] == 40
    assert sma["n_abstained"] > 0


def test_a_broken_baseline_cannot_take_the_page_down(monkeypatch):
    _patch_predictions(monkeypatch, [_prediction(i, "bull", 0.05) for i in range(40)])

    def explode(ticker, as_of):
        raise RuntimeError("vendor down")

    monkeypatch.setattr(baselines, "BASELINES", {"sma_20_50": (explode, "stub")})
    out = baselines.compare()
    assert out["measurable"] is True
    assert out["baselines"][0]["measurable"] is False


# ── probabilistic scoring ───────────────────────────────────────────────────

def test_confidence_that_adds_nothing_over_the_base_rate_is_called_out(monkeypatch):
    """Every call at 60% confidence, 60% of them right: perfectly calibrated,
    and carrying no information about WHICH calls are the good ones."""
    entries = ([_prediction(i, "bull", 0.05, confidence=0.6) for i in range(24)]
               + [_prediction(50 + i, "bull", -0.05, confidence=0.6) for i in range(16)])
    _patch_predictions(monkeypatch, entries)

    out = baselines.brier_comparison()
    assert out["measurable"] is True
    assert out["skill_vs_coin_flip"] > 0          # beats a coin — the easy bar
    assert out["skill_vs_base_rate"] <= 0         # adds nothing — the real bar
    assert "no information beyond the base rate" in out["verdict"]


def test_confidence_that_sorts_good_calls_from_bad_scores_positive_skill(monkeypatch):
    # High-confidence calls are right, low-confidence ones are coin flips.
    entries = [_prediction(i, "bull", 0.05, confidence=0.9) for i in range(20)]
    entries += [_prediction(50 + i, "bull", 0.05 if i % 2 else -0.05, confidence=0.5)
                for i in range(20)]
    _patch_predictions(monkeypatch, entries)

    out = baselines.brier_comparison()
    assert out["skill_vs_base_rate"] > 0
    assert "carry information beyond the base rate" in out["verdict"]


# ── tiering ─────────────────────────────────────────────────────────────────

def _scorecard(monkeypatch, card):
    monkeypatch.setattr("src.v5.learning.module_scorecard", lambda: card, raising=True)


def test_every_module_starts_experimental(monkeypatch):
    _scorecard(monkeypatch, {})
    out = tiers.classify()
    assert len(out) == 41
    assert all(r["tier"] == "experimental" for r in out.values())
    assert tiers.summary()["counts"]["experimental"] == 41


def test_a_module_needs_the_minimum_sample_before_it_can_be_promoted(monkeypatch):
    _scorecard(monkeypatch, {
        "momentum": {"right": 20, "wrong": 0, "n": 20, "hit_rate": 1.0, "p_value": 0.0001,
                     "family": "price"}})
    # 20 resolved, 25 required — a perfect record on too few calls is still unproven.
    assert tiers.classify()["momentum"]["tier"] == "experimental"


def test_a_significant_module_is_promoted_to_core(monkeypatch):
    _scorecard(monkeypatch, {
        "momentum": {"right": 40, "wrong": 10, "n": 50, "hit_rate": 0.8, "p_value": 0.0001,
                     "family": "price"}})
    rec = tiers.classify()["momentum"]
    assert rec["tier"] == "core"
    assert rec["core_after_correction"] is True


def test_a_measurable_but_unremarkable_module_is_provisional(monkeypatch):
    _scorecard(monkeypatch, {
        "momentum": {"right": 26, "wrong": 24, "n": 50, "hit_rate": 0.52, "p_value": 0.89,
                     "family": "price"}})
    assert tiers.classify()["momentum"]["tier"] == "provisional"


def test_a_reliably_wrong_module_is_benched_and_loses_its_vote(monkeypatch):
    _scorecard(monkeypatch, {
        "momentum": {"right": 10, "wrong": 40, "n": 50, "hit_rate": 0.2, "p_value": 0.0001,
                     "family": "price"}})
    assert tiers.classify()["momentum"]["tier"] == "benched"
    assert "momentum" not in tiers.live_eligible()


def test_multiple_comparisons_correction_demotes_a_lone_lucky_module(monkeypatch):
    """One module clearing p<0.05 out of 41 tested is the expected yield of pure
    noise. It may show as `core`, but not as core-after-correction."""
    card = {"momentum": {"right": 33, "wrong": 17, "n": 50, "hit_rate": 0.66,
                         "p_value": 0.03, "family": "price"}}
    # 40 other modules measured and unremarkable.
    for name in tiers.classify():
        if name != "momentum":
            card[name] = {"right": 25, "wrong": 25, "n": 50, "hit_rate": 0.5,
                          "p_value": 0.9, "family": "price"}
    _scorecard(monkeypatch, card)

    rec = tiers.classify()["momentum"]
    assert rec["tier"] == "core"
    assert rec["core_after_correction"] is False
    assert rec["p_value_bh_adjusted"] > 0.05

    s = tiers.summary()
    assert s["counts"]["core"] == 1
    assert s["n_core_after_correction"] == 0
    assert "none survives correction" in s["headline"]


# ── provenance ──────────────────────────────────────────────────────────────

def test_all_shipped_modules_declare_a_valid_provenance():
    from src.v5.registry import PROVENANCE, REGISTRY, load_modules
    load_modules()
    assert REGISTRY
    assert all(s.provenance in PROVENANCE for s in REGISTRY.values())


def test_an_unknown_provenance_is_refused_at_registration():
    from src.v5.registry import module
    with pytest.raises(ValueError, match="unknown provenance"):
        module("bogus", "price", "d", provenance="vibes")(lambda t: None)


def test_a_narrative_module_cannot_report_a_narrow_interval():
    """The floor that stops LLM fluency being laundered into statistical
    confidence. A language model has no observation count, so a tight interval
    is not something it is entitled to."""
    from src.v5.registry import NARRATIVE_MIN_CI_WIDTH, REGISTRY, module, run

    @module("narrative_probe", "machine", "test", provenance="narrative")
    def _probe(ticker):
        return ModuleReport.from_probability(
            "narrative_probe", "machine", ticker, 0.95, (0.94, 0.96),
            thesis="the model was very sure",
            evidence=[Evidence("a claim", 1.0, "test source", "bull")],
            weaknesses=["a stated weakness"])

    try:
        r = run("narrative_probe", "TEST")
        assert r.provenance == "narrative"
        assert r.ci_width >= NARRATIVE_MIN_CI_WIDTH - 1e-9
        assert r.bull <= 65.0            # cannot outvote a measured module
        assert abs(r.bull + r.bear + r.neutral - 100) < 0.5
        assert r.view == "bull"          # still allowed to lean
    finally:
        REGISTRY.pop("narrative_probe", None)


def test_a_module_cannot_forge_its_own_provenance():
    from src.v5.registry import REGISTRY, module, run

    @module("liar", "price", "test", provenance="narrative")
    def _liar(ticker):
        r = ModuleReport.from_probability(
            "liar", "price", ticker, 0.9, (0.88, 0.92),
            thesis="t", evidence=[Evidence("c", 1, "s", "bull")], weaknesses=["w"])
        r.provenance = "statistical"      # the forgery
        return r

    try:
        assert run("liar", "TEST").provenance == "narrative"
    finally:
        REGISTRY.pop("liar", None)


def test_widening_leaves_an_already_wide_interval_alone():
    r = ModuleReport.from_probability(
        "m", "price", "T", 0.6, (0.3, 0.9),
        thesis="t", evidence=[Evidence("c", 1, "s", "bull")], weaknesses=["w"])
    assert r.widened_to(0.35) is r


def test_widening_a_boundary_interval_stays_inside_the_unit_range():
    r = ModuleReport.from_probability(
        "m", "price", "T", 0.99, (0.98, 1.0),
        thesis="t", evidence=[Evidence("c", 1, "s", "bull")], weaknesses=["w"])
    w = r.widened_to(0.35)
    assert 0.0 <= w.ci_low < w.ci_high <= 1.0
    assert w.ci_width >= 0.35 - 1e-9


def test_widening_never_touches_an_abstention():
    from src.v5.contract import insufficient
    r = insufficient("m", "price", "T", "no data")
    assert r.widened_to(0.35) is r
