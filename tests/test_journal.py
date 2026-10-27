"""
The trade journal: every desk decision, what happened after it, and the
ledger mirror that grades it.
"""
from __future__ import annotations

import json
import uuid

import pytest

from src.desk import journal as J


SIG = {"current_price": 100.0, "stop_loss": 95.0, "take_profit": 110.0,
       "composite_score": 30.0, "bullish_prob": 0.62, "asset_class": "equities",
       "regime": "Bull", "atr_pct": 0.02}


@pytest.fixture
def jdb(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "JOURNAL_DB", tmp_path / "aria_journal.db")
    monkeypatch.setattr(J, "_signal_snapshot", lambda t: dict(SIG))
    return J


def _transcript(verdict="NO_TRADE", view="bull", conviction=55, ticker="AAPL"):
    return {
        "id": f"dbt-{uuid.uuid4().hex[:8]}",
        "ticker": ticker,
        "at": "2026-10-20T15:00:00",
        "llm": "deterministic (Ollama offline)",
        "prompt_version": "desk-debate-v1",
        "opinions": [
            {"agent": "technical", "view": "bull", "conviction": 70,
             "thesis": "Trend up. Momentum strong."},
            {"agent": "fundamental", "view": "bear", "conviction": 40,
             "thesis": "Rich valuation"},
            {"agent": "sentiment", "view": "neutral", "conviction": 0,
             "thesis": "No news"},
            {"agent": "macro", "view": "bull", "conviction": 50,
             "thesis": "Constructive backdrop"},
        ],
        "contradictions": [{"flag": "same figure", "value": 1, "source": "x"}],
        "rounds": [{"round": 1, "bull": "Bull r1", "bear": "Bear r1"},
                   {"round": 2, "bull": "Bull final. More.", "bear": "Bear final"},
                   {"round": "guard", "note": "guard"}],
        "judge": {"view": view, "conviction": conviction, "conviction_bar": 70,
                  "verdict": verdict, "regime": "Expansion",
                  "risk_multiplier": 0.75, "invalidation_level": 94.0,
                  "reasoning": "Nets bull below the bar. Second sentence."},
    }


def _ledger_rows(did):
    from src.core import ledger
    with ledger.connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM predictions WHERE external_ref LIKE ?", (f"journal:{did}:%",))]


# ── decisions ────────────────────────────────────────────────────────────────

def test_no_trade_is_logged_with_every_voter(jdb):
    t = _transcript()
    assert jdb.log_decision(t, cfg={"max_hold_days": 10}) == t["id"]
    d = jdb.decisions()[0]
    assert d["direction"] == "no-trade" and d["verdict"] == "NO_TRADE"
    assert d["confidence"] == 55 and d["entry_price"] == 100.0
    assert d["stop_loss"] == 95.0 and d["target"] == 110.0
    votes = json.loads(d["votes"])
    assert set(votes) == {"technical", "fundamental", "sentiment", "macro",
                          "bull", "bear", "judge"}
    assert votes["technical"]["reason"] == "Trend up."
    assert votes["bull"]["reason"] == "Bull final."        # last numbered round
    assert d["vote_fundamental"] == "bear"
    assert d["n_contradictions"] == 1
    assert d["sentinel_status"] == "NOT_CONSULTED"
    assert d["regime"] == "Expansion" and d["prompt_version"] == "desk-debate-v1"
    assert d["aria_version"]
    assert "trading_agents" not in " ".join(d).lower()


def test_log_decision_is_idempotent(jdb):
    t = _transcript()
    jdb.log_decision(t)
    jdb.log_decision(t)
    assert len(jdb.decisions()) == 1
    assert len(_ledger_rows(t["id"])) == 2


def test_decision_is_mirrored_to_the_ledger_on_two_horizons(jdb):
    t = _transcript(verdict="BUY", view="bull", conviction=80)
    jdb.log_decision(t)
    rows = sorted(_ledger_rows(t["id"]), key=lambda r: r["horizon_days"])
    assert [r["horizon_days"] for r in rows] == [1, 5]
    assert all(r["source"] == "desk-journal" and r["direction"] == "bull"
               and r["price_at"] == 100.0 for r in rows)
    d = jdb.decisions()[0]
    assert d["ledger_pred_1d"] == rows[0]["id"]
    assert d["ledger_pred_1w"] == rows[1]["id"]


@pytest.mark.parametrize("bad", [None, {}, {"id": "x"}, {"ticker": "A"},
                                 {"id": "y", "ticker": "B", "judge": None}])
def test_log_decision_never_raises(jdb, bad):
    jdb.log_decision(bad)          # must not raise into the desk cycle


def test_record_slate_stores_sentinel_and_execution(jdb):
    traded, rejected, unsized = (_transcript("BUY", conviction=80),
                                 _transcript("BUY", conviction=81),
                                 _transcript("SELL", "bear", 82))
    for t in (traded, rejected, unsized):
        jdb.log_decision(t)
    slate = {"slate": [{"debate_id": traded["id"], "qty": 3, "stop": 96.0,
                        "target": 112.0,
                        "sentinel": {"sentinel_status": "OK", "consulted": True,
                                     "ok": True, "summary": "x" * 900}}],
             "rejected": [{"trade": {"debate_id": rejected["id"], "qty": 1,
                                     "sentinel": None},
                           "reason": "Heat cap"}]}
    execs = [{"debate_id": traded["id"], "mode": "auto", "trade_id": "q1",
              "fill_price": 100.5, "at": "2026-10-20T15:01:00"}]
    jdb.record_slate(slate, execs)
    by = {d["id"]: d for d in jdb.decisions()}
    a = by[traded["id"]]
    assert a["slate_status"] == "slated" and a["executed"] == 1
    assert a["status"] == "traded" and a["fill_price"] == 100.5
    assert a["sentinel_status"] == "OK"
    assert len(json.loads(a["sentinel"])["summary"]) == 600
    b = by[rejected["id"]]
    assert b["slate_status"].startswith("rejected: Heat cap")
    assert b["sentinel_status"] == "NOT_CONSULTED"
    assert by[unsized["id"]]["slate_status"] == "not sized by the planner"


def test_mark_executed_covers_the_reflex_lane(jdb):
    t = _transcript()                      # a NO_TRADE the reflex later fired
    jdb.log_decision(t)
    jdb.mark_executed(t["id"], 101.0, "q9")
    d = jdb.decisions()[0]
    assert d["executed"] == 1 and d["status"] == "traded" and d["trade_id"] == "q9"


# ── costs ────────────────────────────────────────────────────────────────────

def test_trade_costs_are_bps_per_side():
    c = J.trade_costs("equity", 100.0, 110.0, 10)
    assert c["costs"] == pytest.approx(5 / 10_000 * 10 * 210)
    assert c["cost_pct"] == pytest.approx(c["costs"] / 1000 * 100)
    assert J.trade_costs("crypto", 100, 100, 1)["cost_bps_per_side"] == 30.0
    assert J.cost_bps("equity", {"cost_bps_equity": 12.0}) == 12.0
    assert J.cost_bps("equity", {"cost_bps_equity": float("nan")}) == 5.0


# ── trade outcomes ───────────────────────────────────────────────────────────

def _close_record(did, exit_price, qty=10, fraction=1.0, reason="target hit",
                  side="buy"):
    entry = 100.0
    d = 1 if side == "buy" else -1
    return {"at": "2026-10-22T15:00:00", "ticker": "AAPL", "entry_side": side,
            "qty": qty, "entry_price": entry, "exit_price": exit_price,
            "pnl": round((exit_price - entry) * qty * d, 2),
            "pnl_pct": round((exit_price - entry) / entry * 100 * d, 2),
            "reason": reason, "fraction": fraction, "debate_id": did}


def test_trade_outcome_after_costs_with_excursions(jdb):
    t = _transcript("BUY", conviction=80)
    jdb.log_decision(t)
    pos = {"asset_class": "equity", "high_water": 111.0, "low_water": 97.0}
    jdb.log_outcome(_close_record(t["id"], 110.0), pos)
    o = jdb.outcomes(t["id"], "trade")[0]
    assert o["status"] == "final" and o["result"] == "win"
    assert o["pnl_abs_gross"] == pytest.approx(100.0)
    assert o["pnl_abs_net"] == pytest.approx(100.0 - 5 / 10_000 * 10 * 210)
    assert o["pnl_pct_net"] < o["pnl_pct_gross"]
    assert o["mae_pct"] == pytest.approx(-3.0) and o["mae_price"] == 97.0
    assert o["mfe_pct"] == pytest.approx(11.0) and o["mfe_price"] == 111.0
    assert o["target_hit"] == 1 and o["stop_hit"] == 0
    right = json.loads(o["right_voters"])
    assert right["technical"] is True and right["fundamental"] is False
    assert right["sentiment"] is None
    assert right["bull"] is True and right["bear"] is False and right["judge"] is True
    assert jdb.decisions()[0]["status"] == "closed"


def test_short_trade_excursions_are_mirrored(jdb):
    t = _transcript("SELL", view="bear", conviction=80)
    jdb.log_decision(t)
    pos = {"asset_class": "equity", "high_water": 92.0, "low_water": 104.0}
    jdb.log_outcome(_close_record(t["id"], 95.0, side="sell", reason="stop hit"), pos)
    o = jdb.outcomes(t["id"], "trade")[0]
    assert o["mae_pct"] == pytest.approx(-4.0) and o["mae_price"] == 104.0
    assert o["mfe_pct"] == pytest.approx(8.0) and o["mfe_price"] == 92.0
    assert o["stop_hit"] == 1


def test_a_gross_win_eaten_by_costs_is_a_scratch(jdb):
    t = _transcript("BUY", conviction=80)
    jdb.log_decision(t)
    jdb.log_outcome(_close_record(t["id"], 100.05), {"asset_class": "equity"})
    o = jdb.outcomes(t["id"], "trade")[0]
    assert o["pnl_abs_gross"] > 0 and o["result"] == "scratch"


def test_scale_out_legs_make_one_trade(jdb):
    t = _transcript("BUY", conviction=80)
    jdb.log_decision(t)
    pos = {"asset_class": "equity", "high_water": 112.0, "low_water": 99.0}
    jdb.log_outcome(_close_record(t["id"], 110.0, qty=5, fraction=0.5), pos)
    o = jdb.outcomes(t["id"], "trade")[0]
    assert o["status"] == "partial" and o["result"] is None
    jdb.log_outcome(_close_record(t["id"], 104.0, qty=5, reason="trailing stop"), pos)
    o = jdb.outcomes(t["id"], "trade")
    assert len(o) == 1 and o[0]["status"] == "final"
    assert o[0]["pnl_abs_gross"] == pytest.approx(50 + 20)
    assert o[0]["exit_price"] == pytest.approx(107.0)
    assert len(json.loads(o[0]["legs"])) == 2


def test_adopted_positions_without_a_debate_are_skipped(jdb):
    jdb.log_outcome(_close_record("", 101.0), {})
    assert jdb.outcomes() == []


# ── the hourly tracker and horizon grades ────────────────────────────────────

def _grade_in_ledger(pred_id, price_end, ret):
    from src.core import ledger
    assert ledger.grade(pred_id, correct=ret > 0, price_end=price_end,
                        actual_return=ret, announce=False)


def test_tracker_samples_and_copies_ledger_grades(jdb):
    t = _transcript()                   # NO_TRADE on a bull view
    jdb.log_decision(t)
    from datetime import datetime
    now = datetime(2026, 10, 21, 12)
    prices = iter([94.0, 104.0])
    out = jdb.track_once(price_fn=lambda tk: next(prices), now=now)
    assert out["sampled"] == 1 and out["graded"] == 0
    jdb.track_once(price_fn=lambda tk: next(prices), now=now)
    d = jdb.decisions()[0]
    assert d["track_low"] == 94.0 and d["track_high"] == 104.0
    assert d["stop_touched_at"], "price 94 is through the 95 stop for a bull view"

    _grade_in_ledger(d["ledger_pred_1d"], 103.0, 0.03)
    out = jdb.track_once(price_fn=lambda tk: 103.0, now=now)
    assert out["graded"] == 1
    o = jdb.outcomes(t["id"], "horizon_1d")[0]
    assert o["status"] == "final" and o["exit_price"] == 103.0
    assert o["pnl_pct_gross"] == pytest.approx(3.0)
    assert o["pnl_pct_net"] == pytest.approx(3.0 - 0.10)    # 5 bps x 2
    assert o["result"] == "win"
    assert o["mae_pct"] == pytest.approx(-6.0) and o["mfe_pct"] == pytest.approx(4.0)
    assert o["stop_hit"] == 1
    right = json.loads(o["right_voters"])
    # The judge declined a view that would have won: the NO_TRADE was wrong.
    assert right["judge"] is False and right["technical"] is True
    assert "hypothetical" in o["note"]


def test_a_no_trade_on_a_view_that_lost_was_right(jdb):
    t = _transcript()
    jdb.log_decision(t)
    d = jdb.decisions()[0]
    _grade_in_ledger(d["ledger_pred_1w"], 96.0, -0.04)
    jdb.track_once(price_fn=lambda tk: 96.0)
    o = jdb.outcomes(t["id"], "horizon_1w")[0]
    assert o["result"] == "loss"
    assert json.loads(o["right_voters"])["judge"] is True


def test_an_unresolvable_ledger_grade_is_recorded_as_ungradable(jdb):
    from src.core import ledger
    t = _transcript()
    jdb.log_decision(t)
    d = jdb.decisions()[0]
    ledger.grade(d["ledger_pred_1d"], correct=None,
                 note="GRADING_SKIPPED / IDENTITY_UNRESOLVED", announce=False)
    jdb.track_once(price_fn=lambda tk: None)
    o = jdb.outcomes(t["id"], "horizon_1d")[0]
    assert o["status"] == "ungradable" and o["result"] is None
    assert "IDENTITY_UNRESOLVED" in o["note"]


def test_a_neutral_view_has_no_directional_pnl(jdb):
    t = _transcript(view="neutral", conviction=0)
    jdb.log_decision(t)
    d = jdb.decisions()[0]
    _grade_in_ledger(d["ledger_pred_1d"], 101.0, 0.01)
    jdb.track_once(price_fn=lambda tk: 101.0)
    o = jdb.outcomes(t["id"], "horizon_1d")[0]
    assert o["pnl_pct_gross"] is None and o["result"] is None


def test_tracker_never_raises_on_a_broken_price_feed(jdb):
    jdb.log_decision(_transcript())

    def boom(t):
        raise RuntimeError("feed down")
    assert jdb.track_once(price_fn=boom)["errors"] == 0


# ── wiring ───────────────────────────────────────────────────────────────────

def test_position_close_records_net_pnl_and_journals_it(jdb, monkeypatch):
    from src.desk import position_manager as pm
    from src.execution.broker_base import OrderResult, OrderStatus
    t = _transcript("BUY", conviction=80, ticker="BTC-USD")
    jdb.log_decision(t)

    class B:
        def is_connected(self):
            return True

        def submit_order(self, req):
            return OrderResult(broker_order_id="x", status=OrderStatus.FILLED,
                               filled_qty=req.qty, avg_fill_price=110.0)

        def cancel_order(self, oid):
            return True

    class M:
        _alpaca = B()

        def open_orders_by_ticker(self):
            return {}

    eng = pm.PositionManager.__new__(pm.PositionManager)
    eng.cfg = {}
    logged = []
    monkeypatch.setattr(eng, "_log_execution", lambda r: None, raising=False)
    monkeypatch.setattr(eng, "_log_closed", logged.append, raising=False)
    monkeypatch.setattr(eng, "_update_scorecard", lambda r: None, raising=False)
    monkeypatch.setattr(eng, "_record_outcome_memory", lambda r: None, raising=False)
    monkeypatch.setattr("src.desk.notify.push", lambda m, c=None: None)
    monkeypatch.setattr("src.execution.live_guard.paper_confirmed", lambda b: True)
    tracked = {"BTC-USD": {"asset_class": "crypto", "side": "long", "qty": 1,
                           "entry_price": 100.0, "debate_id": t["id"],
                           "high_water": 112.0, "low_water": 98.0}}
    eng._close(tracked, "BTC-USD", {"qty": 1, "side": "long"}, M(), reason="target hit")

    rec = logged[0]
    assert rec["pnl"] == 10.0, "gross pnl must keep its meaning"
    assert rec["pnl_net"] == pytest.approx(10.0 - 30 / 10_000 * 210, abs=0.01)
    o = jdb.outcomes(t["id"], "trade")[0]
    assert o["mae_price"] == 98.0 and o["cost_bps_per_side"] == 30.0


def test_ingest_prefers_net_pnl(monkeypatch, tmp_path):
    from src.core import ingest, ledger
    f = tmp_path / "desk" / "closed_trades.jsonl"
    f.parent.mkdir(parents=True)
    at = f"2026-10-2{uuid.uuid4().int % 9}T10:00:{uuid.uuid4().int % 60:02d}"
    did = f"dbt-{uuid.uuid4().hex[:8]}"
    f.write_text(json.dumps({"ticker": "AAPL", "at": at, "debate_id": did,
                             "pnl": 10.0, "pnl_net": 8.9, "pnl_pct": 1.0,
                             "pnl_pct_net": 0.89}) + "\n")
    monkeypatch.setattr(ingest, "DATA", tmp_path)
    ingest.ingest_closed_trades()
    with ledger.connect() as c:
        r = c.execute("SELECT pnl, outcome FROM decisions WHERE external_ref=?",
                      (f"desk-trade:{did}:{at}",)).fetchone()
    assert r["pnl"] == 8.9 and "after est. costs" in r["outcome"]


def test_planned_trade_carries_the_second_opinion():
    from src.brain.cognitive.planner import PlannedTrade
    p = PlannedTrade("AAPL", "buy", 1, "equity", 95, 110, 30, 80, "t")
    assert p.second_opinion is None


def test_desk_cycle_journals_every_verdict():
    """Wiring pin: the cycle calls log_decision right after each debate and
    record_slate after execution."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "src" / "desk" /
           "desk_daemon.py").read_text(encoding="utf-8")
    i_debate = src.index("engine.debate(ticker, opinions, conditioner)")
    i_log = src.index("journal.log_decision(t, conditioner, cfg)")
    assert 0 < i_log - i_debate < 400
    assert src.index("journal.record_slate(") > src.index("executor.execute_slate(")
    assert '"journal_track"' in src
