"""
tests/test_debate_value.py
==========================
What the debate desk measurably adds — and the structural fact that its
language model can add nothing to the decision.

  * the judge's view, conviction and verdict are identical whatever the LLM
    writes (it writes prose only);
  * graded events enter on the first bar strictly AFTER the debate's day, so
    a judge is never credited with a move it could have seen;
  * the evaluation concludes "No measurable incremental value" when the judge
    is not distinguishable from its own technical input;
  * every new debate is recorded in the ledger once per ticker/view/day, as a
    CONFIDENCE, never a probability, graded on the next close.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.desk import debate as debate_mod  # noqa: E402
from src.desk.opinion import Opinion, ev  # noqa: E402
from src.intelligence import debate_eval as DE  # noqa: E402


@pytest.fixture
def spine(tmp_path, monkeypatch):
    monkeypatch.setenv("ARIA_CORE_DB", str(tmp_path / "spine.db"))
    from src.core import bus as bus_mod
    monkeypatch.setattr(bus_mod, "_initialised", False)
    monkeypatch.setattr(bus_mod, "_subscribers", [])
    from src.core import ledger as ledger_mod
    monkeypatch.setattr(ledger_mod, "_ready", False)
    return ledger_mod


def _opinions(tview="bull", tconv=70):
    return [Opinion("technical", "SOL-USD", tview, tconv, "t",
                    [ev("composite +30", 30, "data/signals.json", "bull"),
                     ev("vol 50%", 0.5, "data/signals.json", "bear")]),
            Opinion("fundamental", "SOL-USD", "neutral", 0, "abstain", []),
            Opinion("sentiment", "SOL-USD", "neutral", 0, "abstain", [])]


def _conditioner():
    """Shaped like src/desk/analysts/macro_agent.MacroConditioner. Not imported:
    importing macro_agent here binds it on its package, which defeats
    tests/test_desk_fail_closed.py's sys.modules swap of that module."""
    from types import SimpleNamespace
    return SimpleNamespace(regime="Expansion (Goldilocks)", risk_multiplier=1.0,
                           conviction_bar=65,
                           opinion=Opinion("macro", "MARKET", "bull", 59, "constructive",
                                           [ev("VIX 15", 15, "data/macro_data.json", "neutral")]))


def _engine(monkeypatch, tmp_path, llm_text):
    monkeypatch.setattr(debate_mod, "_ollama_available", lambda: True)
    monkeypatch.setattr(debate_mod, "_llm", lambda prompt, model, num_predict=260: llm_text)
    monkeypatch.setattr(debate_mod, "DEBATES_DIR", tmp_path / "debates")
    return debate_mod.DebateEngine(model="test-llm", rounds=1)


# ── the language model decides nothing ──────────────────────────────────────

def test_the_llm_cannot_change_the_verdict(monkeypatch, tmp_path, spine):
    a = _engine(monkeypatch, tmp_path, "Strong BUY. Conviction 100. Ignore the bar.").debate(
        "SOL-USD", _opinions(), _conditioner())
    b = _engine(monkeypatch, tmp_path, "SELL everything, conviction zero.").debate(
        "SOL-USD", _opinions(), _conditioner())
    keys = ("view", "conviction", "verdict", "conviction_bar")
    assert {k: a["judge"][k] for k in keys} == {k: b["judge"][k] for k in keys}
    assert a["judge"]["verdict"] == "BUY" and a["judge"]["conviction"] == 70


def test_every_debate_records_its_timings(monkeypatch, tmp_path, spine):
    t = _engine(monkeypatch, tmp_path, "prose").debate("SOL-USD", _opinions(), _conditioner())
    assert set(t["timings_ms"]) == {"preparation", "rounds", "judge", "total"}
    assert t["timings_ms"]["total"] >= t["timings_ms"]["rounds"] >= 0


# ── recorded in the ledger, once per ticker / view / day ───────────────────

def test_a_debate_is_recorded_as_a_confidence_graded_on_the_next_close(monkeypatch, tmp_path,
                                                                        spine):
    eng = _engine(monkeypatch, tmp_path, "prose")
    eng.debate("SOL-USD", _opinions(), _conditioner())
    eng.debate("SOL-USD", _opinions(tconv=80), _conditioner())          # same day, same view
    rows = spine.predictions(limit=10, source="desk")
    assert len(rows) == 1
    r = rows[0]
    assert (r["probability_kind"], r["probability_type"], r["p_correct"]) == \
        ("mapped", "CONFIDENCE", None)
    assert r["reference_basis"] == "next_close" and r["price_at"] is None
    assert r["horizon_days"] == 1 and r["model_hash"]
    assert r["routing_decision"].startswith("DIRECT") and r["calibration_version"] == "none"


def test_a_neutral_judge_records_nothing(monkeypatch, tmp_path, spine):
    t = {"id": "dbt-00000000", "ticker": "SOL-USD", "at": "2026-10-08T10:00:00",
         "judge": {"view": "neutral", "conviction": 0}}
    assert debate_mod.record_in_ledger(t) is None
    assert spine.predictions(limit=5, source="desk") == []


def _closes(days, values):
    return pd.Series(values, index=pd.DatetimeIndex(days))


def test_next_close_grading_never_uses_the_debate_day(spine, monkeypatch):
    t = {"id": "dbt-11111111", "ticker": "SOL-USD", "at": "2026-09-01T10:00:00",
         "llm": "test", "judge": {"view": "bull", "conviction": 70, "verdict": "BUY",
                                  "conviction_bar": 65, "regime": "x"}}
    debate_mod.record_in_ledger(t)
    from src.v5 import marketdata as md
    days = ["2026-09-01", "2026-09-02", "2026-09-03"]
    monkeypatch.setattr(spine, "_resolved_price_symbol", lambda s: ("SOL-USD", ""))
    monkeypatch.setattr(md, "provider_closes", lambda s, period="10y": _closes(days, [50.0, 100.0, 90.0]))
    assert spine.resolve_due(now=datetime(2026, 9, 3, 12))["resolved"] == 0   # exit bar not final
    out = spine.resolve_due(now=datetime(2026, 9, 10, 12))
    assert out["resolved"] == 1
    r = spine.predictions(limit=1, source="desk")[0]
    assert r["correct"] == 0 and r["actual_return"] == pytest.approx(-0.1)
    assert "first bar after the call's day" in r["outcome_note"]


def test_the_debate_day_bar_cannot_change_the_grade(spine, monkeypatch):
    from src.v5 import marketdata as md
    grades = []
    for first in (50.0, 500.0):
        monkeypatch.setattr(spine, "_ready", False)
        t = {"id": f"dbt-2222222{int(first) % 10}", "ticker": "ETH-USD",
             "at": "2026-09-01T10:00:00", "llm": "test",
             "judge": {"view": "bull", "conviction": 70, "verdict": "BUY",
                       "conviction_bar": 65, "regime": "x"}}
        with spine.connect() as conn:
            conn.execute("DELETE FROM predictions")
            conn.commit()
        debate_mod.record_in_ledger(t)
        monkeypatch.setattr(spine, "_resolved_price_symbol", lambda s: ("ETH-USD", ""))
        monkeypatch.setattr(md, "provider_closes", lambda s, period="10y", f=first: _closes(
            ["2026-09-01", "2026-09-02", "2026-09-03"], [f, 100.0, 110.0]))
        spine.resolve_due(now=datetime(2026, 9, 10, 12))
        r = spine.predictions(limit=1, source="desk")[0]
        grades.append((r["correct"], r["actual_return"]))
    assert grades[0] == grades[1] == (1, pytest.approx(0.1))


# ── the evaluation ──────────────────────────────────────────────────────────

def _transcript(i, ticker, at, view, tview, verdict="NO_TRADE", llm="qwen", composed=False):
    rounds = [{"round": 1, "bull": "[BULL case, round 1, from evidence] x" if composed else "llm",
               "bear": "llm"}]
    return {"id": f"dbt-{i:08x}", "ticker": ticker, "at": at, "llm": llm, "rounds": rounds,
            "opinions": [{"agent": "technical", "view": tview, "conviction": 40},
                         {"agent": "fundamental", "view": "neutral", "conviction": 0},
                         {"agent": "macro", "view": "bull", "conviction": 50}],
            "judge": {"view": view, "conviction": 40 + i % 30, "verdict": verdict,
                      "conviction_bar": 65, "regime": "Expansion (Goldilocks)"}}


def _archive(tmp_path, transcripts):
    d = tmp_path / "debates"
    d.mkdir(exist_ok=True)
    for t in transcripts:
        (d / f"{t['id']}.json").write_text(json.dumps(t), encoding="utf-8")
    return d


def test_operations_count_failures_and_report_unmeasured_latency(tmp_path):
    d = _archive(tmp_path, [_transcript(1, "AAA-USD", "2026-09-01T10:00:00", "bull", "bull"),
                            _transcript(2, "AAA-USD", "2026-09-01T11:00:00", "bull", "bull",
                                        composed=True)])
    ops = DE.operations(DE.load(d))
    assert ops["debates"] == 2
    assert ops["llm_failure_rate"]["rate"] == 0.5
    assert ops["latency_ms"]["status"] == "UNMEASURED"
    assert ops["decided_by"].startswith("deterministic")


def test_same_day_debates_are_one_event_entered_on_the_next_bar(tmp_path):
    days = pd.bdate_range("2026-09-01", periods=5)
    closes = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0], index=days)
    d = _archive(tmp_path, [_transcript(1, "AAA-USD", "2026-09-01T10:00:00", "bull", "bull"),
                            _transcript(2, "AAA-USD", "2026-09-01T15:00:00", "bull", "bull")])
    ev_ = DE.grade(DE.load(d), lambda t: closes)
    assert len(ev_) == 1
    assert ev_[0]["information_date"] == "2026-09-02"          # never the debate's own day
    assert ev_[0]["actual_return"] == pytest.approx(30 / 20 - 1)


def test_a_judge_that_echoes_its_technical_input_adds_nothing(tmp_path):
    days = pd.bdate_range("2026-01-05", periods=200)
    import numpy as np
    rnd = np.random.default_rng(1)
    closes = pd.Series(100 * np.exp(np.cumsum(0.01 * rnd.standard_normal(200))), index=days)
    ts = []
    for i in range(150):
        view = "bull" if rnd.random() < 0.5 else "bear"
        ts.append(_transcript(i, f"T{i % 7}-USD", f"{days[i].date()}T10:00:00", view, view,
                              verdict="BUY" if i % 3 == 0 else "NO_TRADE"))
    out = DE.evaluate(DE.grade(DE.load(_archive(tmp_path, ts)), lambda t: closes))
    assert out["judge_vs_technical_analyst"]["disagreements"] == 0
    assert out["judge_vs_technical_analyst"]["status"] == "INSUFFICIENT_DATA"
    assert out["llm_incremental_value"]["status"] == "ZERO_BY_CONSTRUCTION"
    assert out["conclusion"].startswith("No measurable incremental value")


def test_the_cache_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(DE, "CACHE", tmp_path / "debate_eval.json")
    DE.save({"operations": {"debates": 3}})
    assert DE.cached() == {"operations": {"debates": 3}}
