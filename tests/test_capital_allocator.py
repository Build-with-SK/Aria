"""Capital allocator — portfolio-mindful sizing policy (pure functions)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.desk.capital_allocator import allocate, allocate_for_account

CFG = {"max_correlated_positions": 3}


# ── equity bands → mode ──────────────────────────────────────────────────

def test_small_account_conservative_long_only():
    a = allocate(3000, 3000, 3000, "Expansion (Goldilocks)", CFG)
    assert a.mode == "CONSERVATIVE"
    assert a.max_positions == 2
    assert a.shorts_allowed is False
    assert a.size_multiplier < 1.0        # dialed down


def test_mid_account_standard():
    a = allocate(15000, 15000, 15000, "Expansion (Goldilocks)", CFG)
    assert a.mode == "STANDARD"
    assert a.size_multiplier == 1.0
    assert a.net_exposure_cap_pct == 100.0


def test_large_account_diversified_scales_positions():
    a = allocate(50000, 50000, 50000, "Expansion (Goldilocks)", CFG)
    assert a.mode == "DIVERSIFIED"
    assert a.max_positions == min(12, 50000 // 2500)   # 12 (capped)


def test_diversified_position_cap_ceiling():
    a = allocate(20000, 20000, 20000, "Expansion (Goldilocks)", CFG)  # STANDARD
    b = allocate(100000, 100000, 100000, "Expansion (Goldilocks)", CFG)
    assert b.max_positions == 12          # ⌊100000/2500⌋=40 capped at 12


# ── regime interaction ───────────────────────────────────────────────────

def test_risk_off_allows_shorts_and_trims_size():
    calm = allocate(15000, 15000, 15000, "Expansion (Goldilocks)", CFG)
    off = allocate(15000, 15000, 15000, "Risk-Off", CFG)
    assert calm.shorts_allowed is False
    assert off.shorts_allowed is True
    assert off.net_exposure_cap_pct == 50.0
    assert off.size_multiplier < calm.size_multiplier


def test_crisis_flattens_hardest():
    a = allocate(15000, 15000, 15000, "Crisis", CFG)
    assert a.net_exposure_cap_pct == 25.0
    assert a.size_multiplier <= 0.5


# ── cash-awareness ───────────────────────────────────────────────────────

def test_low_free_cash_throttles_new_size():
    full = allocate(20000, 20000, 20000, "Expansion (Goldilocks)", CFG)
    nearly_invested = allocate(20000, 2000, 2000, "Expansion (Goldilocks)", CFG)
    assert nearly_invested.size_multiplier < full.size_multiplier


def test_position_cap_freezes_when_already_full():
    a = allocate(3000, 3000, 3000, "Expansion (Goldilocks)", CFG, open_positions=5)
    # already holding 5 in a 2-name band → cap freezes at current count,
    # no NEW names (exits free room)
    assert a.max_positions == 5


def test_allocate_for_account_wrapper():
    account = {"equity": 15000, "cash": 15000, "buying_power": 15000,
               "positions": [{"ticker": "AAPL"}]}
    a = allocate_for_account(account, "Expansion (Goldilocks)", CFG)
    assert a.mode == "STANDARD"
    assert a.reason and "STANDARD" in a.reason
