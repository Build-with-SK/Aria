"""Fable teacher/reviewer — pure parsing, budget, lesson store/recall.
No network: the Fable call is mocked."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import src.desk.teacher as teacher


# ── parsing ──────────────────────────────────────────────────────────────

def test_parse_valid_json():
    txt = ('{"thesis_quality": 72, "direction_right": true, '
           '"misleading_agent": "sentiment", "key_lesson": "News spike faded fast", '
           '"do_differently": "Wait for confirmation"}')
    r = teacher.parse_review(txt)
    assert r["thesis_quality"] == 72 and r["direction_right"] is True
    assert r["misleading_agent"] == "sentiment"
    assert r["key_lesson"] == "News spike faded fast"


def test_parse_json_embedded_in_prose():
    txt = 'Sure! Here is my review:\n{"thesis_quality": 40, "key_lesson": "Stop was too tight"}\nHope that helps'
    r = teacher.parse_review(txt)
    assert r and r["key_lesson"] == "Stop was too tight"
    assert r["misleading_agent"] == "none"        # defaulted


def test_parse_clamps_and_rejects():
    assert teacher.parse_review('{"thesis_quality": 999, "key_lesson": "x"}')["thesis_quality"] == 100
    assert teacher.parse_review('{"thesis_quality": 50}') is None   # no lesson → reject
    assert teacher.parse_review("not json at all") is None
    # bad agent falls back to none
    assert teacher.parse_review(
        '{"misleading_agent": "astrology", "key_lesson": "y"}')["misleading_agent"] == "none"


def test_prompt_includes_outcome_and_verdict():
    record = {"ticker": "AAPL", "entry_side": "buy", "entry_price": 320,
              "exit_price": 312, "pnl_pct": -2.5, "r_multiple": -1.0,
              "reason": "stop hit", "thesis": "momentum breakout"}
    transcript = {"judge": {"verdict": "BUY", "conviction": 68, "key_risk": "overbought"},
                  "opinions": [{"agent": "technical", "view": "bull", "conviction": 70}]}
    p = teacher.build_review_prompt(record, transcript)
    assert "AAPL" in p and "-2.5%" in p and "stop hit" in p
    assert "BUY" in p and "STRICT JSON" in p


# ── store + recall ───────────────────────────────────────────────────────

def test_lesson_store_and_recall(tmp_path, monkeypatch):
    monkeypatch.setattr(teacher, "LESSONS_FILE", tmp_path / "lessons.jsonl")
    teacher._append_lesson({"ticker": "SAIL", "pnl_pct": 5.0,
                            "key_lesson": "Steel cyclical — trust the macro",
                            "do_differently": "size smaller"})
    teacher._append_lesson({"ticker": "AAPL", "pnl_pct": -3.0,
                            "key_lesson": "Ignored the earnings date"})
    out = teacher.recall_lessons("SAIL")
    assert "Steel cyclical" in out and "size smaller" in out
    assert "AAPL" not in out
    assert teacher.recall_lessons("NOPE") == ""


# ── review flow (Fable mocked) ───────────────────────────────────────────

def test_review_trade_writes_lesson(tmp_path, monkeypatch):
    monkeypatch.setattr(teacher, "LESSONS_FILE", tmp_path / "lessons.jsonl")
    monkeypatch.setattr(teacher, "_call_fable",
                        lambda prompt, model: '{"thesis_quality": 60, '
                        '"direction_right": false, "misleading_agent": "sentiment", '
                        '"key_lesson": "Sentiment overweighted a one-day pop"}')
    rec = {"ticker": "TSLA", "entry_side": "buy", "pnl_pct": -4.4,
           "debate_id": "dbt-1", "r_multiple": -1.2}
    lesson = teacher.review_trade(rec, {"judge": {}, "opinions": []},
                                  cfg={"teacher_model": "claude-fable-5"})
    assert lesson and lesson["misleading_agent"] == "sentiment"
    assert teacher.recall_lessons("TSLA")


def test_disabled_teacher_is_noop(monkeypatch):
    r = teacher.teach_from_closed_trades(cfg={"teacher_enabled": False})
    assert r["reviewed"] == 0


def test_budget_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(teacher, "STATE_FILE", tmp_path / "state.json")
    state = {"day": "", "count": 0}
    assert teacher._budget_left({"teacher_daily_cap": 5}, state) == 5
    state["count"] = 5
    assert teacher._budget_left({"teacher_daily_cap": 5}, state) <= 0
