"""Exit-rule law tests — the deterministic core of the PositionManager.
No broker, no LLM, no I/O: pure functions only."""
from datetime import datetime, timedelta

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.desk.position_manager import (
    evaluate_exit, exit_debate_score, r_progress, trading_age_days,
    EXIT_DEBATE_CLOSE_AT,
)

CFG = {"max_hold_days": 10, "time_stop_min_r": 0.5, "scale_out_at_target": False}


def long_pos(**over):
    pos = {"ticker": "AAPL", "side": "long", "qty": 10,
           "entry_price": 100.0, "entry_at": datetime.now().isoformat(),
           "stop": 95.0, "initial_stop": 95.0, "target": 110.0,
           "invalidation": 90.0, "high_water": 100.0, "scaled_out": False}
    pos.update(over)
    return pos


def short_pos(**over):
    pos = {"ticker": "TSLA", "side": "short", "qty": 5,
           "entry_price": 200.0, "entry_at": datetime.now().isoformat(),
           "stop": 210.0, "initial_stop": 210.0, "target": 180.0,
           "invalidation": 220.0, "high_water": 200.0, "scaled_out": False}
    pos.update(over)
    return pos


# ── rule 1: hard stop ────────────────────────────────────────────────────

def test_hard_stop_long():
    v = evaluate_exit(long_pos(), 94.9, {}, CFG)
    assert v["action"] == "close" and v["reason"] == "stop hit"


def test_hard_stop_exact_price():
    assert evaluate_exit(long_pos(), 95.0, {}, CFG)["action"] == "close"


def test_hard_stop_short():
    v = evaluate_exit(short_pos(), 210.5, {}, CFG)
    assert v["action"] == "close" and v["reason"] == "stop hit"


def test_no_exit_in_range():
    assert evaluate_exit(long_pos(), 101.0, {}, CFG)["action"] == "hold"


# ── rule 2: target ───────────────────────────────────────────────────────

def test_target_hit_long():
    v = evaluate_exit(long_pos(), 110.2, {}, CFG)
    assert v["action"] == "close" and v["reason"] == "target hit"


def test_target_hit_short():
    assert evaluate_exit(short_pos(), 179.0, {}, CFG)["action"] == "close"


def test_target_scale_out_flag():
    cfg = {**CFG, "scale_out_at_target": True}
    v = evaluate_exit(long_pos(), 110.2, {}, cfg)
    assert v["action"] == "scale_out"
    # already scaled → full close
    v2 = evaluate_exit(long_pos(scaled_out=True), 110.2, {}, cfg)
    assert v2["action"] == "close"


# ── rule 3: trailing stop ────────────────────────────────────────────────

def test_trailing_breakeven_at_1r():
    # risk = 5, price 105 = +1R → stop ratchets to breakeven (no ATR given)
    v = evaluate_exit(long_pos(), 105.0, {}, CFG)
    assert v["action"] == "hold" and v["new_stop"] == 100.0


def test_trailing_atr_beyond_breakeven():
    # +1.6R with 2% ATR: trail = 108 * 0.98 = 105.84 > breakeven
    v = evaluate_exit(long_pos(high_water=107.0), 108.0, {"atr_pct": 0.02}, CFG)
    assert v["new_stop"] == round(108.0 * 0.98, 4)
    assert v["new_high_water"] == 108.0


def test_trailing_never_lowers_stop():
    v = evaluate_exit(long_pos(stop=106.0, initial_stop=95.0), 107.0,
                      {"atr_pct": 0.05}, CFG)   # trail would be 101.65
    assert v["new_stop"] is None


def test_trailing_short_ratchet():
    # short from 200, now 189 (>1R of 10) → breakeven 200 → new stop below 210
    v = evaluate_exit(short_pos(), 189.0, {}, CFG)
    assert v["new_stop"] == 200.0


def test_no_trailing_below_1r():
    v = evaluate_exit(long_pos(), 104.0, {"atr_pct": 0.02}, CFG)
    assert v["new_stop"] is None


# ── rule 4: thesis invalidation ──────────────────────────────────────────

def test_invalidation_price_breach_closes():
    # stop already gone? use stopless position so invalidation is the trigger
    v = evaluate_exit(long_pos(stop=0, initial_stop=0), 89.5, {}, CFG)
    assert v["action"] == "close" and "invalidat" in v["reason"]


def test_composite_flip_triggers_debate():
    v = evaluate_exit(long_pos(), 101.0, {"composite_score": -25}, CFG)
    assert v["action"] == "debate"


def test_composite_flip_short_mirror():
    v = evaluate_exit(short_pos(), 199.0, {"composite_score": 25}, CFG)
    assert v["action"] == "debate"


def test_mild_composite_no_debate():
    v = evaluate_exit(long_pos(), 101.0, {"composite_score": -10}, CFG)
    assert v["action"] == "hold"


# ── rule 5: time stop ────────────────────────────────────────────────────

def test_time_stop_old_and_flat():
    old = (datetime.now() - timedelta(days=20)).isoformat()
    v = evaluate_exit(long_pos(entry_at=old), 100.5, {}, CFG)
    assert v["action"] == "close" and "time stop" in v["reason"]


def test_time_stop_spared_by_progress():
    old = (datetime.now() - timedelta(days=20)).isoformat()
    # +1.2R → progress ≥ 0.5R, no time stop (but trailing ratchet fires)
    v = evaluate_exit(long_pos(entry_at=old), 106.0, {}, CFG)
    assert v["action"] == "hold"


def test_fresh_position_no_time_stop():
    v = evaluate_exit(long_pos(), 100.5, {}, CFG)
    assert v["action"] == "hold"


# ── rule 6: exit-debate scoring ──────────────────────────────────────────

def test_exit_debate_hard_flip_and_losing_closes():
    pos = long_pos()
    score, why = exit_debate_score(pos, 97.0, {"composite_score": -30}, CFG)
    assert score >= EXIT_DEBATE_CLOSE_AT      # 40 (flip) + 20 (losing)
    assert len(why) >= 2


def test_exit_debate_winner_holds():
    score, _ = exit_debate_score(long_pos(), 108.0, {"composite_score": -22}, CFG)
    assert score < EXIT_DEBATE_CLOSE_AT       # only the flip's 40


# ── helpers ──────────────────────────────────────────────────────────────

def test_r_progress_long_short():
    assert r_progress(long_pos(), 105.0) == 1.0
    assert r_progress(short_pos(), 190.0) == 1.0
    assert r_progress(long_pos(), 95.0) == -1.0


def test_trading_age_skips_weekends():
    # 7 calendar days always contain 5 weekdays
    start = (datetime.now() - timedelta(days=7)).isoformat()
    assert trading_age_days(start) == 5


def test_sane_invalidation_guard():
    from src.desk.position_manager import _sane_invalidation
    assert _sane_invalidation(90.0, 100.0, True) == 90.0     # valid long level
    assert _sane_invalidation(402.0, 391.4, True) == 0.0     # above long entry → dropped
    assert _sane_invalidation(210.0, 200.0, False) == 210.0  # valid short level
    assert _sane_invalidation(190.0, 200.0, False) == 0.0    # below short entry → dropped
    assert _sane_invalidation(0.0, 100.0, True) == 0.0


def test_sane_levels_guard():
    from src.desk.position_manager import _sane_levels
    # valid long levels pass through
    assert _sane_levels(95.0, 110.0, 100.0, True) == (95.0, 110.0)
    # the Tuesday AAPL case: stale target below a long entry → dropped
    assert _sane_levels(303.71, 325.97, 326.54, True) == (303.71, None)
    # wrong-side stop dropped
    assert _sane_levels(105.0, 110.0, 100.0, True) == (None, 110.0)
    # short mirror
    assert _sane_levels(210.0, 180.0, 200.0, False) == (210.0, 180.0)
    assert _sane_levels(190.0, 210.0, 200.0, False) == (None, None)
    # no entry → passthrough
    assert _sane_levels(95.0, 110.0, 0.0, True) == (95.0, 110.0)


def test_bad_inputs_hold():
    assert evaluate_exit(long_pos(entry_price=0), 100, {}, CFG)["action"] == "hold"
    assert evaluate_exit(long_pos(), 0, {}, CFG)["action"] == "hold"
