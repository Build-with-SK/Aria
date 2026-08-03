"""
ARIA V5 — contract, isolation, ensemble mechanics, meta-reasoning, risk gate and
the learning loop.

These tests are all offline: no module here touches the network. The research
modules themselves are exercised only through fakes, because their job is to
abstain when data is missing and that is exactly what an offline test proves.
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.v5.contract import (Evidence, ModuleReport, bootstrap_interval, insufficient,
                             validate, wilson_interval)


def _report(module="m", family="price", ticker="TEST", p=0.7, ci=(0.6, 0.8), **kw):
    return ModuleReport.from_probability(
        module, family, ticker, p, ci,
        thesis="test thesis",
        evidence=[Evidence("a claim", 1.0, "test source", "bull")],
        weaknesses=["a stated weakness"], **kw)


# ── contract ────────────────────────────────────────────────────────────────

def test_scores_sum_to_100_and_validate_clean():
    r = _report()
    assert abs(r.bull + r.bear + r.neutral - 100) < 0.01
    assert validate(r) == []
    assert r.view == "bull" and r.net > 0


def test_wider_interval_means_more_neutral_mass():
    tight = _report(p=0.8, ci=(0.75, 0.85))
    wide = _report(p=0.8, ci=(0.3, 0.95))
    assert wide.neutral > tight.neutral
    assert wide.bull < tight.bull, "a wide interval must not produce a confident score"


def test_extreme_probability_cannot_beat_a_wide_interval():
    certain_but_vague = _report(p=1.0, ci=(0.0, 1.0))
    assert certain_but_vague.neutral == pytest.approx(85.0), "neutral mass is capped at 85"
    assert certain_but_vague.bull <= 15.0


def test_insufficient_is_a_valid_abstention_not_an_error():
    r = insufficient("m", "quant", "TEST", "no data")
    assert r.insufficient_data and r.view == "abstain" and not r.votes
    assert r.neutral == 100 and r.ci_low is None
    assert validate(r) == [], "an abstention must not be a contract violation"


def test_validate_catches_a_scored_view_with_no_evidence():
    r = ModuleReport.from_probability("m", "price", "T", 0.7, (0.6, 0.8),
                                      thesis="t", evidence=[], weaknesses=["w"])
    assert any("no cited evidence" in p for p in validate(r))


def test_validate_catches_evidence_without_a_source():
    r = _report()
    r.evidence = [{"claim": "unsourced", "value": 1, "source": "", "lean": "bull"}]
    assert any("without a source" in p for p in validate(r))


def test_wilson_interval_widens_as_the_sample_shrinks():
    lo_big, hi_big = wilson_interval(60, 100)
    lo_small, hi_small = wilson_interval(6, 10)
    assert (hi_small - lo_small) > (hi_big - lo_big)
    assert 0 <= lo_small <= hi_small <= 1


def test_bootstrap_interval_needs_two_points():
    assert bootstrap_interval([0.6]) == (0.0, 1.0)
    lo, hi = bootstrap_interval([0.6, 0.62, 0.58, 0.61])
    assert lo < 0.61 < hi


# ── non-finite numbers must never reach the ensemble ────────────────────────

def test_clamp_propagates_nan_instead_of_returning_the_upper_bound():
    """max(lo, min(hi, nan)) silently returns hi in CPython. That turned a
    division by zero into a maximally bullish score once; never again."""
    from src.v5.modules._util import clamp
    out = clamp(float("nan"), 0.0, 1.0)
    assert out != out, "NaN must survive clamping so the contract can catch it"
    assert clamp(5.0, 0.0, 1.0) == 1.0 and clamp(-5.0, 0.0, 1.0) == 0.0


def test_a_nan_probability_becomes_an_abstention_not_a_score():
    r = ModuleReport.from_probability(
        "m", "price", "T", float("nan"), (0.4, 0.6),
        thesis="t", evidence=[Evidence("c", 1, "s")], weaknesses=["w"])
    assert r.insufficient_data and not r.votes
    assert "non-finite" in r.reason


def test_a_nan_interval_becomes_an_abstention():
    r = ModuleReport.from_probability(
        "m", "price", "T", 0.7, (float("nan"), 0.9),
        thesis="t", evidence=[Evidence("c", 1, "s")], weaknesses=["w"])
    assert r.insufficient_data


def test_infinity_is_rejected_too():
    r = ModuleReport.from_probability(
        "m", "price", "T", float("inf"), (0.4, 0.6),
        thesis="t", evidence=[Evidence("c", 1, "s")], weaknesses=["w"])
    assert r.insufficient_data


def test_validate_rejects_a_non_finite_score():
    r = _report()
    r.bull = float("nan")
    assert any("not a finite number" in p for p in validate(r))


def test_the_ensemble_never_sees_a_nan_through_the_registry():
    from src.v5 import registry

    @registry.module("_nan", "quant", "divides by zero")
    def _nan(ticker):
        return ModuleReport.from_probability(
            "_nan", "quant", ticker, 0.0 / 1.0 if False else float("nan"), (0.1, 0.9),
            thesis="t", evidence=[Evidence("c", 1, "s")], weaknesses=["w"])

    r = registry.run("_nan", "TEST")
    assert r.insufficient_data
    ens = _synth([r, _report("ok", "price", p=0.8, ci=(0.7, 0.9))])
    assert ens.net_score == ens.net_score, "a NaN module must not poison the aggregate"
    assert ens.confidence == ens.confidence
    registry.REGISTRY.pop("_nan", None)


# ── unknown instruments ─────────────────────────────────────────────────────

def test_an_unpriceable_symbol_is_a_clean_error_not_a_verdict(monkeypatch):
    """Market-level modules would happily report on a symbol that does not
    exist, producing a confident-looking 'no position'. It must not get that
    far."""
    from src.v5 import pipeline

    monkeypatch.setattr(pipeline.md, "closes", lambda *a, **k: None)
    called = []
    monkeypatch.setattr(pipeline.registry, "run_all",
                        lambda *a, **k: called.append(1) or [])

    out = pipeline.analyze("NOT_A_TICKER", log=False)
    assert out.get("unknown_instrument") is True
    assert "No price history" in out["error"]
    assert not called, "no module may run for an instrument with no price history"


def test_every_early_exit_has_the_same_shape():
    from src.v5 import pipeline
    out = pipeline.analyze("", log=False)
    for key in ("ticker", "as_of", "elapsed_ms", "error"):
        assert key in out


# ── registry isolation ──────────────────────────────────────────────────────

def test_a_raising_module_becomes_an_abstention_not_a_crash():
    from src.v5 import registry

    @registry.module("_boom", "quant", "always explodes")
    def _boom(ticker):
        raise RuntimeError("detonated")

    r = registry.run("_boom", "TEST")
    assert r.insufficient_data and "RuntimeError" in r.reason
    assert not r.votes
    registry.REGISTRY.pop("_boom", None)


def test_a_module_returning_junk_becomes_an_abstention():
    from src.v5 import registry

    @registry.module("_junk", "quant", "returns nonsense")
    def _junk(ticker):
        return {"bull": 100}

    r = registry.run("_junk", "TEST")
    assert r.insufficient_data and "not a ModuleReport" in r.reason
    registry.REGISTRY.pop("_junk", None)


def test_a_contract_violating_module_is_downgraded_not_trusted():
    from src.v5 import registry

    @registry.module("_liar", "quant", "claims confidence with no interval")
    def _liar(ticker):
        return ModuleReport(module="_liar", family="quant", ticker=ticker,
                            bull=90, bear=10, neutral=0, thesis="trust me")

    r = registry.run("_liar", "TEST")
    assert r.insufficient_data, "an invalid confident report must never reach the ensemble"
    registry.REGISTRY.pop("_liar", None)


def test_unknown_module_name_abstains():
    from src.v5 import registry
    assert registry.run("does_not_exist", "TEST").insufficient_data


def test_every_registered_module_declares_a_known_family():
    from src.v5 import registry
    registry.load_modules()
    assert len(registry.REGISTRY) >= 30
    for spec in registry.REGISTRY.values():
        assert spec.family in registry.FAMILIES


def test_reinforcement_learning_can_never_vote():
    from src.v5 import registry
    registry.load_modules()
    assert registry.REGISTRY["reinforcement_learning"].research_only is True
    names = [m["name"] for m in registry.list_modules()]
    assert "reinforcement_learning" in names
    # run_all excludes research-only engines from a live recommendation
    from src.v5.registry import REGISTRY
    live = [s for s in REGISTRY.values() if not s.research_only]
    assert all(s.name != "reinforcement_learning" for s in live)


# ── ensemble ────────────────────────────────────────────────────────────────

def _synth(reports, ticker="TEST"):
    from src.v5.ensemble import synthesise
    return synthesise(reports, ticker)


def test_agreement_beats_disagreement_on_confidence():
    agree = [_report(f"m{i}", "price", p=0.75, ci=(0.68, 0.82)) for i in range(6)]
    mixed = ([_report(f"m{i}", "price", p=0.75, ci=(0.68, 0.82)) for i in range(3)] +
             [_report(f"n{i}", "price", p=0.25, ci=(0.18, 0.32)) for i in range(3)])
    assert _synth(agree).confidence > _synth(mixed).confidence
    assert _synth(mixed).dispersion > _synth(agree).dispersion


def test_wide_intervals_reduce_confidence_mechanically():
    tight = [_report(f"m{i}", "price", p=0.75, ci=(0.70, 0.80)) for i in range(5)]
    wide = [_report(f"m{i}", "price", p=0.75, ci=(0.45, 0.99)) for i in range(5)]
    assert _synth(tight).confidence > _synth(wide).confidence


def test_abstentions_reduce_confidence_but_do_not_flip_direction():
    base = [_report(f"m{i}", "price", p=0.75, ci=(0.68, 0.82)) for i in range(5)]
    with_abstain = base + [insufficient(f"a{i}", "quant", "TEST", "no data") for i in range(5)]
    a, b = _synth(base), _synth(with_abstain)
    assert b.abstention_rate > 0
    assert b.confidence < a.confidence
    assert b.direction == a.direction == "bull"


def test_all_abstaining_produces_no_view():
    r = _synth([insufficient(f"a{i}", "quant", "TEST", "no data") for i in range(4)])
    assert r.direction == "neutral" and r.confidence == 0.5 and r.n_voting == 0


def test_confidence_is_a_probability_above_a_coin_flip():
    r = _synth([_report(f"m{i}", "price", p=0.9, ci=(0.85, 0.95)) for i in range(6)])
    assert 0.5 <= r.confidence <= 1.0
    assert r.confidence > 0.55, "strong unanimous evidence should clear the trading floor"


def test_bearish_ensemble_points_bearish():
    r = _synth([_report(f"m{i}", "price", p=0.15, ci=(0.08, 0.22)) for i in range(6)])
    assert r.direction == "bear" and r.net_score < 0 and r.p_bull < 0.5


def test_dissent_is_preserved_in_the_output():
    reports = ([_report(f"m{i}", "price", p=0.8, ci=(0.72, 0.88)) for i in range(4)] +
               [_report("contrarian", "quant", p=0.15, ci=(0.08, 0.22))])
    r = _synth(reports)
    assert r.direction == "bull"
    assert any(d["module"] == "contrarian" for d in r.dissent), "a losing argument is never deleted"


def test_a_thinly_covered_family_cannot_outvote_a_well_covered_one():
    """One quant engine reporting must not inherit the whole quant family weight
    and overturn four price engines."""
    four_price = [_report(f"p{i}", "price", p=0.80, ci=(0.72, 0.88)) for i in range(4)]
    lone_quant = _report("q1", "quant", p=0.15, ci=(0.08, 0.22))
    r = _synth(four_price + [lone_quant])
    assert r.direction == "bull"
    assert r.weights["q1"] < sum(r.weights[f"p{i}"] for i in range(4))


def test_a_family_that_all_abstained_dilutes_nothing():
    price_only = [_report(f"p{i}", "price", p=0.8, ci=(0.72, 0.88)) for i in range(3)]
    plus_dead_family = price_only + [insufficient("q1", "quant", "TEST", "no data")]
    a, b = _synth(price_only), _synth(plus_dead_family)
    # Weights are rounded to 4dp for display, hence the 1e-3 tolerance.
    assert sum(a.weights.values()) == pytest.approx(1.0, abs=1e-3)
    assert sum(b.weights.values()) == pytest.approx(1.0, abs=1e-3)
    assert a.weights == b.weights, "an abstaining family must change nothing"


# ── meta-reasoning ──────────────────────────────────────────────────────────

def test_meta_lowers_confidence_when_an_independent_path_disagrees():
    from src.v5.meta import review
    # Weighted toward price (heaviest family) but out-voted by count elsewhere,
    # so the unweighted paths reach the opposite conclusion.
    reports = ([_report("p1", "price", p=0.95, ci=(0.90, 0.99))] +
               [_report(f"b{i}", "behavioural", p=0.30, ci=(0.22, 0.38)) for i in range(3)] +
               [_report(f"m{i}", "macro", p=0.30, ci=(0.22, 0.38)) for i in range(3)])
    ens = _synth(reports)
    m = review("TEST", ens, reports)
    disagreeing = [p for p in m.alternative_paths if not p.agrees]
    if disagreeing:
        assert m.confidence_after < m.confidence_before
        assert m.edge_after < m.edge_before


def test_meta_leaves_a_corroborated_call_alone():
    from src.v5.meta import review
    reports = [_report(f"m{i}", "price", p=0.8, ci=(0.74, 0.86)) for i in range(6)]
    ens = _synth(reports)
    m = review("TEST", ens, reports)
    assert m.confidence_after == pytest.approx(m.confidence_before, abs=1e-6)
    assert all(p.agrees for p in m.alternative_paths)


def test_meta_confidence_never_crosses_below_a_coin_flip():
    from src.v5.meta import review
    reports = ([_report("p1", "price", p=0.99, ci=(0.95, 1.0))] +
               [_report(f"x{i}", "macro", p=0.01, ci=(0.0, 0.05)) for i in range(6)])
    ens = _synth(reports)
    m = review("TEST", ens, reports)
    assert m.confidence_after >= 0.5, "a disagreement must reduce edge, never assert the opposite"


def test_meta_always_produces_a_counterargument_and_falsification_tests():
    from src.v5.meta import review
    reports = [_report(f"m{i}", "price", p=0.8, ci=(0.74, 0.86)) for i in range(4)]
    m = review("TEST", _synth(reports), reports)
    assert m.counterargument and len(m.counterargument) > 60
    assert len(m.falsification_tests) >= 2


def test_meta_flags_a_figure_cited_to_opposite_ends():
    from src.v5.meta import review
    a = _report("a", "price", p=0.8, ci=(0.7, 0.9))
    b = _report("b", "quant", p=0.2, ci=(0.1, 0.3))
    a.evidence = [Evidence("vol is 30%", 0.30, "same_source", "bull")]
    b.evidence = [Evidence("vol is 30%", 0.30, "same_source", "bear")]
    m = review("TEST", _synth([a, b]), [a, b])
    assert any("cited both bullishly and bearishly" in c for c in m.contradictions)


# ── risk gate ───────────────────────────────────────────────────────────────

class _FakeMeta:
    def __init__(self, conf, edge):
        self.confidence_after, self.edge_after = conf, edge


def test_risk_vetoes_a_call_below_the_conviction_floor(monkeypatch):
    from src.v5 import risk_gate
    monkeypatch.setattr(risk_gate.md, "closes", lambda *a, **k: None)
    monkeypatch.setattr(risk_gate.md, "returns", lambda *a, **k: None)
    monkeypatch.setattr(risk_gate.md, "signal", lambda *a, **k: {})
    reports = [_report(f"m{i}", "price", p=0.52, ci=(0.4, 0.64)) for i in range(4)]
    ens = _synth(reports)
    v = risk_gate.assess("TEST", ens, reports, _FakeMeta(0.51, 0.02))
    assert v.verdict in ("VETO", "NO_TRADE") and not v.approved
    assert v.position_size_pct == 0.0


def test_risk_gate_never_returns_a_recommendation_without_an_invalidation():
    from src.v5 import risk_gate
    reports = [_report(f"m{i}", "price", p=0.8, ci=(0.74, 0.86)) for i in range(4)]
    ens = _synth(reports)
    v = risk_gate.assess("TEST", ens, reports, _FakeMeta(0.65, 0.30))
    assert v.invalidation and len(v.invalidation) > 10


def test_neutral_is_no_trade_not_a_veto(monkeypatch):
    from src.v5 import risk_gate
    monkeypatch.setattr(risk_gate.md, "closes", lambda *a, **k: None)
    monkeypatch.setattr(risk_gate.md, "returns", lambda *a, **k: None)
    reports = [_report("a", "price", p=0.55, ci=(0.4, 0.7)),
               _report("b", "price", p=0.45, ci=(0.3, 0.6))]
    ens = _synth(reports)
    v = risk_gate.assess("TEST", ens, reports, _FakeMeta(0.5, 0.0))
    assert v.verdict == "NO_TRADE"


def test_open_heat_is_zero_with_an_empty_book():
    from src.v5.risk_gate import _open_heat
    assert _open_heat([], 100_000) == 0.0


# ── learning ────────────────────────────────────────────────────────────────

@pytest.fixture
def learning_env(tmp_path, monkeypatch):
    from src.v5 import learning
    monkeypatch.setattr(learning, "V5_DIR", tmp_path)
    monkeypatch.setattr(learning, "PRED_LOG", tmp_path / "predictions.jsonl")
    monkeypatch.setattr(learning, "WEIGHTS_FILE", tmp_path / "weights.json")
    return learning


def test_prediction_log_round_trips(learning_env):
    pid = learning_env.log_prediction(
        ticker="TEST", direction="bull", p_bull=0.7, confidence=0.62,
        horizon_days=21, price=100.0,
        modules=[_report("m1").to_dict()], rationale="because")
    entries = learning_env.load_predictions()
    assert len(entries) == 1 and entries[0]["id"] == pid
    assert entries[0]["resolved"] is False
    assert entries[0]["modules"][0]["module"] == "m1"


def _seed_resolved(learning, n, correct, module="momentum", view="bull"):
    """Write `n` already-resolved predictions directly, `correct` of them right."""
    rows = []
    for i in range(n):
        won = i < correct
        rows.append({
            "id": f"p{i}", "at": (datetime.now() - timedelta(days=60)).isoformat(),
            "ticker": "TEST", "direction": "bull", "p_bull": 0.7, "confidence": 0.65,
            "horizon_days": 21, "price_at": 100.0, "resolved": True,
            "realised_return": 0.05 if won else -0.05, "correct": won,
            "modules": [{"module": module, "family": "price", "net": 30.0,
                         "view": view, "abstained": False}],
            "attribution": {"causes": ["thesis confirmed" if won else "incorrect assumptions"]},
        })
    learning.PRED_LOG.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                                 encoding="utf-8")


def test_a_small_sample_never_moves_a_weight(learning_env):
    _seed_resolved(learning_env, n=10, correct=10)
    assert learning_env.update_weights()["changed"] == 0
    assert learning_env.current_multipliers() == {}


def test_a_significant_sample_moves_the_weight_and_is_bounded(learning_env):
    _seed_resolved(learning_env, n=40, correct=32)      # 80%, p well under 0.05
    out = learning_env.update_weights()
    assert out["changed"] == 1
    mult = learning_env.current_multipliers()["momentum"]
    assert 1.0 < mult <= 1.0 + learning_env.MAX_TILT + 1e-9, "tilt must stay bounded"
    assert learning_env.weights_version() == 2


def test_a_coin_flip_record_moves_nothing(learning_env):
    _seed_resolved(learning_env, n=40, correct=20)
    assert learning_env.update_weights()["changed"] == 0


def test_weight_changes_are_reversible(learning_env):
    _seed_resolved(learning_env, n=40, correct=32)
    learning_env.update_weights()
    assert learning_env.current_multipliers()
    out = learning_env.revert_to(1)
    assert out.get("reverted_to") == 1
    assert learning_env.current_multipliers() == {}, "revert must restore the earlier weights"


def test_reverting_an_unknown_version_errors(learning_env):
    assert "error" in learning_env.revert_to(99)


def test_module_scorecard_ignores_abstentions_and_neutrals(learning_env):
    rows = [{
        "id": "x", "at": datetime.now().isoformat(), "ticker": "TEST", "direction": "bull",
        "confidence": 0.6, "horizon_days": 21, "price_at": 100.0, "resolved": True,
        "realised_return": 0.04, "correct": True,
        "modules": [
            {"module": "voter", "family": "price", "net": 30, "view": "bull", "abstained": False},
            {"module": "sitter", "family": "quant", "net": 0, "view": "neutral", "abstained": False},
            {"module": "absent", "family": "macro", "net": 0, "view": "abstain", "abstained": True},
        ],
    }]
    learning_env.PRED_LOG.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    card = learning_env.module_scorecard()
    assert "voter" in card and card["voter"]["right"] == 1
    assert "sitter" not in card and "absent" not in card


def test_bias_snapshot_reports_a_one_sided_house(learning_env):
    _seed_resolved(learning_env, n=20, correct=10)
    snap = learning_env.bias_snapshot()
    assert snap["n_predictions"] == 20
    assert snap["bull_share"] == 1.0, "an all-bullish log must be visible as such"
    assert snap["hit_rate"] == pytest.approx(0.5)


def test_attribution_names_a_specific_cause(learning_env, monkeypatch):
    import src.v5.marketdata as md
    monkeypatch.setattr(md, "returns", lambda *a, **k: None)
    monkeypatch.setattr(md, "closes", lambda *a, **k: None)
    entry = {"at": datetime.now().isoformat(), "ticker": "TEST", "horizon_days": 21,
             "confidence": 0.85,
             "modules": [{"module": "a", "net": 90, "view": "bull", "abstained": False},
                         {"module": "b", "net": -90, "view": "bear", "abstained": False}]}
    att = learning_env._attribute(entry, ret=-0.12, correct=False)
    assert att["causes"], "a wrong call must be attributed to something specific"
    assert "market randomness" not in att["causes"] or len(att["causes"]) > 1
    assert "behavioural bias" in att["causes"], "85% confidence and wrong is overconfidence"


# ── audit + report ──────────────────────────────────────────────────────────

def test_audit_and_report_render_from_a_synthetic_analysis(monkeypatch):
    from src.v5 import audit, meta as meta_mod, report, risk_gate
    monkeypatch.setattr(risk_gate.md, "closes", lambda *a, **k: None)
    monkeypatch.setattr(risk_gate.md, "returns", lambda *a, **k: None)

    reports = ([_report(f"m{i}", "price", p=0.78, ci=(0.70, 0.86)) for i in range(4)] +
               [_report("doubter", "quant", p=0.2, ci=(0.12, 0.28)),
                insufficient("gone", "macro", "TEST", "no data")])
    ens = _synth(reports)
    m = meta_mod.review("TEST", ens, reports)
    risk = risk_gate.assess("TEST", ens, reports, m)
    sa = audit.build("TEST", ens, reports, m, risk)

    assert sa.what_i_know and sa.what_i_do_not_know and sa.assumptions
    assert any("gone" in x for x in sa.what_i_do_not_know), "an abstention must appear as a gap"
    assert sa.confidence_range["confidence_range"][0] >= 0.5
    assert sa.blind_spots, "the modules' own weaknesses must surface"

    md_text = report.render({
        "ticker": "TEST", "as_of": "now", "elapsed_ms": 1, "prediction_id": None,
        "recommendation": {"action": "LONG", "headline": "h", "confidence_band": "60%-70%",
                           "invalidation": "x", "execution_note": "proposal only",
                           "risk_verdict": risk.verdict, "confidence": 0.65},
        "ensemble": ens.to_dict(), "risk": risk.to_dict(), "meta": m.to_dict(),
        "self_audit": sa.to_dict(), "modules": [r.to_dict() for r in reports],
        "module_count": {"total": 6, "reporting": 5, "abstained": 1},
        "weights_version": 1,
    })
    for section in ["Investment Committee Memo", "Dissent", "Risk gate",
                    "Meta-reasoning", "Self-audit", "Falsification tests"]:
        assert section in md_text


# ── identity ────────────────────────────────────────────────────────────────

def test_the_two_laws_survive_every_prompt_size():
    from src.v5.identity import system_prompt
    for text in (system_prompt(), system_prompt(full=False), system_prompt("ctx")):
        assert "LAW 1" in text and "LAW 2" in text
        assert text.index("LAW 1") < text.index("OPERATING PRINCIPLE"), \
            "the Laws must precede everything they override"


def test_identity_carries_context_when_given():
    from src.v5.identity import system_prompt
    assert "SPY at 500" in system_prompt("SPY at 500")


# ── track record: the flywheel and its calibration ──────────────────────────

def _seed_calibration(learning, rows):
    """rows: list of (stated_confidence, correct, direction). Writes them as
    resolved predictions straight into the patched log."""
    out = []
    for i, (conf, correct, direction) in enumerate(rows):
        out.append({
            "id": f"c{i}", "at": datetime.now().isoformat(), "ticker": "TEST",
            "direction": direction, "p_bull": 0.6, "confidence": conf,
            "horizon_days": 21, "price_at": 100.0, "resolved": True,
            "realised_return": 0.05 if correct else -0.05, "correct": correct,
            "modules": [{"module": "momentum", "family": "price", "net": 30.0,
                         "view": "bull", "abstained": False}],
            "attribution": {"causes": ["thesis confirmed" if correct else "incorrect assumptions"]},
        })
    learning.PRED_LOG.write_text("\n".join(json.dumps(r) for r in out) + "\n", encoding="utf-8")


def test_calibration_refuses_to_report_below_its_minimum(learning_env):
    from src.v5 import track_record as tr
    _seed_calibration(learning_env, [(0.6, True, "bull")] * 5)
    cal = tr.calibration()
    assert cal["measurable"] is False
    assert cal["n_resolved"] == 5 and cal["n_required"] == tr.MIN_FOR_CALIBRATION
    assert "decoration" in cal["note"]


def test_a_perfectly_calibrated_system_scores_near_zero_error(learning_env):
    from src.v5 import track_record as tr
    # 60% stated, 60% realised, 50 calls
    rows = [(0.60, True, "bull")] * 30 + [(0.60, False, "bull")] * 20
    _seed_calibration(learning_env, rows)
    cal = tr.calibration()
    assert cal["measurable"] is True
    assert cal["ece"] == pytest.approx(0.0, abs=0.01), "claimed 60%, got 60% — no gap"
    assert cal["brier"] == pytest.approx(0.24, abs=0.005)
    assert cal["skill"] > 0, "a calibrated 60% must beat a flat coin flip"
    assert "calibrated" in cal["verdict"].lower()


def test_confidence_worse_than_a_coin_flip_is_called_out_first(learning_env):
    """Claiming 90% and delivering 50% is not merely 'poorly calibrated' — the
    confidence numbers carry negative information, and that is the more severe
    finding, so it must be the one reported."""
    from src.v5 import track_record as tr
    rows = [(0.90, True, "bull")] * 20 + [(0.90, False, "bull")] * 20
    _seed_calibration(learning_env, rows)
    cal = tr.calibration()
    assert cal["ece"] == pytest.approx(0.40, abs=0.02)
    assert cal["skill"] < 0
    assert "worse than a flat 50% guess" in cal["verdict"]
    assert "uninformative" in cal["verdict"], "it must tell the reader to ignore the number"


def test_a_skilled_but_overconfident_system_is_graded_as_such(learning_env):
    """Positive skill, wide gap: the direction is informative but the
    probability attached to it is not."""
    from src.v5 import track_record as tr
    rows = [(0.95, True, "bull")] * 30 + [(0.95, False, "bull")] * 10   # claims 95%, gets 75%
    _seed_calibration(learning_env, rows)
    cal = tr.calibration()
    assert cal["skill"] > 0
    assert cal["ece"] == pytest.approx(0.20, abs=0.02)
    assert "poorly calibrated" in cal["verdict"].lower()


def test_a_thin_bucket_is_shown_but_not_scored(learning_env):
    from src.v5 import track_record as tr
    rows = ([(0.60, True, "bull")] * 15 + [(0.60, False, "bull")] * 10
            + [(0.85, True, "bull")] * 2)          # only 2 in the 80%+ band
    _seed_calibration(learning_env, rows)
    cal = tr.calibration()
    thin = [b for b in cal["buckets"] if b["n"] == 2]
    assert thin and thin[0]["reportable"] is False
    assert thin[0]["realised_frequency"] is None, "a 2-sample bucket must not report a frequency"


def test_the_flywheel_reports_a_stalled_stage_with_its_reason(learning_env):
    from src.v5 import track_record as tr
    learning_env.log_prediction(ticker="T", direction="bull", p_bull=0.7, confidence=0.6,
                                horizon_days=21, price=100.0, modules=[])
    stages = {s["stage"]: s for s in tr.flywheel()}
    assert stages["PREDICT"]["turning"] is True
    assert stages["RESOLVE"]["turning"] is False
    assert "time-gated" in stages["RESOLVE"]["blocked"]
    assert stages["REWEIGHT"]["blocked"], "an unearned reweight must explain why it has not happened"


def test_the_export_only_contains_labelled_rows(learning_env):
    from src.v5 import track_record as tr
    _seed_calibration(learning_env, [(0.6, True, "bull"), (0.6, False, "bear")])
    learning_env.log_prediction(ticker="PENDING", direction="bull", p_bull=0.7,
                                confidence=0.6, horizon_days=21, price=100.0, modules=[])
    rows = tr.training_export()
    assert len(rows) == 2, "an unresolved prediction has no label and must not be exported"
    assert all("label_correct" in r and r["realised_return"] is not None for r in rows)
    assert all(t != "PENDING" for t in (r["ticker"] for r in rows))
    assert tr.training_export_jsonl().count("\n") == 1


def test_the_headline_never_invents_a_track_record(learning_env):
    from src.v5 import track_record as tr
    learning_env.log_prediction(ticker="T", direction="bull", p_bull=0.7, confidence=0.9,
                                horizon_days=21, price=100.0, modules=[])
    head = tr.build()["headline"]
    assert head["resolved"] == 0
    assert "no track record" in head["text"].lower()
    assert head["hit_rate"] is None


def test_build_survives_every_loop_being_unavailable(learning_env, monkeypatch):
    """The page must render even if the desk, technical tracker and brain data
    are all missing — a missing loop is reported, never raised."""
    from src.v5 import track_record as tr
    monkeypatch.setattr(tr, "DATA", learning_env.V5_DIR)
    payload = tr.build()
    assert len(payload["sources"]) == 4
    assert payload["flywheel"] and payload["headline"]["text"]
