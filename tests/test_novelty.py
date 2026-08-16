"""
tests/test_novelty.py
=====================
The novelty sense (src/brain/cognitive/novelty.py).

Two failure modes, opposite and equally fatal:

  crying wolf   it fires on a thin history, or on ordinary days, and is
                ignored by the end of the week
  going quiet   it misses the day where every number is individually
                unremarkable and the combination has never happened, which is
                the only thing it can catch that a threshold cannot

Most of what follows is one or the other.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.brain.cognitive import novelty as N


def day(breadth=0.1, dispersion=12.0, conviction=14.0, mean_score=1.0,
        alerts=3.0, macro_score=5.0, ml_split=0.1) -> dict:
    return {"breadth": breadth, "dispersion": dispersion,
            "conviction": conviction, "mean_score": mean_score,
            "alerts": alerts, "macro_score": macro_score, "ml_split": ml_split}


def ordinary_history(n=60):
    """Sixty unremarkable days with a little wobble."""
    return [day(breadth=0.1 + (i % 5) * 0.01,
                dispersion=12.0 + (i % 7) * 0.2,
                conviction=14.0 + (i % 4) * 0.3,
                mean_score=1.0 + (i % 3) * 0.1,
                alerts=3.0 + (i % 3),
                macro_score=5.0 + (i % 6) * 0.2,
                ml_split=0.1 + (i % 4) * 0.01)
            for i in range(n)]


# ── it refuses to speak too early ───────────────────────────────────────────

def test_a_thin_history_says_it_cannot_tell():
    """On day two everything is unprecedented. A sense that says so teaches
    its owner to ignore it by day four."""
    r = N.assess(day(breadth=-0.9), history=ordinary_history(5))
    assert r.state == N.UNKNOWN
    assert r.speak() == ""
    assert "everything would look unprecedented" in r.note
    assert r.observations == 5


def test_the_threshold_is_stated_not_implied():
    r = N.assess(day(), history=ordinary_history(10))
    assert str(N.MIN_HISTORY) in r.note


def test_status_reports_the_gap(tmp_path):
    path = tmp_path / "h.jsonl"
    for f in ordinary_history(4):
        N.remember(f, path=path)
    st = N.status(path)
    assert st["ready"] is False and st["observations"] == 4
    assert "more observations" in st["note"]


# ── it stays quiet on ordinary days ─────────────────────────────────────────

def test_an_ordinary_day_is_familiar_and_silent():
    """A sense that comments every cycle is furniture."""
    r = N.assess(day(), history=ordinary_history())
    assert r.state == N.FAMILIAR
    assert r.speak() == ""
    assert r.drivers == []


def test_a_mildly_different_day_is_still_familiar():
    r = N.assess(day(breadth=0.13, conviction=14.6), history=ordinary_history())
    assert r.state == N.FAMILIAR


# ── it speaks when something is genuinely strange ───────────────────────────

def test_an_extreme_feature_is_named_and_quantified():
    r = N.assess(day(breadth=-0.95), history=ordinary_history())
    assert r.state in (N.UNUSUAL, N.UNPRECEDENTED)
    assert r.drivers and r.drivers[0].feature == "breadth"
    said = r.speak()
    assert "breadth is unusually low" in said
    assert "robust deviations" in said


def test_she_says_she_does_not_know_what_it_means():
    """Novelty is not direction. Anything that read this as a signal would be
    inventing an edge nobody has measured."""
    said = N.assess(day(conviction=90.0), history=ordinary_history()).speak()
    assert "I do not know what it means" in said
    assert "a reason to look, not a signal" in said


def test_the_strongest_driver_leads():
    r = N.assess(day(breadth=-0.95, alerts=40.0), history=ordinary_history())
    assert abs(r.drivers[0].robust_z) >= abs(r.drivers[-1].robust_z)


# ── the reading a threshold cannot make ─────────────────────────────────────

def test_an_unprecedented_combination_of_ordinary_numbers_is_caught():
    """THE test. Every feature sits inside its own historical range; the
    COMBINATION has never occurred. No per-feature threshold can find this,
    which is the whole reason the joint reading exists."""
    history = []
    for i in range(60):
        if i % 2:                       # calm days: low conviction, low alerts
            history.append(day(conviction=8.0 + (i % 3) * 0.2, alerts=1.0,
                               dispersion=6.0, breadth=0.3))
        else:                           # busy days: high conviction and alerts
            history.append(day(conviction=20.0 + (i % 3) * 0.2, alerts=9.0,
                               dispersion=18.0, breadth=-0.3))

    # Individually ordinary on both counts, together never seen: calm-day
    # conviction with busy-day alerts.
    r = N.assess(day(conviction=8.1, alerts=9.0, dispersion=6.0, breadth=0.3),
                 history=history)
    assert r.combination_novel, f"missed the combination (score {r.score})"
    assert r.state != N.FAMILIAR
    assert "no close match" in r.speak() or "occurred together" in r.speak()


def test_the_joint_score_reads_as_a_percentile():
    r = N.assess(day(), history=ordinary_history())
    assert 0.0 <= r.score <= 1.0


def test_a_high_percentile_alone_does_not_fire():
    """The percentile is scale-free. On a history of near-identical days a
    rounding difference still ranks above 95% of them, so isolation has to be
    material as well as rare — the same pairing the adapter gate uses."""
    flat = [day() for _ in range(60)]
    r = N.assess(day(conviction=14.0000001), history=flat)
    assert r.state == N.FAMILIAR
    assert not r.combination_novel


def test_a_genuinely_isolated_day_clears_both_gates():
    flat = [day(conviction=14.0 + (i % 3) * 0.1) for i in range(60)]
    r = N.assess(day(conviction=60.0), history=flat)
    assert r.combination_novel or r.drivers
    assert r.state != N.FAMILIAR
    assert r.isolation_ratio >= N.JOINT_MIN_RATIO or r.drivers


def test_the_percentile_is_ranked_against_normal_days_not_itself():
    """The first version ranked today's distance against its own distance
    distribution, which returns about k/n for every possible day — a score
    that could never fire."""
    history = ordinary_history()
    _, rank, ratio = N.joint_novelty(day(breadth=-0.99, conviction=99.0), history)
    assert rank > 0.9 and ratio > N.JOINT_MIN_RATIO


# ── robust statistics, and why they are robust ──────────────────────────────

def test_one_crash_does_not_blind_the_sense_for_months():
    """With mean and standard deviation, a single -0.99 breadth day inflates
    the spread so far that nothing looks unusual afterwards. The median and
    MAD do not move."""
    history = ordinary_history(59) + [day(breadth=-0.99)]
    r = N.assess(day(breadth=-0.9), history=history)
    assert r.drivers, "a near-repeat of a crash should still register"
    assert r.drivers[0].feature == "breadth"


def test_a_feature_that_never_moves_is_not_evidence():
    flat = [day(macro_score=5.0) for _ in range(60)]
    r = N.assess(day(macro_score=5.0), history=flat)
    assert all(d.feature != "macro_score" for d in r.drivers)


def test_a_degenerate_history_cannot_produce_an_infinite_score():
    flat = [day(macro_score=5.0) for _ in range(60)]
    z, _, _ = N.robust_z(999.0, [5.0] * 60)
    assert z == N.FEATURE_UNUSUAL          # capped, not infinite
    r = N.assess(day(macro_score=999.0), history=flat)
    assert all(abs(d.robust_z) < 100 for d in r.drivers)


def test_median_and_mad():
    assert N.median([3, 1, 2]) == 2
    assert N.median([4, 1, 2, 3]) == 2.5
    assert N.mad([1, 1, 1]) == 0.0
    assert N.mad([1, 2, 3, 4, 5]) > 0


# ── memory ──────────────────────────────────────────────────────────────────

def test_today_is_judged_before_it_is_remembered(tmp_path):
    """Otherwise the strangest day on record quietly makes itself look one
    observation less strange."""
    path = tmp_path / "h.jsonl"
    for f in ordinary_history():
        N.remember(f, path=path)

    snapshot = SimpleNamespace(
        signals={f"T{i}": SimpleNamespace(composite_score=-95.0) for i in range(20)},
        ml={}, alerts=[], macro=SimpleNamespace(macro_score=5.0))
    r = N.observe(snapshot, path=path)
    assert r.state != N.FAMILIAR
    assert r.observations == 60                    # judged against 60, not 61
    assert len(N.load_history(path)) == 61         # and then remembered


def test_an_unchanged_market_is_not_a_new_observation(tmp_path):
    """The brain cycle looks every fifteen minutes; the desk refreshes far
    less often. Remembering every look would fill her memory with one day
    repeated forty times — the spread would collapse, the next real change
    would read as unprecedented, and 'usual' would mean 'the last two days'."""
    path = tmp_path / "h.jsonl"
    assert N.remember(day(), path=path) is True
    for _ in range(40):
        assert N.remember(day(), path=path) is False
    assert len(N.load_history(path)) == 1

    assert N.remember(day(breadth=0.4), path=path) is True
    assert len(N.load_history(path)) == 2


def test_observing_repeatedly_does_not_inflate_the_history(tmp_path):
    path = tmp_path / "h.jsonl"
    snapshot = SimpleNamespace(
        signals={"A": SimpleNamespace(composite_score=10.0)},
        ml={}, alerts=[], macro=SimpleNamespace(macro_score=5.0))
    for _ in range(10):
        N.observe(snapshot, path=path)
    assert len(N.load_history(path)) == 1


def test_strange_days_are_remembered_too(tmp_path):
    """A sense that only remembers ordinary days finds every repetition of a
    new regime just as shocking as the first, and never learns."""
    path = tmp_path / "h.jsonl"
    N.remember(day(breadth=-0.99), path=path)
    assert N.load_history(path)[0]["breadth"] == -0.99


def test_a_corrupt_line_does_not_blind_the_sense(tmp_path):
    path = tmp_path / "h.jsonl"
    N.remember(day(breadth=0.1), path=path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{ this is not json\n")
    N.remember(day(breadth=0.5), path=path)
    assert len(N.load_history(path)) == 2


def test_history_is_bounded(tmp_path):
    path = tmp_path / "h.jsonl"
    for f in ordinary_history(N.MAX_HISTORY + 20):
        N.remember(f, path=path)
    assert len(N.load_history(path)) == N.MAX_HISTORY


# ── the features themselves ─────────────────────────────────────────────────

def test_features_describe_the_shape_of_a_day():
    snapshot = SimpleNamespace(
        signals={"A": SimpleNamespace(composite_score=40.0),
                 "B": SimpleNamespace(composite_score=-20.0),
                 "C": SimpleNamespace(composite_score=0.0)},
        ml={"A": SimpleNamespace(overall_bullish=0.9)},
        alerts=["A"], macro=SimpleNamespace(macro_score=7.5))
    f = N.features_from(snapshot)
    assert f["breadth"] == pytest.approx(0.0)      # one up, one down, one flat
    assert f["conviction"] == pytest.approx(20.0)
    assert f["alerts"] == 1.0
    assert f["macro_score"] == 7.5


def test_an_empty_market_does_not_crash_the_sense():
    f = N.features_from(SimpleNamespace(signals={}, ml={}, alerts=[], macro=None))
    assert f["breadth"] == 0.0 and f["conviction"] == 0.0


def test_the_feature_set_stays_small():
    """In a space of 700 per-ticker dimensions everything is far from
    everything else, and the sense would fire constantly while measuring
    nothing."""
    assert len(N.features_from(SimpleNamespace(
        signals={}, ml={}, alerts=[], macro=None))) <= 8
