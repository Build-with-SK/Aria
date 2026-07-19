"""Scorecard calibration tests — bounded weight tilts, agent scoring."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import src.desk.scorecard as sc


def fresh_card():
    return {"agents": {a: {"right": 0, "wrong": 0} for a in sc.DEFAULT_WEIGHTS},
            "trades": 0, "weights": dict(sc.DEFAULT_WEIGHTS), "weight_log": []}


def test_no_recalibration_below_min_trades():
    card = fresh_card()
    card["trades"] = sc.MIN_TRADES - 1
    card["agents"]["technical"] = {"right": 15, "wrong": 4}
    sc._recalibrate(card)
    assert card["weights"] == sc.DEFAULT_WEIGHTS
    assert card["weight_log"] == []


def test_recalibration_tilts_toward_predictive_agent():
    card = fresh_card()
    card["trades"] = sc.MIN_TRADES
    card["agents"]["technical"] = {"right": 18, "wrong": 2}    # 90% hit
    card["agents"]["fundamental"] = {"right": 10, "wrong": 10}  # coin flip
    card["agents"]["sentiment"] = {"right": 4, "wrong": 16}    # 20% hit
    sc._recalibrate(card)
    w = card["weights"]
    assert w["technical"] > sc.DEFAULT_WEIGHTS["technical"]
    assert w["sentiment"] < sc.DEFAULT_WEIGHTS["sentiment"]
    assert abs(sum(w.values()) - 1.0) < 0.01
    assert len(card["weight_log"]) == 1          # every change is logged


def test_tilt_is_bounded():
    card = fresh_card()
    card["trades"] = 100
    # sentiment perfect, everyone else useless — tilt still clamps
    card["agents"]["technical"] = {"right": 0, "wrong": 50}
    card["agents"]["fundamental"] = {"right": 0, "wrong": 50}
    card["agents"]["sentiment"] = {"right": 50, "wrong": 0}
    sc._recalibrate(card)
    # pre-normalization raw values are default ± MAX_TILT
    raw_sent = sc.DEFAULT_WEIGHTS["sentiment"] + sc.MAX_TILT
    raw_tech = sc.DEFAULT_WEIGHTS["technical"] - sc.MAX_TILT
    total = raw_sent + raw_tech + (sc.DEFAULT_WEIGHTS["fundamental"] - sc.MAX_TILT)
    assert abs(card["weights"]["sentiment"] - raw_sent / total) < 0.001
    # even a perfect sentiment agent cannot outrank a gutted technical default
    assert card["weights"]["technical"] == round(raw_tech / total, 4)


def test_record_closed_trade_scores_agents(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "SCORECARD_FILE", tmp_path / "scorecard.json")
    # fake debate transcript on disk
    import src.desk.debate as debate
    monkeypatch.setattr(debate, "DEBATES_DIR", tmp_path)
    transcript = {"id": "dbt-test1", "opinions": [
        {"agent": "technical", "view": "bull"},
        {"agent": "fundamental", "view": "bear"},
        {"agent": "sentiment", "view": "neutral"},
        {"agent": "macro", "view": "bull"},
    ]}
    (tmp_path / "dbt-test1.json").write_text(json.dumps(transcript), encoding="utf-8")

    # long trade that won: technical (bull) right, fundamental (bear) wrong,
    # sentiment (neutral) and macro not scored
    sc.record_closed_trade({"debate_id": "dbt-test1", "pnl": 100.0,
                            "entry_side": "buy"})
    card = sc._load()
    assert card["agents"]["technical"] == {"right": 1, "wrong": 0}
    assert card["agents"]["fundamental"] == {"right": 0, "wrong": 1}
    assert card["agents"]["sentiment"] == {"right": 0, "wrong": 0}
    assert "macro" not in card["agents"]
    assert card["trades"] == 1


def test_record_ignores_missing_debate(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "SCORECARD_FILE", tmp_path / "scorecard.json")
    sc.record_closed_trade({"debate_id": "nope", "pnl": 50.0, "entry_side": "buy"})
    assert not (tmp_path / "scorecard.json").exists()
