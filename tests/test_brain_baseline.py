"""
tests/test_brain_baseline.py
============================
The baseline harness (docs/ARIA_NEXT_SESSION.md step 3).

The comparison logic is what these tests are really about. A baseline that
reports "OK" when a deterministic number moved is worse than no baseline: it
signs off on a swap that changed something it was not supposed to be able to
change. So the equality checks are tested harder than the latency ones.

No network, no LLM, no market data — samples are constructed by hand.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.inference import baseline as B


def make(modules=None, debates=None, structured=None, latency=None,
         at="2026-08-11T10:00:00", model="qwen2.5-coder:7b") -> dict:
    return {
        "schema": "aria.brain_baseline/1",
        "at": at,
        "brain": {"desk_llm_model": model, "hunt_interval_minutes": 30},
        "tickers": ["AAPL", "NVDA"],
        "modules": modules if modules is not None else [
            {"ticker": "AAPL", "ok": True, "total": 41, "reporting": 33,
             "abstained": 8, "abstained_modules": ["credit", "breadth"]},
            {"ticker": "NVDA", "ok": True, "total": 41, "reporting": 35,
             "abstained": 6, "abstained_modules": ["credit"]},
        ],
        "debates": debates if debates is not None else [
            {"ticker": "AAPL", "ok": True, "verdict": "PASS", "conviction": 58},
            {"ticker": "NVDA", "ok": True, "verdict": "LONG", "conviction": 71},
        ],
        "structured": structured if structured is not None else [
            {"probe": "teacher_json", "attempts": 6, "parsed": 6, "failures": 0,
             "unreachable": 0, "failure_rate": 0.0},
        ],
        "latency": latency if latency is not None else {
            "samples": 2, "p50_ms": 4000, "p95_ms": 5000, "max_ms": 5000,
            "tick_minutes": 30, "projected_cycle_ms": 10000,
            "fits_the_tick": True},
    }


# ── the equality checks: the whole reason baselines are captured first ───────

def test_an_unchanged_brain_compares_clean():
    assert B.compare(make(), make())["verdict"] == "OK"


def test_a_changed_abstention_count_is_miswiring_not_regression():
    """The 41 modules never call an LLM. If this number moves after a brain
    swap, the swap did something else as well."""
    after = make(modules=[
        {"ticker": "AAPL", "ok": True, "total": 41, "reporting": 34,
         "abstained": 7, "abstained_modules": ["credit"]},
        {"ticker": "NVDA", "ok": True, "total": 41, "reporting": 35,
         "abstained": 6, "abstained_modules": ["credit"]},
    ])
    result = B.compare(make(), after)
    assert result["verdict"] == "MISWIRED"
    assert any("do not call an LLM" in m for m in result["miswired"])


def test_the_same_count_of_different_modules_is_still_miswiring():
    """8 abstaining before and 8 after is not 'unchanged' if they are not the
    same 8 — a count-only check would wave this through."""
    after = make(modules=[
        {"ticker": "AAPL", "ok": True, "total": 41, "reporting": 33,
         "abstained": 8, "abstained_modules": ["credit", "value"]},
        {"ticker": "NVDA", "ok": True, "total": 41, "reporting": 35,
         "abstained": 6, "abstained_modules": ["credit"]},
    ])
    result = B.compare(make(), after)
    assert result["verdict"] == "MISWIRED"
    assert any("not the same modules" in m for m in result["miswired"])


def test_a_stale_feed_is_not_reported_as_miswiring():
    """Modules abstain when their inputs are missing. A baseline captured
    during a yfinance rate-limit, compared against a clean one, must not
    accuse the wiring of something the feed did."""
    before = make(modules=[
        {"ticker": "AAPL", "ok": True, "total": 41, "reporting": 33,
         "abstained": 8, "abstained_modules": ["credit"], "stale": False,
         "vendor": "yfinance"},
    ])
    after = make(modules=[
        {"ticker": "AAPL", "ok": True, "total": 41, "reporting": 20,
         "abstained": 21, "abstained_modules": ["credit", "value"],
         "stale": True, "vendor": "cache"},
    ])
    result = B.compare(before, after)
    assert result["verdict"] != "MISWIRED"
    assert any("Not a clean comparison" in n for n in result["notes"])


def test_the_same_feed_still_makes_abstention_drift_miswiring():
    before = make(modules=[
        {"ticker": "AAPL", "ok": True, "total": 41, "reporting": 33,
         "abstained": 8, "abstained_modules": ["credit"], "stale": False,
         "vendor": "yfinance"},
    ])
    after = make(modules=[
        {"ticker": "AAPL", "ok": True, "total": 41, "reporting": 32,
         "abstained": 9, "abstained_modules": ["credit", "value"],
         "stale": False, "vendor": "yfinance"},
    ])
    assert B.compare(before, after)["verdict"] == "MISWIRED"


def test_a_changed_conviction_means_the_llm_reached_the_verdict():
    """The judge's numbers are computed from the evidence. The LLM writes
    prose and never decides conviction — so if conviction moved, it does."""
    after = make(debates=[
        {"ticker": "AAPL", "ok": True, "verdict": "PASS", "conviction": 58},
        {"ticker": "NVDA", "ok": True, "verdict": "LONG", "conviction": 74},
    ])
    result = B.compare(make(), after)
    assert result["verdict"] == "MISWIRED"
    assert any("deterministic" in m for m in result["miswired"])


def test_a_changed_verdict_is_miswiring():
    after = make(debates=[
        {"ticker": "AAPL", "ok": True, "verdict": "LONG", "conviction": 58},
        {"ticker": "NVDA", "ok": True, "verdict": "LONG", "conviction": 71},
    ])
    assert B.compare(make(), after)["verdict"] == "MISWIRED"


def test_prose_changing_is_not_a_problem():
    """A new brain is supposed to argue differently. Only the numbers are
    pinned."""
    before = make(debates=[{"ticker": "AAPL", "ok": True, "verdict": "PASS",
                            "conviction": 58, "llm": "qwen2.5-coder:7b"}])
    after = make(debates=[{"ticker": "AAPL", "ok": True, "verdict": "PASS",
                           "conviction": 58, "llm": "qwen2.5-7b-instruct"}])
    assert B.compare(before, after)["verdict"] == "OK"


# ── the legitimate comparisons ───────────────────────────────────────────────

def test_a_much_slower_brain_is_a_regression_not_miswiring():
    after = make(latency={"samples": 2, "p50_ms": 30000, "p95_ms": 40000,
                          "max_ms": 40000, "tick_minutes": 30,
                          "projected_cycle_ms": 80000, "fits_the_tick": True})
    result = B.compare(make(), after)
    assert result["verdict"] == "REGRESSED"
    assert not result["miswired"]


def test_falling_out_of_the_tick_budget_is_called_out():
    after = make(latency={"samples": 2, "p50_ms": 900000, "p95_ms": 1000000,
                          "max_ms": 1000000, "tick_minutes": 30,
                          "projected_cycle_ms": 2000000, "fits_the_tick": False})
    result = B.compare(make(), after)
    assert any("no longer fits" in r for r in result["regressed"])


def test_worse_json_compliance_is_a_regression():
    after = make(structured=[{"probe": "teacher_json", "attempts": 6,
                              "parsed": 2, "failures": 4, "unreachable": 0,
                              "failure_rate": 0.667}])
    result = B.compare(make(), after)
    assert result["verdict"] == "REGRESSED"
    assert any("teacher_json" in r for r in result["regressed"])


def test_an_unreachable_brain_has_no_failure_rate():
    """0 of 0 parsed is not 100% compliance and not 0% — reporting either
    would let an outage read as a result."""
    s = B.StructuredSample(probe="teacher_json", attempts=4, unreachable=4)
    assert s.failure_rate is None

    after = make(structured=[{"probe": "teacher_json", "attempts": 6,
                              "parsed": 0, "failures": 0, "unreachable": 6,
                              "failure_rate": None}])
    result = B.compare(make(), after)
    assert result["verdict"] == "OK"          # not a regression — no data
    assert any("unreachable" in n for n in result["notes"])


def test_failure_rate_counts_only_replies_that_arrived():
    s = B.StructuredSample(probe="p", attempts=10, parsed=6, failures=2,
                           unreachable=2)
    assert s.failure_rate == 0.25             # 2 of the 8 that answered


# ── the report ───────────────────────────────────────────────────────────────

def test_the_summary_leads_with_miswiring():
    after = make(debates=[{"ticker": "AAPL", "ok": True, "verdict": "LONG",
                           "conviction": 99}])
    text = B.format_comparison(B.compare(make(), after))
    assert text.startswith("MISWIRED")
    assert "a number that cannot move, moved" in text


def test_a_missing_ticker_is_a_note_not_a_silent_pass():
    after = make(modules=[{"ticker": "AAPL", "ok": True, "total": 41,
                           "reporting": 33, "abstained": 8,
                           "abstained_modules": ["credit", "breadth"]}],
                 debates=[{"ticker": "AAPL", "ok": True, "verdict": "PASS",
                           "conviction": 58}])
    result = B.compare(make(), after)
    assert any("NVDA" in n for n in result["notes"])


# ── the pieces that run without a brain ──────────────────────────────────────

def test_latency_block_projects_a_whole_cycle_not_one_call():
    debates = [B.DebateSample(ticker=t, ok=True, elapsed_ms=ms)
               for t, ms in (("A", 1000), ("B", 2000), ("C", 3000),
                             ("D", 4000), ("E", 5000))]
    lat = B.latency_block(debates, tick_minutes=30)
    assert lat["samples"] == 5
    assert lat["p50_ms"] == 3000
    assert lat["projected_cycle_ms"] == lat["p95_ms"] * 5
    assert lat["fits_the_tick"] is True


def test_latency_with_nothing_to_measure_says_so():
    lat = B.latency_block([B.DebateSample(ticker="A", ok=False)])
    assert lat["samples"] == 0
    assert "no latency to report" in lat["note"]


def test_structured_capture_counts_unreachable_separately():
    """An exception from the router is an outage, not a format failure."""
    calls = {"n": 0}

    def flaky(prompt, max_tokens):
        calls["n"] += 1
        if calls["n"] % 2:
            raise RuntimeError("all candidates failed for tier STANDARD")
        return '{"thesis_quality": 40, "direction_right": false, ' \
               '"misleading_agent": "technical", "key_lesson": "size smaller", ' \
               '"do_differently": "wait for the retest"}'

    samples = B.capture_structured(attempts=4, complete=flaky)
    json_probe = [s for s in samples if s.probe == "teacher_json"][0]
    assert json_probe.unreachable == 2
    assert json_probe.parsed == 2
    assert json_probe.failures == 0


def test_structured_capture_catches_prose_where_json_was_demanded():
    def chatty(prompt, max_tokens):
        return "Sure! Here's my assessment: the trade was reasonable but early."

    samples = B.capture_structured(attempts=3, complete=chatty)
    json_probe = [s for s in samples if s.probe == "teacher_json"][0]
    token_probe = [s for s in samples if s.probe == "reflex_token"][0]
    assert json_probe.failures == 3 and json_probe.failure_rate == 1.0
    assert token_probe.failures == 3
    assert json_probe.examples          # the actual reply, for diagnosis


def test_the_fixed_five_are_fixed():
    """A baseline compared against a different universe measures the
    universe."""
    assert B.FIXED_TICKERS == ("AAPL", "NVDA", "JPM", "XOM", "SPY")


def test_the_harness_calls_functions_that_exist():
    """The first capture run recorded five identical AttributeErrors and saved
    them as a baseline: `pipeline.analyse` does not exist, `analyze` does. A
    harness whose failure mode is a plausible-looking file of errors needs the
    entry points pinned."""
    from src.desk import teacher
    from src.v5 import pipeline
    assert callable(getattr(pipeline, "analyze", None))
    assert callable(getattr(teacher, "parse_review", None))


def test_save_and_load_round_trip(tmp_path):
    payload = make()
    path = B.save(payload, directory=tmp_path)
    assert B.load(path)["at"] == payload["at"]
    assert B.latest(tmp_path) == path
