"""
tests/test_probability_semantics.py
===================================
A score never silently becomes a probability, and a confidence never enters a
Brier score.

The ledger once scored V5's P(bull), the desk's mapped conviction and nothing
as P(correct) and reported a Brier of 0.288 that measured nothing. These tests
pin the vocabulary (src/intelligence/semantics.py), the refusal of every
proper scoring rule to read a non-probability, and the ledger's storage of an
explicit probability_type.
"""
import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.intelligence import semantics as S  # noqa: E402
from src.intelligence.semantics import ProbabilityType as T  # noqa: E402


@pytest.fixture
def spine(tmp_path, monkeypatch):
    monkeypatch.setenv("ARIA_CORE_DB", str(tmp_path / "spine.db"))
    from src.core import bus as bus_mod
    monkeypatch.setattr(bus_mod, "_initialised", False)
    monkeypatch.setattr(bus_mod, "_subscribers", [])
    from src.core import ledger as ledger_mod
    monkeypatch.setattr(ledger_mod, "_ready", False)
    return ledger_mod


def _row(**kw):
    base = {"direction": "bull", "p_correct": 0.62, "probability_kind": "stated"}
    base.update(kw)
    return base


# ── the vocabulary ──────────────────────────────────────────────────────────

def test_only_stated_and_calibrated_are_scorable():
    assert S.SCORABLE == {T.STATED_PROBABILITY, T.CALIBRATED_PROBABILITY}


@pytest.mark.parametrize("kind,expected", [
    ("stated", T.STATED_PROBABILITY), ("calibrated", T.CALIBRATED_PROBABILITY),
    ("mapped", T.CONFIDENCE), ("score", T.SCORE), ("none", T.NONE),
    (None, T.UNKNOWN), ("probability", T.UNKNOWN)])
def test_type_comes_from_what_was_recorded(kind, expected):
    assert S.type_of({"probability_kind": kind, "probability": 0.7}) is expected


def test_an_unlabelled_number_is_unknown_not_a_probability():
    """30 V5 rows of 2026-10-06 carry a confidence and no kind."""
    r = {"probability": 0.5757, "confidence": 0.5268, "direction": "bull"}
    assert S.type_of(r) is T.UNKNOWN
    assert S.scorable_probability(r) == (None, S.KIND_UNRECORDED)


def test_explicit_type_wins_over_kind():
    assert S.type_of({"probability_type": "SCORE", "probability_kind": "stated"}) is T.SCORE


@pytest.mark.parametrize("row,reason", [
    (_row(probability_kind="mapped"), S.NOT_A_PROBABILITY),
    (_row(probability_kind="score"), S.NOT_A_PROBABILITY),
    (_row(probability_kind="none"), S.NO_NUMBER),
    (_row(direction="neutral"), S.NOT_DIRECTIONAL),
    (_row(p_correct=1.4), S.OUT_OF_RANGE),
    (_row(p_correct=float("nan")), S.OUT_OF_RANGE),
    (_row(probability_kind="calibrated"), S.NO_CALIBRATION_VERSION),
])
def test_every_non_probability_says_why(row, reason):
    assert S.scorable_probability(row) == (None, reason)


def test_a_stated_directional_probability_is_scorable():
    assert S.scorable_probability(_row()) == (0.62, None)
    assert S.scorable_probability(_row(probability_kind="calibrated",
                                       calibration_version="platt-v1")) == (0.62, None)


# ── the scoring rules refuse ────────────────────────────────────────────────

@pytest.mark.parametrize("ptype", ["CONFIDENCE", "SCORE", "NONE", "UNKNOWN", "made-up"])
def test_brier_refuses_anything_that_is_not_a_probability(ptype):
    with pytest.raises(S.NotAProbability):
        S.brier_score([(0.7, 1, "STATED_PROBABILITY"), (0.86, 0, ptype)])
    with pytest.raises(S.NotAProbability):
        S.log_loss([(0.86, 0, ptype)])


def test_brier_and_log_loss_known_answers():
    pts = [(0.8, 1, "STATED_PROBABILITY"), (0.6, 0, "STATED_PROBABILITY")]
    assert S.brier_score(pts) == pytest.approx(((0.2 ** 2) + (0.6 ** 2)) / 2)
    import math
    assert S.log_loss(pts) == pytest.approx(-(math.log(0.8) + math.log(0.4)) / 2)
    assert S.brier_score([]) is None


def test_require_probability_rejects_out_of_range_and_non_numbers():
    with pytest.raises(S.NotAProbability):
        S.require_probability(1.2, "STATED_PROBABILITY")
    with pytest.raises(S.NotAProbability):
        S.require_probability("high", "STATED_PROBABILITY")
    assert S.require_probability(0.55, T.STATED_PROBABILITY) == 0.55


# ── the ledger stores the type and enforces it ─────────────────────────────

def _read(ledger, pid):
    with ledger.connect() as conn:
        return dict(conn.execute("SELECT * FROM predictions WHERE id=?", (pid,)).fetchone())


def test_a_score_never_gets_a_p_correct(spine):
    pid = spine.record_prediction(subject="AAPL", claim="t", direction="bull",
                                  probability=0.81, probability_type="SCORE",
                                  p_correct=0.81, source="t", announce=False)
    r = _read(spine, pid)
    assert r["probability_type"] == "SCORE"
    assert r["probability_kind"] == "score"
    assert r["p_correct"] is None


def test_a_mapped_confidence_is_stored_as_confidence(spine):
    pid = spine.record_prediction(subject="AAPL", claim="t", direction="bull",
                                  probability=0.86, probability_kind="mapped",
                                  source="t", announce=False)
    r = _read(spine, pid)
    assert (r["probability_type"], r["p_correct"]) == ("CONFIDENCE", None)


def test_a_calibrated_probability_without_a_version_is_not_stored_as_one(spine):
    pid = spine.record_prediction(subject="AAPL", claim="t", direction="bull",
                                  p_correct=0.66, probability_type="CALIBRATED_PROBABILITY",
                                  source="t", announce=False)
    r = _read(spine, pid)
    assert r["probability_type"] == "UNKNOWN"
    assert r["p_correct"] is None


def test_a_versioned_calibrated_probability_is_kept(spine):
    pid = spine.record_prediction(subject="AAPL", claim="t", direction="bull",
                                  p_correct=0.66, probability_type="CALIBRATED_PROBABILITY",
                                  calibration_version="platt-2026-11", source="t",
                                  announce=False)
    r = _read(spine, pid)
    assert (r["probability_type"], r["p_correct"], r["calibration_version"]) == \
        ("CALIBRATED_PROBABILITY", 0.66, "platt-2026-11")


def test_no_kind_and_no_number_is_recorded_as_unknown(spine):
    pid = spine.record_prediction(subject="AAPL", claim="t", direction="bull",
                                  source="t", announce=False)
    assert _read(spine, pid)["probability_type"] == "UNKNOWN"


def test_ledger_calibration_still_reads_only_stated(spine):
    """The core calibration path is untouched by the new kinds."""
    for i in range(3):
        spine.record_prediction(subject="AAPL", claim=f"s{i}", direction="bull",
                                p_correct=0.9, probability_type="SCORE", source="t",
                                created_at=f"2026-01-0{i + 1}T10:00:00", announce=False)
    cal = spine.calibration()
    assert cal["n_resolved"] == 0


# ── every production caller declares what its number is ────────────────────

def _calls(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("record_prediction"):
            yield node


def test_every_production_record_prediction_call_declares_its_probability():
    """A caller that passed a strength as `probability` with no kind would be
    stored as a stated probability. None may."""
    files = [p for p in (ROOT / "src").rglob("*.py") if "__pycache__" not in p.parts]
    files += [ROOT / "main.py"]
    seen = 0
    for f in files:
        if f.name == "ledger.py" and f.parent.name == "core":
            continue
        for call in _calls(f):
            seen += 1
            kws = {k.arg for k in call.keywords}
            assert kws & {"probability_kind", "probability_type"}, (
                f"{f.relative_to(ROOT)}:{call.lineno} records a prediction without "
                f"declaring probability_kind / probability_type")
    assert seen >= 5, "the scan found too few callers — it is broken"
