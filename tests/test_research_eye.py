"""
tests/test_research_eye.py
==========================
The eye's promises, in order of how badly breaking them would hurt:

  1. It does not report the same thing twice. An eye without habituation is
     a firehose, and a firehose gets muted.
  2. Its first look at a watch reports NOTHING. Everything is new the first
     time you open your eyes; reporting that floods the reader on day one.
  3. One broken watch or source does not blind the whole cycle.
  4. It never proposes or executes anything.

No network.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.research.base import Document  # noqa: E402


@pytest.fixture
def eye(tmp_path, monkeypatch):
    from src.research import eye as module
    monkeypatch.setattr(module, "STATE_FILE", tmp_path / "eye_state.json")
    monkeypatch.setattr(module, "OBSERVATIONS", tmp_path / "observations.jsonl")
    return module


def _doc(url, title, source="googlenews", **meta):
    return Document(url=url, title=title, text="body", source=source,
                    backend="b", meta={"content_id": url, **meta})


def _sweep(docs, failed=None):
    from src.research.leads import Sweep
    return Sweep(query="q", leads=list(docs), ran={"googlenews": len(docs)},
                 failed=failed or {})


# ------------------------------------------------------- baseline & habituation

def test_first_look_reports_nothing_and_primes_baseline(eye, monkeypatch):
    monkeypatch.setattr(eye, "hunt", lambda *a, **k: _sweep([
        _doc("u1", "Nvidia beats"), _doc("u2", "Fed holds"),
    ]))
    eye.watch("NVDA", kind="ticker")

    first = eye.blink()
    assert first["observations"] == [], "first look must report nothing"
    assert first["primed"] == [{"watch": "ticker:nvda", "baseline": 2}]


def test_second_look_reports_only_what_is_new(eye, monkeypatch):
    seen = [_doc("u1", "Nvidia beats"), _doc("u2", "Fed holds")]
    monkeypatch.setattr(eye, "hunt", lambda *a, **k: _sweep(seen))
    eye.watch("NVDA", kind="ticker")
    eye.blink()                                   # prime

    seen.append(_doc("u3", "Nvidia cuts guidance"))
    second = eye.blink(force=True)

    titles = [o["title"] for o in second["observations"]]
    assert titles == ["Nvidia cuts guidance"]


def test_unchanged_world_produces_silence(eye, monkeypatch):
    docs = [_doc("u1", "Nvidia beats")]
    monkeypatch.setattr(eye, "hunt", lambda *a, **k: _sweep(docs))
    eye.watch("NVDA", kind="ticker")
    eye.blink()

    for _ in range(3):
        assert eye.blink(force=True)["observations"] == []
    assert eye.briefing() == "", "silence is a valid report"


def test_cadence_is_respected_unless_forced(eye, monkeypatch):
    monkeypatch.setattr(eye, "hunt", lambda *a, **k: _sweep([_doc("u1", "x")]))
    eye.watch("NVDA", kind="ticker", cadence_minutes=60)
    eye.blink()                                   # first look consumes the slot

    assert eye.blink()["watches_looked"] == 0, "not due yet"
    assert eye.blink(force=True)["watches_looked"] == 1


def test_rewatching_keeps_the_baseline(eye, monkeypatch):
    docs = [_doc("u1", "Nvidia beats")]
    monkeypatch.setattr(eye, "hunt", lambda *a, **k: _sweep(docs))
    eye.watch("NVDA", kind="ticker")
    eye.blink()

    eye.watch("NVDA", kind="ticker", cadence_minutes=15)     # re-added
    again = eye.blink(force=True)
    assert again["observations"] == [], "re-adding must not re-flood"
    assert again["primed"] == [], "it is not a first look any more"


# ------------------------------------------------------------------ salience

def test_corroborated_story_outranks_a_lone_post(eye, monkeypatch):
    monkeypatch.setattr(eye, "hunt", lambda *a, **k: _sweep([]))
    eye.watch("NVDA", kind="ticker")
    eye.blink()

    monkeypatch.setattr(eye, "hunt", lambda *a, **k: _sweep([
        _doc("a", "Nvidia cuts datacenter guidance sharply", "googlenews"),
        _doc("b", "Nvidia cuts datacenter guidance sharply", "reddit"),
        _doc("c", "random unrelated chatter about lunch", "stocktwits"),
    ]))
    obs = eye.blink(force=True)["observations"]
    assert "Nvidia" in obs[0]["title"]
    assert "independent sources" in obs[0]["why"]
    assert obs[0]["salience"] > obs[-1]["salience"]


def test_filing_outranks_chatter_at_equal_novelty(eye, monkeypatch):
    monkeypatch.setattr(eye, "hunt", lambda *a, **k: _sweep([]))
    eye.watch("NVDA", kind="ticker")
    eye.blink()

    monkeypatch.setattr(eye, "hunt", lambda *a, **k: _sweep([
        _doc("f", "8-K filed", "edgar", form="8-K"),
        _doc("s", "to the moon", "stocktwits"),
    ]))
    obs = eye.blink(force=True)["observations"]
    assert obs[0]["source"] == "edgar"
    assert "primary filing" in obs[0]["why"]


def test_observation_cap_records_what_was_suppressed(eye, monkeypatch):
    monkeypatch.setattr(eye, "hunt", lambda *a, **k: _sweep([]))
    eye.watch("NVDA", kind="ticker")
    eye.blink()

    monkeypatch.setattr(eye, "hunt", lambda *a, **k: _sweep(
        [_doc(f"u{i}", f"story {i}") for i in range(30)]
    ))
    result = eye.blink(force=True, max_observations=5)
    assert len(result["observations"]) == 5
    assert result["suppressed"] == 25


# ------------------------------------------------------------- resilience

def test_one_failing_watch_does_not_blind_the_others(eye, monkeypatch):
    def hunt(query, **kwargs):
        if query == "BROKEN":
            raise RuntimeError("source exploded")
        return _sweep([_doc("u1", "fine")])

    monkeypatch.setattr(eye, "hunt", hunt)
    eye.watch("BROKEN", kind="ticker")
    eye.watch("OK", kind="ticker")

    result = eye.blink()
    assert "ticker:broken" in result["failed"]
    assert result["watches_looked"] == 1


def test_partial_sweep_failures_are_reported_per_watch(eye, monkeypatch):
    monkeypatch.setattr(eye, "hunt",
                        lambda *a, **k: _sweep([_doc("u1", "x")],
                                               failed={"reddit": "HTTP 429"}))
    eye.watch("NVDA", kind="ticker")
    result = eye.blink()
    assert result["failed"]["ticker:nvda/reddit"] == "HTTP 429"


def test_corrupt_state_does_not_permanently_blind_the_eye(eye):
    eye.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    eye.STATE_FILE.write_text("{not json", encoding="utf-8")
    assert eye.watches() == []
    eye.watch("NVDA", kind="ticker")
    assert len(eye.watches()) == 1


# --------------------------------------------------------------- reporting

def test_briefing_is_text_for_the_brain_and_cites_urls(eye, monkeypatch):
    monkeypatch.setattr(eye, "hunt", lambda *a, **k: _sweep([]))
    eye.watch("NVDA", kind="ticker")
    eye.blink()

    monkeypatch.setattr(eye, "hunt", lambda *a, **k: _sweep([
        _doc("https://ex.com/a", "Nvidia cuts guidance", "googlenews"),
    ]))
    eye.blink(force=True)

    text = eye.briefing()
    assert "Nvidia cuts guidance" in text
    assert "https://ex.com/a" in text
    assert text.startswith("What I noticed on the internet")


def test_observations_persist_as_jsonl_with_citations(eye, monkeypatch):
    monkeypatch.setattr(eye, "hunt", lambda *a, **k: _sweep([]))
    eye.watch("NVDA", kind="ticker")
    eye.blink()
    monkeypatch.setattr(eye, "hunt", lambda *a, **k: _sweep([
        _doc("https://ex.com/a", "Nvidia cuts guidance"),
    ]))
    eye.blink(force=True)

    rows = [json.loads(line) for line in
            eye.OBSERVATIONS.read_text(encoding="utf-8").strip().splitlines()]
    assert rows[0]["url"] == "https://ex.com/a"
    assert rows[0]["watch_id"] == "ticker:nvda"

    obs = eye.Observation(**rows[0])
    assert "retrieved" in obs.cite()


# -------------------------------------------------------------- the fence

def test_the_eye_cannot_trade():
    """The brain proposes and the human approves. The eye only observes.

    Checked against the parsed syntax tree rather than the file text, so that
    naming the rule in a comment does not fail the test that enforces it.
    """
    import ast

    tree = ast.parse(
        (Path(__file__).parent.parent / "src" / "research" / "eye.py")
        .read_text(encoding="utf-8")
    )
    referenced = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } | {
        alias.name.split(".")[-1]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    forbidden = {"approve_and_execute", "OrderManager", "submit_order",
                 "place_order", "AlpacaBroker", "IBKRBroker"}
    assert not (referenced & forbidden), (
        f"the eye must not reach execution: {sorted(referenced & forbidden)}"
    )
