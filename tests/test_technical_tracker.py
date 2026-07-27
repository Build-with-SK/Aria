"""Technical recommendations tracker — snapshot idempotency + hit-rate eval."""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import src.data.technical_tracker as tr


def _seed(tmp_path, monkeypatch, rows):
    log = tmp_path / "recs.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(tr, "LOG_FILE", log)


def test_snapshot_dedups_same_day(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "LOG_FILE", tmp_path / "recs.jsonl")
    fake = {"strong_buy": [{"symbol": "AAPL", "summary": "Strong Buy", "price": 100.0}],
            "buy": [], "sell": []}
    monkeypatch.setattr(tr, "scan_universe_recommendations", None, raising=False)
    import src.data.technical_summary as ts
    monkeypatch.setattr(ts, "scan_universe_recommendations", lambda tf, lim: fake)
    r1 = tr.snapshot()
    r2 = tr.snapshot()          # same day again → nothing new
    assert r1["logged"] == 1 and r2["logged"] == 0


def test_evaluate_hit_rate(tmp_path, monkeypatch):
    old = (date.today() - timedelta(days=5)).isoformat()
    _seed(tmp_path, monkeypatch, [
        {"date": old, "symbol": "AAPL", "timeframe": "1d",
         "summary": "Strong Buy", "entry_price": 100.0},   # up → hit
        {"date": old, "symbol": "MSFT", "timeframe": "1d",
         "summary": "Buy", "entry_price": 100.0},           # down → miss
        {"date": old, "symbol": "XOM", "timeframe": "1d",
         "summary": "Strong Sell", "entry_price": 100.0},   # down → hit
    ])
    monkeypatch.setattr(tr, "_current_prices",
                        lambda syms: {"AAPL": 110.0, "MSFT": 95.0, "XOM": 90.0})
    rep = tr.evaluate(min_age_days=1)
    assert rep["graded"] == 3
    by = rep["by_label"]
    assert by["ALL"]["hit_rate"] == round(2 / 3, 3)        # AAPL + XOM hit
    assert by["bullish"]["count"] == 2 and by["bearish"]["count"] == 1
    # bearish call that fell 10% scores +10 in call direction
    assert by["Strong Sell"]["avg_return_in_call_direction_pct"] == 10.0
    # bullish avg: (+10 AAPL, -5 MSFT) / 2 = +2.5
    assert by["bullish"]["avg_return_in_call_direction_pct"] == 2.5


def test_evaluate_skips_fresh(tmp_path, monkeypatch):
    today = date.today().isoformat()
    _seed(tmp_path, monkeypatch, [
        {"date": today, "symbol": "AAPL", "timeframe": "1d",
         "summary": "Buy", "entry_price": 100.0}])
    rep = tr.evaluate(min_age_days=1)
    assert "note" in rep          # nothing ripe yet


def test_empty_log(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "LOG_FILE", tmp_path / "none.jsonl")
    assert "note" in tr.evaluate()
