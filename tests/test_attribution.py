"""
tests/test_attribution.py
=========================
Why a prediction was right or wrong — Phases 16 and 19.

The property under test is that the module distinguishes a THESIS FAILURE from
a COIN FLIP. A wrong 1-day call where the price moved 0.04% and one where it
moved 8% against the thesis are both "correct: false" in the ledger, and
scoring them identically is most of why short-horizon hit rates carry so
little information.

The other property, equally important, is the refusal in `_by_regime`: with one
regime in the record, a breakdown is a single row wearing the authority of a
comparison, and the module must say UNMEASURABLE instead.
"""
from __future__ import annotations

import pytest

from src.core import attribution as at


def _p(subject="AAPL", direction="bull", ret=0.05, correct=True,
       horizon=1, regime=None, strategy="s", pid=None):
    return {"id": pid or f"{subject}-{ret}", "subject": subject,
            "direction": direction, "actual_return": ret, "correct": correct,
            "horizon_days": horizon, "regime_at": regime, "strategy": strategy,
            "price_at": 100.0,
            # An ungraded prediction has no end price either — deriving one
            # from a None return is what the fixture was doing.
            "price_end": None if ret is None else 100.0 * (1 + ret),
            "probability": 0.7}


# ── the noise band ──────────────────────────────────────────────────────────

def test_a_tiny_move_against_the_call_is_noise_not_a_thesis_failure():
    a = at.attribute_one(_p(ret=-0.0004, correct=False), sigma=0.02)
    assert a["reason"] == "NOISE_MISS"
    assert a["decisive"] is False


def test_a_large_move_against_the_call_is_a_decisive_miss():
    a = at.attribute_one(_p(ret=-0.08, correct=False), sigma=0.02)
    assert a["reason"] == "DECISIVE_MISS"
    assert a["decisive"] is True
    assert a["sigmas"] == pytest.approx(4.0, abs=0.01)


def test_a_tiny_move_with_the_call_is_not_credited_as_skill():
    """Being right by 0.04% on a 2% sigma is not evidence of a good thesis."""
    a = at.attribute_one(_p(ret=0.0004, correct=True), sigma=0.02)
    assert a["reason"] == "NOISE_HIT"
    assert a["decisive"] is False


def test_the_band_boundary_is_half_a_sigma():
    just_under = at.attribute_one(_p(ret=0.0099, correct=True), sigma=0.02)
    just_over = at.attribute_one(_p(ret=0.0101, correct=True), sigma=0.02)
    assert just_under["reason"] == "NOISE_HIT"
    assert just_over["reason"] == "DECISIVE_HIT"


def test_neutral_calls_get_their_own_reasons():
    held = at.attribute_one(_p(direction="neutral", ret=0.001, correct=True), sigma=0.02)
    broke = at.attribute_one(_p(direction="neutral", ret=0.09, correct=False), sigma=0.02)
    assert held["reason"] == "NEUTRAL_HELD"
    assert broke["reason"] == "NEUTRAL_BROKE"


def test_an_ungraded_prediction_is_unscorable_not_a_miss():
    """Absent evidence must not become evidence of failure."""
    a = at.attribute_one(_p(ret=None, correct=None), sigma=0.02)
    assert a["reason"] == "UNSCORABLE"
    assert a["decisive"] is None


# ── sigma estimation ────────────────────────────────────────────────────────

def test_sigma_prefers_the_subjects_own_returns():
    """Crypto and a utility do not share a noise band."""
    wide = at._horizon_sigma([0.10, -0.12, 0.09, -0.11, 0.13, -0.08], 1)
    assert wide > 0.05


def test_sigma_falls_back_when_history_is_too_thin():
    """Four points cannot estimate a standard deviation worth using."""
    s = at._horizon_sigma([0.01, 0.02, -0.01, 0.005], horizon_days=4)
    assert s == pytest.approx(at.DEFAULT_DAILY_VOL * 2, abs=1e-9)


def test_sigma_does_not_rescale_already_horizon_scaled_returns():
    """Ledger returns are the move over that prediction's own horizon, so
    applying sqrt(time) again would double-count."""
    rets = [0.02, -0.02, 0.03, -0.03, 0.01, -0.01]
    s1 = at._horizon_sigma(rets, horizon_days=1)
    s20 = at._horizon_sigma(rets, horizon_days=20)
    assert s1 == s20


# ── the decomposition ───────────────────────────────────────────────────────

def _stub_ledger(monkeypatch, rows):
    from src.core import ledger
    monkeypatch.setattr(ledger, "predictions", lambda **kw: rows)


def test_decisive_and_noise_slices_are_reported_separately(monkeypatch):
    rows = ([_p(subject=f"D{i}", ret=0.09, correct=True, pid=f"d{i}") for i in range(30)]
            + [_p(subject=f"N{i}", ret=0.0001, correct=False, pid=f"n{i}") for i in range(30)])
    _stub_ledger(monkeypatch, rows)
    out = at.decompose()
    assert out["decisive_moves"]["n"] == 30
    assert out["noise_moves"]["n"] == 30
    assert "coin flips" in out["noise_moves"]["note"]


def test_reason_mix_percentages_sum_to_one_hundred(monkeypatch):
    rows = [_p(subject=f"T{i}", ret=0.09 if i % 2 else 0.0001,
               correct=bool(i % 3), pid=f"t{i}") for i in range(60)]
    _stub_ledger(monkeypatch, rows)
    out = at.decompose()
    assert sum(v["pct"] for v in out["reason_mix"].values()) == pytest.approx(100.0, abs=0.5)


def test_slices_below_the_threshold_are_undecided(monkeypatch):
    rows = [_p(subject=f"T{i}", direction="bear", ret=0.09, correct=False, pid=f"t{i}")
            for i in range(10)]
    _stub_ledger(monkeypatch, rows)
    out = at.decompose()
    assert out["by_direction"]["bear"]["verdict"].startswith("undecided")


# ── the §19 refusal ─────────────────────────────────────────────────────────

def test_one_regime_is_reported_unmeasurable(monkeypatch):
    """The live case: 61 of 61 desk events are 'Expansion (Goldilocks)'."""
    rows = [_p(subject=f"T{i}", regime="Expansion (Goldilocks)", pid=f"t{i}")
            for i in range(60)]
    _stub_ledger(monkeypatch, rows)
    out = at.decompose()
    reg = out["by_regime"]
    assert reg["measurable"] is False
    assert "at least two regimes" in reg["note"]
    assert reg["regimes_in_record"] == ["Expansion (Goldilocks)"]


def test_two_regimes_become_measurable(monkeypatch):
    """Nothing needs changing for this to start working — only the market."""
    rows = ([_p(subject=f"A{i}", regime="Expansion", correct=True, ret=0.09, pid=f"a{i}")
             for i in range(30)]
            + [_p(subject=f"B{i}", regime="Contraction", correct=False, ret=-0.09, pid=f"b{i}")
               for i in range(30)])
    _stub_ledger(monkeypatch, rows)
    reg = at.decompose()["by_regime"]
    assert reg["measurable"] is True
    assert set(reg["slices"]) == {"Expansion", "Contraction"}


def test_unrecorded_regimes_are_counted_not_invented(monkeypatch):
    rows = [_p(subject=f"T{i}", regime=None, pid=f"t{i}") for i in range(20)]
    _stub_ledger(monkeypatch, rows)
    reg = at.decompose()["by_regime"]
    assert reg["measurable"] is False
    assert reg["unrecorded"] == 20
    assert reg["regimes_in_record"] == []


# ── the reading ─────────────────────────────────────────────────────────────

def test_reading_declines_below_the_event_minimum(monkeypatch):
    rows = [_p(subject=f"T{i}", pid=f"t{i}") for i in range(10)]
    _stub_ledger(monkeypatch, rows)
    assert "below the" in at.decompose()["reading"]


def test_reading_names_the_noise_share(monkeypatch):
    rows = ([_p(subject=f"D{i}", ret=0.09, correct=True, pid=f"d{i}") for i in range(60)]
            + [_p(subject=f"N{i}", ret=0.0001, correct=True, pid=f"n{i}") for i in range(40)])
    _stub_ledger(monkeypatch, rows)
    reading = at.decompose()["reading"]
    assert "horizon-sigma" in reading and "coin flips" in reading


def test_explain_reports_which_sigma_basis_it_used(monkeypatch):
    rows = [_p(subject="AAPL", ret=0.01 * i, correct=True, pid=f"p{i}") for i in range(8)]
    _stub_ledger(monkeypatch, rows)
    out = at.explain("p3")
    assert out["prediction"]["id"] == "p3"
    assert "own realised returns" in out["sigma_basis"]


def test_explain_is_honest_about_a_thin_sigma(monkeypatch):
    rows = [_p(subject="RARE", ret=0.02, correct=True, pid="only")]
    _stub_ledger(monkeypatch, rows)
    out = at.explain("only")
    assert "default" in out["sigma_basis"] and "too few" in out["sigma_basis"]


def test_explain_404s_on_an_unknown_id(monkeypatch):
    _stub_ledger(monkeypatch, [])
    assert "error" in at.explain("nope")
