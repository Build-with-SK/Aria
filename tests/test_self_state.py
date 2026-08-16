"""
tests/test_self_state.py
========================
Her sense of her own state (docs/ARIA_NEXT_SESSION.md step 7, last bullet).

The thing under test is the DEGRADATION direction. Every check here has to
fail to "unknown" or "down", never to "ok" — a health report that says fine
when it could not run converts an outage into a false assurance, which is the
precise failure it exists to catch.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.brain import self_state as S


# ── the report shape ─────────────────────────────────────────────────────────

def test_one_broken_sense_makes_the_whole_report_down(monkeypatch):
    monkeypatch.setattr(S, "SENSES", (
        lambda: S.Sense("brain", S.DOWN, "unreachable"),
        lambda: S.Sense("data", S.OK, "fresh"),
    ))
    r = S.report()
    assert r["state"] == S.DOWN
    assert r["senses"]["brain"]["state"] == S.DOWN


def test_degraded_beats_ok_but_down_beats_degraded(monkeypatch):
    monkeypatch.setattr(S, "SENSES", (
        lambda: S.Sense("a", S.OK), lambda: S.Sense("b", S.DEGRADED)))
    assert S.report()["state"] == S.DEGRADED
    monkeypatch.setattr(S, "SENSES", (
        lambda: S.Sense("a", S.DOWN), lambda: S.Sense("b", S.DEGRADED)))
    assert S.report()["state"] == S.DOWN


def test_unknown_is_not_ok(monkeypatch):
    """A check that could not run must not read as a passing check."""
    monkeypatch.setattr(S, "SENSES", (
        lambda: S.Sense("a", S.OK), lambda: S.Sense("b", S.UNKNOWN)))
    assert S.report()["state"] == S.UNKNOWN


def test_a_sense_that_crashes_becomes_unknown_not_missing(monkeypatch):
    def exploding():
        raise RuntimeError("chromadb went away")

    exploding.__name__ = "sense_brain"
    monkeypatch.setattr(S, "SENSES", (exploding,))
    r = S.report()
    assert r["senses"]["brain"]["state"] == S.UNKNOWN
    assert "this check itself failed" in r["senses"]["brain"]["detail"]


# ── what she says, and when she says nothing ────────────────────────────────

def test_she_says_nothing_when_everything_works():
    """An assistant that reports its own health every turn has taught the
    owner to skip the line by the third time."""
    assert S.speak([S.Sense("brain", S.OK, "fine"), S.Sense("data", S.OK, "fine")]) == ""


def test_she_leads_with_it_when_something_is_down():
    line = S.speak([S.Sense("brain", S.DOWN, "my reasoning model is unreachable"),
                    S.Sense("data", S.OK, "fine")])
    assert line.startswith("Before I answer")
    assert "unreachable" in line


def test_a_merely_degraded_state_is_mentioned_not_alarming():
    line = S.speak([S.Sense("data", S.DEGRADED, "my market state is 90 hours old")])
    assert line.startswith("Worth saying")
    assert "90 hours old" in line


def test_unknown_alone_is_not_announced():
    """'I could not check' is worth reporting in the payload, not worth
    interrupting an answer with."""
    assert S.speak([S.Sense("record", S.UNKNOWN, "could not read the log")]) == ""


# ── the individual senses ────────────────────────────────────────────────────

def test_an_unreachable_brain_reads_down_and_names_the_host(monkeypatch):
    def refuse(*a, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(S.urllib.request, "urlopen", refuse)
    s = S.sense_brain()
    assert s.state == S.DOWN
    assert "unreachable" in s.detail
    assert "abstain" in s.detail          # she says what she will do about it


def test_a_brain_routed_to_a_model_that_is_not_installed_is_degraded(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"models": [{"name": "gemma3:4b"}]}).encode()

    monkeypatch.setattr(S.urllib.request, "urlopen", lambda *a, **kw: FakeResponse())
    monkeypatch.setattr("src.inference.router.load_tier_config",
                        lambda: {"DEEP": [["ollama", "a-model-nobody-pulled"]]})
    s = S.sense_brain()
    assert s.state == S.DEGRADED
    assert "a-model-nobody-pulled" in s.detail


def test_stale_market_state_is_degraded_and_quantified(tmp_path, monkeypatch):
    import os
    import time
    monkeypatch.setattr(S, "DATA", tmp_path)
    path = tmp_path / "signals.json"
    path.write_text(json.dumps({"AAPL": {}, "NVDA": {}}), encoding="utf-8")
    old = time.time() - (S.SIGNALS_STALE_HOURS + 20) * 3600
    os.utime(path, (old, old))

    s = S.sense_data()
    assert s.state == S.DEGRADED
    assert "hours old" in s.detail
    assert s.facts["signals_age_hours"] > S.SIGNALS_STALE_HOURS


def test_fresh_market_state_is_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "DATA", tmp_path)
    (tmp_path / "signals.json").write_text(json.dumps({"AAPL": {}}), encoding="utf-8")
    s = S.sense_data()
    assert s.state == S.OK and s.facts["tickers"] == 1


def test_a_missing_signals_file_is_unknown_not_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "DATA", tmp_path)
    assert S.sense_data().state == S.UNKNOWN


def test_an_unreadable_signals_file_is_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "DATA", tmp_path)
    (tmp_path / "signals.json").write_text("{not json", encoding="utf-8")
    assert S.sense_data().state == S.DEGRADED


def test_a_loop_that_has_never_run_is_down(monkeypatch):
    monkeypatch.setattr("src.v5.loop.health", lambda: {
        "healthy": False, "predict": {"last_run": None, "ok": False},
        "resolve": {"last_run": None, "ok": False}})
    s = S.sense_loop()
    assert s.state == S.DOWN
    assert "no track record can accumulate" in s.detail


def test_a_stalled_leg_says_she_has_stopped_learning(monkeypatch):
    """The failure mode that has already happened here: everything answers
    normally and nothing accumulates."""
    monkeypatch.setattr("src.v5.loop.health", lambda: {
        "healthy": False,
        "predict": {"last_run": "2026-08-01T21:10:00", "hours_ago": 240, "ok": False},
        "resolve": {"last_run": "2026-08-11T18:00:00", "hours_ago": 2, "ok": True}})
    s = S.sense_loop()
    assert s.state == S.DEGRADED
    assert "predict" in s.detail
    assert "stopped learning" in s.detail


def test_a_turning_loop_is_ok(monkeypatch):
    monkeypatch.setattr("src.v5.loop.health", lambda: {
        "healthy": True,
        "predict": {"last_run": "2026-08-11T21:10:00", "hours_ago": 3, "ok": True},
        "resolve": {"last_run": "2026-08-11T23:00:00", "hours_ago": 1, "ok": True}})
    assert S.sense_loop().state == S.OK


def test_zero_resolved_calls_is_reported_as_a_fact_not_a_fault(monkeypatch):
    """Invariant 5: zero is the honest number. It is not an error state, and
    it is not something to paper over either."""
    monkeypatch.setattr("src.v5.learning.load_predictions",
                        lambda: [{"resolved": False}, {"resolved": False}])
    s = S.sense_record()
    assert s.state == S.OK
    assert "none resolved yet" in s.detail
    assert "will not imply one" in s.detail
    assert s.facts == {"logged": 2, "resolved": 0}


# ── what she is allowed to say about herself ────────────────────────────────

def test_the_prompt_block_states_the_resolved_count(monkeypatch):
    """Asked whether she was learning, she said "Yes, I am continuously
    learning and updating my models" — with zero resolved predictions and no
    fine-tune ever run. Facts in the prompt are what change that answer."""
    monkeypatch.setattr("src.v5.learning.load_predictions",
                        lambda: [{"resolved": False}] * 46)
    S._cache["report"] = None
    text = S.for_prompt()
    assert "46 predictions logged, 0 resolved" in text
    assert "NO measured track record" in text


def test_the_prompt_block_denies_the_specific_false_claim(monkeypatch):
    S._cache["report"] = None
    text = S.for_prompt()
    assert "NO fine-tune has ever run" in text
    assert "your model is not updating" in text.lower()


def test_it_does_not_flatten_learning_into_a_flat_no(monkeypatch):
    """Given only the denial, she over-corrected to "I do not have any
    mechanism to improve over time" — false in the other direction, since
    those mechanisms exist and are merely empty. Both errors are the same
    error: a claim that outruns the evidence."""
    S._cache["report"] = None
    text = S.for_prompt()
    assert "MECHANISMS exist and run" in text
    assert "as wrong as saying you are already improving" in text
    assert "long-term memory" in text


def test_a_served_adapter_is_reported_as_such(monkeypatch):
    monkeypatch.setattr("src.training.adapters.status", lambda: {
        "serving": "2026-09-a", "serving_is_base_model": False,
        "base_model": "qwen", "registered": ["2026-09-a"]})
    assert S.learning_state()["weights"]["fine_tuned"] is True
    S._cache["report"] = None
    assert "2026-09-a" in S.for_prompt()


def test_degraded_senses_reach_the_prompt(monkeypatch):
    monkeypatch.setattr(S, "SENSES", (
        lambda: S.Sense("data", S.DEGRADED, "my market state is 105 hours old"),
        lambda: S.Sense("loop", S.OK, "turning"),
    ))
    S._cache["report"] = None
    text = S.for_prompt()
    assert "105 hours old" in text


def test_the_prompt_block_is_cached(monkeypatch):
    """A chat turn must not wait on a health sweep that touches the network
    and the disk."""
    calls = []
    monkeypatch.setattr(S, "SENSES", (
        lambda: calls.append(1) or S.Sense("brain", S.OK, "fine"),))
    S._cache["report"] = None
    S.report_cached()
    S.report_cached()
    S.report_cached()
    assert len(calls) == 1


def test_a_stale_cache_is_refreshed(monkeypatch):
    calls = []
    monkeypatch.setattr(S, "SENSES", (
        lambda: calls.append(1) or S.Sense("brain", S.OK, "fine"),))
    S._cache["report"] = None
    S.report_cached()
    S.report_cached(max_age_s=0)
    assert len(calls) == 2


def test_the_chat_prompt_actually_includes_it():
    """The block is worth nothing sitting in a module nobody calls, and it must
    be in the SYSTEM prompt — a droppable context block would be evicted by a
    long conversation, which is exactly when she starts improvising."""
    source = (Path(__file__).parent.parent / "backend" / "main.py").read_text(
        encoding="utf-8", errors="replace")
    assert "self_state import for_prompt" in source
    assert "{_identity}{_truth}" in source


def test_an_unreadable_prediction_log_is_unknown(monkeypatch):
    def boom():
        raise OSError("disk went away")

    monkeypatch.setattr("src.v5.learning.load_predictions", boom)
    assert S.sense_record().state == S.UNKNOWN
