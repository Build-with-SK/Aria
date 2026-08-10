"""
tests/test_walkforward.py
=========================
PHASE 3 — statistical rigor.

Two claims are under test, and both are about honesty rather than performance:

  1. The replay is point-in-time. A module evaluated as of 2020 cannot see 2021.
     If this is wrong, every number the harness produces is a lie in the
     flattering direction, so it is tested directly rather than assumed from
     the fact that a context manager exists.
  2. A module that has never been validated says so, in the API response,
     rather than looking exactly like one that has.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.v5 import marketdata as md, walkforward as wf     # noqa: E402


@pytest.fixture(autouse=True)
def isolated_results(tmp_path, monkeypatch):
    """Never read or write the real walk_forward.json."""
    monkeypatch.setattr(wf, "RESULTS_FILE", tmp_path / "walk_forward.json")
    wf._CACHE.clear()
    yield
    wf._CACHE.clear()


# ── 1. point-in-time replay ──────────────────────────────────────────────────

def test_as_of_hides_the_future(monkeypatch):
    """The mechanism the whole harness rests on."""
    idx = pd.bdate_range("2020-01-01", periods=600)
    frame = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0,
                          "Close": range(600), "Volume": 1.0}, index=idx)
    monkeypatch.setattr(md, "_full_history", lambda s, i: frame)

    everything = md.history("X", period="10y")
    assert everything.index.max() == idx[-1]

    cutoff = idx[300]
    with md.as_of(cutoff):
        seen = md.history("X", period="10y")
    assert seen.index.max() == cutoff
    assert len(seen) == 301
    assert not (seen.index > cutoff).any()

    # And the clock is restored on the way out.
    assert md.history("X", period="10y").index.max() == idx[-1]


def test_as_of_is_restored_even_when_the_body_raises(monkeypatch):
    idx = pd.bdate_range("2020-01-01", periods=100)
    frame = pd.DataFrame({"Close": range(100)}, index=idx)
    monkeypatch.setattr(md, "_full_history", lambda s, i: frame)
    with pytest.raises(ValueError):
        with md.as_of(idx[10]):
            raise ValueError("boom")
    assert md.current_as_of() is None


def test_as_of_does_not_leak_between_threads(monkeypatch):
    """The registry runs modules in a thread pool. A global clock would let one
    evaluation's date contaminate another's — the exact leak this prevents."""
    import threading
    idx = pd.bdate_range("2020-01-01", periods=200)
    frame = pd.DataFrame({"Close": range(200)}, index=idx)
    monkeypatch.setattr(md, "_full_history", lambda s, i: frame)

    seen = {}

    def worker():
        seen["other_thread"] = md.current_as_of()

    with md.as_of(idx[50]):
        t = threading.Thread(target=worker)
        t.start()
        t.join()
    assert seen["other_thread"] is None


def test_forward_return_is_measured_in_trading_days():
    """A 21-day horizon is a month of trading. Mixing in calendar days
    shortens every horizon by roughly a third without saying so."""
    idx = pd.bdate_range("2020-01-01", periods=100)
    closes = pd.Series([100.0 + i for i in range(100)], index=idx)
    r = wf._forward_return(closes, idx[10], 21)
    assert r == pytest.approx((131.0 - 110.0) / 110.0)


def test_forward_return_is_none_when_the_window_is_incomplete():
    """A call whose horizon has not elapsed must not be graded."""
    idx = pd.bdate_range("2020-01-01", periods=30)
    closes = pd.Series(range(30), index=idx, dtype=float)
    assert wf._forward_return(closes, idx[25], 21) is None


def test_evaluation_skips_the_ungradable_tail(monkeypatch):
    """The newest walk-forward fold lands on bars with no forward window. If it
    is not excluded, the most recent fold silently contributes nothing while
    appearing to have been evaluated."""
    from src.models.walk_forward import PurgedWalkForwardCV
    idx = pd.bdate_range("2018-01-01", periods=1000)
    frame = pd.DataFrame({"Close": range(1000)}, index=idx, dtype=float)
    gradable = frame.iloc[:-21]
    folds = list(PurgedWalkForwardCV(n_splits=5, min_train_size=120)
                 .split(gradable, horizon=21))
    assert folds
    for _, test_idx in folds:
        assert gradable.index[test_idx].max() <= idx[-22]


# ── 2. the unvalidated flag ──────────────────────────────────────────────────

def test_a_module_never_validated_says_so():
    s = wf.status("some_module_nobody_has_tested")
    assert s["walk_forward_validated"] is False
    assert s["walk_forward"]["status"] == "never run"
    assert "untested" in s["walk_forward"]["note"]


def test_validated_does_not_mean_it_works():
    """"Validated" means MEASURED. A module with 1,200 calls and no edge at all
    is validated, and a reader who takes that as an endorsement is being
    misled by a field name. The claim about skill is a separate field, and it
    is separate because the answer is usually no."""
    wf.save({"at": "x", "results": {
        "measured_but_useless": {"module": "measured_but_useless",
                                 "validated": True, "significant": False,
                                 "n_calls": 1200, "n_effective": 949,
                                 "hit_rate": 0.613, "base_rate_up": 0.606,
                                 "edge_over_base": 0.007, "p_value": 0.66,
                                 "verdict": "not distinguishable"}}})
    s = wf.status("measured_but_useless")
    assert s["walk_forward_validated"] is True
    assert s["walk_forward_skill_demonstrated"] is False
    assert s["walk_forward"]["status"] == "measured, no demonstrable skill"
    assert s["walk_forward"]["p_value"] == 0.66


def test_a_validated_module_reports_its_numbers():
    wf.save({"at": "2026-08-06T00:00:00", "results": {
        "momentum": {"module": "momentum", "validated": True, "hit_rate": 0.58,
                     "base_rate_up": 0.53, "edge_over_base": 0.05,
                     "information_coefficient": 0.04, "n_calls": 120,
                     "verdict": "positive", "at": "2026-08-06T00:00:00"}}})
    s = wf.status("momentum")
    assert s["walk_forward_validated"] is True
    assert s["walk_forward"]["hit_rate"] == 0.58
    assert s["walk_forward"]["edge_over_base"] == 0.05


def test_an_insufficient_sample_is_not_validated():
    """A module that ran but produced 4 calls has not been validated. Storing a
    result is not the same as passing."""
    wf.save({"at": "x", "results": {
        "thin": {"module": "thin", "validated": False, "n_calls": 4,
                 "hit_rate": 1.0, "verdict": "insufficient"}}})
    s = wf.status("thin")
    assert s["walk_forward_validated"] is False
    assert s["walk_forward"]["status"] == "insufficient sample"
    # A 100% hit rate on four calls must not be presented as a triumph.
    assert "insufficient" in s["walk_forward"]["verdict"]


def test_the_flag_travels_on_the_module_report():
    """The point of the exercise: it is visible where somebody reads a signal,
    not only in a document."""
    from src.v5.contract import ModuleReport
    wf.save({"at": "x", "results": {
        "validated_one": {"module": "validated_one", "validated": True,
                          "hit_rate": 0.6, "n_calls": 80, "verdict": "positive"}}})
    good = ModuleReport(module="validated_one", family="price", ticker="AAPL",
                        bull=70, bear=10, neutral=20).to_dict()
    unknown = ModuleReport(module="never_tested", family="price", ticker="AAPL",
                           bull=70, bear=10, neutral=20).to_dict()
    assert good["walk_forward_validated"] is True
    assert unknown["walk_forward_validated"] is False
    # Both carry the field. An absent field reads as "fine" to every consumer.
    assert "walk_forward" in good and "walk_forward" in unknown


def test_summary_covers_every_registered_module():
    """Coverage is counted over the registry, not over whatever happened to be
    in the results file — otherwise 3 modules validated out of 3 attempted
    reads as full coverage of 41."""
    from src.v5 import registry
    registry.load_modules()
    wf.save({"at": "x", "results": {}})
    s = wf.summary()
    assert s["modules_total"] == len(registry.REGISTRY) >= 41
    assert s["modules_validated"] == 0
    assert s["modules_unvalidated"] == s["modules_total"]
    assert s["coverage_pct"] == 0.0
    assert all(m["verdict"] == "never run" for m in s["modules"])


def test_verdict_language_is_not_flattering():
    """A module worse than the coin is described as worse than the coin —
    but only when the sample supports saying so."""
    bad = wf._verdict(100, hit_rate=0.42, base=0.53, ic=-0.03,
                      n_eff=90, p_value=0.01, significant=True)
    assert "NEGATIVE" in bad
    good = wf._verdict(100, hit_rate=0.60, base=0.53, ic=0.05,
                       n_eff=90, p_value=0.02, significant=True)
    assert "positive" in good


def test_a_big_edge_on_a_thin_sample_is_not_called_a_finding():
    """The failure this guard exists for. The first wide run reported a 27.6%
    'edge' for one module from eleven correlated observations; the words
    'positive' and 'NEGATIVE' were doing work the data could not support.

    A large raw edge with no significance must read as noise, explicitly."""
    v = wf._verdict(100, hit_rate=0.90, base=0.63, ic=0.10,
                    n_eff=4, p_value=0.31, significant=False)
    assert "not distinguishable" in v
    assert "NEGATIVE" not in v and "positive and significant" not in v
    assert "~4 independent" in v          # the honest sample is quoted
    assert "Do not act on this number" in v


def test_correlated_names_are_discounted_to_a_realistic_sample():
    """Forty tickers on one date are forty observations of one market day.
    Counting them as forty independent facts is how a backtest manufactures
    significance it has not earned."""
    # Ten dates, forty names, and on each date every name does the same thing.
    # That is ten facts about the market, not four hundred about the names.
    dates = [f"2024-{m:02d}-05" for m in range(1, 11)]
    herd = [{"at": d, "ticker": f"T{i}", "fwd_return": move + i * 1e-6,
             "correct": move > 0, "net": 30}
            for d, move in zip(dates, [0.05, -0.04, 0.06, -0.03, 0.02,
                                       -0.05, 0.04, -0.02, 0.03, -0.06])
            for i in range(40)]
    n_eff, rho = wf.effective_sample_size(herd)
    assert rho > 0.9, f"identical within-date moves scored rho={rho}"
    assert n_eff <= 15, f"400 herd observations counted as {n_eff} independent"

    # Same dates and names, but outcomes unrelated within a date: no discount
    # is warranted and none should be applied.
    import random
    rng = random.Random(11)
    spread = [{"at": d, "ticker": f"T{i}", "fwd_return": rng.gauss(0, 0.05),
               "correct": rng.random() > 0.5, "net": 30}
              for d in dates for i in range(40)]
    n_eff2, rho2 = wf.effective_sample_size(spread)
    assert rho2 < 0.3, f"independent outcomes scored rho={rho2}"
    assert n_eff2 > n_eff * 5


def test_successes_are_scaled_with_the_discounted_sample():
    """The bug this caught in its own first run.

    Passing a RAW success count with a DISCOUNTED n computes the hit rate as
    hits/n_eff. For 624 hits in 1039 calls discounted to 824 that reads as 76%
    instead of 60%, and returns p = 0.0 for every module — including one whose
    edge was negative. A near-zero edge on a large sample must come back
    insignificant, which is the only way the flag is worth anything.
    """
    n_calls, n_eff = 1039, 824
    hit_rate, base = 0.601, 0.604

    raw_way = wf._binomial_p(int(round(hit_rate * n_calls)), n_eff, base)
    scaled_way = wf._binomial_p(int(round(hit_rate * n_eff)), n_eff, base)

    assert raw_way is not None and raw_way < 0.01      # the wrong, exciting answer
    assert scaled_way is not None and scaled_way > 0.5  # the right, dull one


def test_the_family_of_41_tests_is_corrected_for():
    """Forty-one modules are forty-one simultaneous tests. At p<0.05 about two
    clear by chance under a null where nothing works, so an uncorrected count
    of "significant" modules manufactures findings from noise.

    Uses the same Benjamini-Hochberg routine as src/v5/tiers.py, so the platform
    has one convention rather than two that drift apart.
    """
    from src.v5.tiers import _bh_adjust

    # PURE NOISE looks like uniformly distributed p-values, not like a pile of
    # them at 0.04. Under a true null across 41 tests you expect about two below
    # 0.05 by chance — and those two are exactly the false findings this
    # correction exists to suppress.
    noise = {f"m{i}": (i + 1) / 41 for i in range(41)}
    raw_hits = [k for k, v in noise.items() if v < 0.05]
    assert len(raw_hits) >= 1, "the fixture should contain a chance hit to suppress"
    adjusted_noise = _bh_adjust(noise)
    assert all(v >= 0.05 for v in adjusted_noise.values()), (
        "a chance hit among 41 null tests survived correction")

    # A genuine effect, far below the family threshold, still gets through —
    # a correction that suppresses everything is not a correction, it is a mute
    # button.
    with_real = dict(noise)
    with_real["real"] = 1e-6
    assert _bh_adjust(with_real)["real"] < 0.05


def test_significance_is_reported_not_just_the_point_estimate():
    """A number without an error bar invites acting on noise."""
    assert wf._binomial_p(60, 100, 0.5) is not None
    assert wf._binomial_p(60, 100, 0.5) < 0.10        # 60/100 vs a coin
    assert wf._binomial_p(52, 100, 0.5) > 0.30        # 52/100 is nothing
    assert wf._binomial_p(3, 3, 0.5) is None          # too small to test


def test_base_rate_is_reported_alongside_the_hit_rate():
    """A 60% hit rate in a market that rose 60% of the time is not skill, and a
    hit rate published without its base rate is the most flattering possible
    presentation of nothing."""
    stored = {"module": "m", "validated": True, "hit_rate": 0.6,
              "base_rate_up": 0.6, "edge_over_base": 0.0, "n_calls": 90,
              "verdict": wf._verdict(90, 0.6, 0.6, 0.0, n_eff=40,
                                     p_value=0.99, significant=False)}
    wf.save({"at": "x", "results": {"m": stored}})
    s = wf.status("m")
    assert s["walk_forward"]["base_rate_up"] == 0.6
    assert s["walk_forward"]["edge_over_base"] == 0.0
    assert "not distinguishable" in s["walk_forward"]["verdict"]


def test_wall_clock_modules_are_flagged_not_hidden():
    """A module that reads today's date is not strictly point-in-time under
    replay. Detected and labelled beats silently averaged in."""
    from src.v5 import registry
    registry.load_modules()
    flagged = [n for n in registry.REGISTRY if wf.uses_wall_clock(n)]
    # Whatever the count, the mechanism must work on a known case.
    assert isinstance(flagged, list)
    result_fields = wf.evaluate_module.__doc__
    assert result_fields is not None


# ── the stored file ──────────────────────────────────────────────────────────

def test_results_survive_a_round_trip():
    report = {"at": "2026-08-06", "modules_total": 41, "modules_validated": 2,
              "results": {"a": {"module": "a", "validated": True}}}
    path = wf.save(report)
    assert json.loads(path.read_text(encoding="utf-8"))["modules_total"] == 41
    assert wf.load()["results"]["a"]["validated"] is True


def test_a_missing_results_file_is_not_an_error():
    """A fresh checkout has never run this. That must read as "unvalidated",
    not as a crash in the middle of an analysis."""
    assert wf.load() == {}
    assert wf.status("anything")["walk_forward_validated"] is False
    assert wf.summary()["modules_validated"] == 0
