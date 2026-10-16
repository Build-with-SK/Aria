"""
tests/test_desk_pnl.py
======================
/api/desk/pnl reported a "total" P&L that was not one.

`total_pnl = equity - curve[0]["equity"]`, where `curve` was the LAST 500 lines
of data/desk/equity_curve.jsonl — a slice taken to keep the chart's payload
small. So "total" was the change over a rolling window, and while the file was
still shorter than 500 lines the window also spanned the paper account being
reset from 103,006.92 to 10,000.00 on 2026-07-20, which it reported as a 90%
loss.

Total is now measured from the first snapshot of the CURRENT account basis —
the one after the most recent capital reset — over the whole file, and the
response says how it was derived (`total_pnl_basis`). Every case here uses a
synthetic curve; the production file is never opened.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import main as bm      # noqa: E402


def _rows(values, start="2026-08-01T10:00:00"):
    from datetime import datetime, timedelta
    t0 = datetime.fromisoformat(start)
    return [{"at": (t0 + timedelta(minutes=30 * i)).isoformat(), "equity": v}
            for i, v in enumerate(values)]


# ── the basis ────────────────────────────────────────────────────────────────

def test_normal_growth_is_measured_from_the_first_snapshot():
    b = bm._account_basis(_rows([10_000, 10_020, 10_015, 10_060]))
    assert b["start_equity"] == 10_000 and b["resets_detected"] == []
    assert b["since"] == "2026-08-01T10:00:00" and b["snapshots"] == 4


def test_an_account_reset_starts_a_new_basis_and_is_not_a_loss():
    b = bm._account_basis(_rows([103_709.58, 103_006.92, 10_000.0, 9_990.0, 10_008.38]))
    assert b["start_equity"] == 10_000.0
    assert b["resets_detected"] == [{"at": "2026-08-01T11:00:00",
                                     "from": 103_006.92, "to": 10_000.0}]


def test_the_latest_of_several_resets_is_the_basis():
    b = bm._account_basis(_rows([100_000, 10_000, 10_050, 25_000, 25_100]))
    assert b["start_equity"] == 25_000 and len(b["resets_detected"]) == 2


def test_ordinary_drawdowns_and_rallies_are_not_resets():
    """Nothing the desk can do moves equity a quarter between snapshots; a
    rough stretch must stay inside one basis."""
    b = bm._account_basis(_rows([10_000, 9_700, 9_400, 9_900, 11_500, 10_900]))
    assert b["resets_detected"] == [] and b["start_equity"] == 10_000


def test_an_empty_curve_has_no_basis_and_invents_none():
    b = bm._account_basis([])
    assert b["start_equity"] is None and b["since"] is None and b["snapshots"] == 0


def test_unusable_rows_are_skipped_not_guessed():
    rows = [{"at": "a", "equity": None}, {"at": "b"}, {"equity": "n/a", "at": "c"},
            {"at": "d", "equity": 0}, "not a dict",
            {"at": "e", "equity": 10_000}, {"at": "f", "equity": "10010.5"}]
    b = bm._account_basis(rows)
    assert b["snapshots"] == 2 and b["start_equity"] == 10_000 and b["since"] == "e"


def test_the_basis_states_what_it_cannot_see():
    assert "deposits or withdrawals" in bm._account_basis(_rows([1, 2]))["blind_to"]


# ── the endpoint ─────────────────────────────────────────────────────────────

@pytest.fixture
def desk(tmp_path, monkeypatch):
    """The endpoint, with a synthetic curve and no broker."""
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    (tmp_path / "data" / "desk").mkdir(parents=True)

    def run(values, *, equity, start_equity=None):
        curve = tmp_path / "data" / "desk" / "equity_curve.jsonl"
        if values is None:
            curve.unlink(missing_ok=True)
        else:
            curve.write_text("".join(json.dumps(r) + "\n" for r in _rows(values)),
                             encoding="utf-8")
        monkeypatch.setattr("src.desk.auto_executor.account_snapshot", lambda: {
            "connected": True, "paper": True, "equity": equity, "cash": equity,
            "positions": []})
        monkeypatch.setattr("src.desk.day_state.load_day_state",
                            lambda current_equity=None: {"date": "2026-10-10",
                                                         "start_equity": start_equity})
        return bm.desk_pnl()
    return run


def test_total_is_not_a_rolling_window(desk):
    """600 snapshots: the first 100 climb from 10,000 to 10,099, the rest sit
    at 10,100. The old code measured from snapshot 100 and reported ~0."""
    values = [10_000 + i for i in range(100)] + [10_100] * 500
    out = desk(values, equity=10_100.0, start_equity=10_100.0)
    assert out["total_pnl"] == 100.0, "total P&L is the window's change again"
    assert out["total_pnl_pct"] == 1.0
    assert out["total_pnl_basis"]["snapshots"] == 600
    assert len(out["equity_curve"]) == 500, "the chart's window must stay 500 points"


def test_a_reset_in_the_history_is_not_reported_as_a_loss(desk):
    out = desk([103_709.58, 103_006.92, 10_000.0, 9_995.0, 10_008.38],
               equity=10_008.38, start_equity=10_008.38)
    assert out["total_pnl"] == 8.38 and out["total_pnl_pct"] == pytest.approx(0.084, abs=1e-3)
    assert out["total_pnl_basis"]["start_equity"] == 10_000.0
    assert out["total_pnl_basis"]["resets_detected"][0]["from"] == 103_006.92


def test_with_no_curve_total_falls_back_to_the_day_and_says_so(desk):
    out = desk(None, equity=10_050.0, start_equity=10_000.0)
    assert out["total_pnl"] == 50.0 and out["equity_curve"] == []
    assert "no equity history" in out["total_pnl_basis"]["method"]


def test_with_nothing_to_measure_from_it_reports_zero_not_a_guess(desk):
    out = desk(None, equity=10_050.0, start_equity=None)
    assert out["total_pnl"] == 0.0 and out["total_pnl_basis"]["start_equity"] is None


def test_the_existing_response_keys_are_all_still_there(desk):
    out = desk([10_000, 10_010], equity=10_010.0, start_equity=10_000.0)
    for key in ("connected", "account", "equity", "cash", "day_pnl", "day_pnl_pct",
                "total_pnl", "total_pnl_pct", "open_positions", "open_win_rate",
                "equity_curve", "day"):
        assert key in out, f"/api/desk/pnl lost {key}"
    assert out["day_pnl"] == 10.0, "the day figure must be untouched"
