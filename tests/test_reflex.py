"""Reflex fast-lane tests — pure trigger/confidence logic + arming + store."""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import src.desk.reflex as reflex
from src.desk.reflex import crossed, confidence_ok, is_expired, Playbook


# ── crossed() — momentum trigger, longs and shorts ───────────────────────

def test_long_fires_on_break_up_through_trigger():
    assert crossed("buy", 100.0, 99.0, 100.5) is True
    assert crossed("buy", 100.0, 100.5, 101.0) is False   # already above, no cross
    assert crossed("buy", 100.0, 99.0, 99.5) is False     # didn't reach


def test_short_fires_on_break_down_through_trigger():
    assert crossed("sell", 100.0, 101.0, 99.5) is True
    assert crossed("sell", 100.0, 99.5, 99.0) is False    # already below
    assert crossed("sell", 100.0, 101.0, 100.5) is False  # didn't reach


def test_no_prev_price_fires_if_already_through():
    assert crossed("buy", 100.0, None, 100.5) is True
    assert crossed("buy", 100.0, None, 99.5) is False
    assert crossed("sell", 100.0, None, 99.5) is True


def test_crossed_guards_bad_inputs():
    assert crossed("buy", 0, 99, 100) is False
    assert crossed("buy", 100, 99, 0) is False


# ── confidence gate ──────────────────────────────────────────────────────

def test_confidence_uses_directional_probability():
    sig = {"bullish_prob": 0.72, "bearish_prob": 0.28}
    ok, p = confidence_ok(sig, "buy", 0.70)
    assert ok and p == 0.72
    ok2, p2 = confidence_ok(sig, "sell", 0.70)
    assert not ok2 and p2 == 0.28


def test_confidence_below_threshold_blocks():
    ok, p = confidence_ok({"bullish_prob": 0.60}, "buy", 0.70)
    assert not ok


# ── expiry ───────────────────────────────────────────────────────────────

def test_is_expired():
    past = {"expires_at": (datetime.now() - timedelta(hours=1)).isoformat()}
    future = {"expires_at": (datetime.now() + timedelta(hours=1)).isoformat()}
    assert is_expired(past) is True
    assert is_expired(future) is False


# ── arming (store round-trip) ────────────────────────────────────────────

def test_arm_from_debate_near_bar(tmp_path, monkeypatch):
    monkeypatch.setattr(reflex, "PLAYBOOKS_FILE", tmp_path / "pb.json")
    monkeypatch.setattr(reflex, "load_data_json",
                        lambda f: {"AAPL": {"current_price": 320.0,
                                            "atr_pct": 0.02,
                                            "stop_loss": 310.0,
                                            "take_profit": 340.0}})
    cfg = {"reflex_arm_conviction_gap": 10, "reflex_playbook_expiry_days": 2}
    # conviction 58, bar 65 → within the 10-gap band → arms
    t = {"id": "dbt-x", "ticker": "AAPL",
         "judge": {"verdict": "BUY", "conviction": 58, "reasoning": "ok"}}
    pb = reflex.arm_from_debate(t, 65, cfg)
    assert pb and pb["side"] == "buy" and pb["ticker"] == "AAPL"
    assert pb["trigger_price"] > 320.0          # break-up trigger for a long
    assert reflex.load_playbooks()[0]["ticker"] == "AAPL"


def test_arm_skips_over_bar_and_far_below(tmp_path, monkeypatch):
    monkeypatch.setattr(reflex, "PLAYBOOKS_FILE", tmp_path / "pb.json")
    monkeypatch.setattr(reflex, "load_data_json",
                        lambda f: {"AAPL": {"current_price": 320.0}})
    cfg = {"reflex_arm_conviction_gap": 10, "reflex_playbook_expiry_days": 2}
    # 70 ≥ bar 65 → the slow lane already trades it, don't arm
    assert reflex.arm_from_debate(
        {"id": "d", "ticker": "AAPL",
         "judge": {"verdict": "BUY", "conviction": 70}}, 65, cfg) is None
    # 50 is 15 under the bar → outside the gap, don't arm
    assert reflex.arm_from_debate(
        {"id": "d", "ticker": "AAPL",
         "judge": {"verdict": "BUY", "conviction": 50}}, 65, cfg) is None


def test_arm_from_signal_spike(tmp_path, monkeypatch):
    monkeypatch.setattr(reflex, "PLAYBOOKS_FILE", tmp_path / "pb.json")
    monkeypatch.setattr(reflex, "load_data_json",
                        lambda f: {"NVDA": {"current_price": 500.0,
                                            "composite_score": 45.0,
                                            "atr_pct": 0.02},
                                   "SPY": {"current_price": 600.0,
                                           "composite_score": 5.0}})
    cfg = {"reflex_signal_spike": 40.0, "reflex_playbook_expiry_days": 2}
    armed = reflex.arm_from_signal(cfg, account={"positions": []})
    tickers = [p["ticker"] for p in armed]
    assert "NVDA" in tickers        # 45 ≥ 40 spike
    assert "SPY" not in tickers     # 5 < 40


def test_signal_spike_skips_held_and_crypto_shorts(tmp_path, monkeypatch):
    monkeypatch.setattr(reflex, "PLAYBOOKS_FILE", tmp_path / "pb.json")
    monkeypatch.setattr(reflex, "load_data_json",
                        lambda f: {"ETH-USD": {"current_price": 3000.0,
                                               "composite_score": -50.0},
                                   "AAPL": {"current_price": 320.0,
                                            "composite_score": 45.0}})
    cfg = {"reflex_signal_spike": 40.0, "reflex_playbook_expiry_days": 2}
    armed = reflex.arm_from_signal(cfg, account={"positions": [{"ticker": "AAPL"}]})
    tickers = [p["ticker"] for p in armed]
    assert "ETH-USD" not in tickers     # crypto short — unsupported
    assert "AAPL" not in tickers        # already held
