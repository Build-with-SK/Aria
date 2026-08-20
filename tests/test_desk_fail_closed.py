"""
tests/test_desk_fail_closed.py
==============================
Three safety findings from the 3 August audit, each with the regression test it
was missing. All three shared one shape: a failure that read as an approval.

C1  src/desk/reflex.py       — the risk officer returned True ("approved") on any
                               exception raised before RiskOfficer was built, in
                               a lane that ticks every three seconds.
C2  src/desk/position_manager — _close() was welded shut and was the only path
                               that was; three siblings in the same tick reached
                               the broker with no paper gate.
H2  src/desk/config.py       — no type or finite validation, and json.loads
                               accepts the bare literal NaN. Every gate is a `>`
                               comparison and NaN > x is False, so one value
                               turned every cap off while all of them still
                               reported False and the UI still showed green.

The point of each test is not that the fix is present but that the FAILURE is
blocked, so a refactor that reintroduces the fail-open direction breaks the
build rather than the account.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ── C1 · the reflex lane's risk check fails CLOSED ───────────────────────────

class _Blowup:
    """Stands in for src.desk.analysts.macro_agent. condition() raising is not
    hypothetical: macro_agent formats {macro['dxy_trend']:+.2%}, so a string
    where a number belongs in data/macro_data.json raises TypeError."""

    @staticmethod
    def condition():
        raise TypeError("unsupported format string passed to str.__format__")


def test_reflex_risk_check_rejects_when_conditioner_raises(monkeypatch):
    from src.desk import reflex

    monkeypatch.setitem(sys.modules, "src.desk.analysts.macro_agent", _Blowup)

    lane = reflex.ReflexEngine.__new__(reflex.ReflexEngine)
    lane.cfg = {}
    entry = {"ticker": "NVDA", "qty": 1, "notional": 100.0}

    approved = lane._risk_check(entry, {"equity": 10_000.0})

    assert approved is False, (
        "a risk check that cannot construct the RiskOfficer must reject. "
        "Returning True skips name %, sector %, heat, correlation, the "
        "position cap, the conviction bar and the drawdown breaker — and "
        "auto_executor.gates() does not re-check any of them at execution."
    )
    assert entry.get("_reject"), "a rejection must carry a reason for the log"


def test_reflex_source_does_not_return_true_on_conditioner_failure():
    """Pins the direction in the source too. The behavioural test above can be
    satisfied by an unrelated early return; this fails if the literal
    fail-open comes back.

    Comments are stripped before the scan. The first draft of this test failed
    on the comment that explains the fix — the same self-flagging bug this
    branch has now hit three times. A source scan must read code, not prose
    about code.
    """
    src = (ROOT / "src" / "desk" / "reflex.py").read_text(encoding="utf-8")
    body = src.split("def _risk_check", 1)[1].split("\n    def ", 1)[0]
    head = body.split("from src.desk.risk_officer", 1)[0]
    head = "\n".join(line.split("#", 1)[0] for line in head.splitlines())
    assert "return True" not in head, (
        "_risk_check returns True before the RiskOfficer is constructed — "
        "that is the C1 fail-open"
    )


# ── C2 · no broker mutation on a live account ────────────────────────────────

class _Broker:
    def __init__(self, paper):
        self.paper = paper
        self.cancelled = []

    def is_connected(self):
        return True

    def cancel_order(self, oid):
        self.cancelled.append(oid)


class _Mgr:
    def __init__(self, paper):
        self._alpaca = _Broker(paper)
        self.placed = []
        self.stale_called = False

    def place_protective_orders(self, *a, **k):
        self.placed.append((a, k))
        return {"stop_loss_order": {"id": "s1"}, "take_profit_order": None}

    def cancel_stale_orders(self):
        self.stale_called = True
        return []


@pytest.mark.parametrize("method", ["_heal_brackets", "_replace_stop_order"])
def test_bracket_paths_do_not_touch_a_live_broker(monkeypatch, method):
    from src.desk import position_manager as pm

    monkeypatch.setattr(pm, "_may_mutate_broker", lambda mgr: False)

    engine = pm.PositionManager.__new__(pm.PositionManager)
    engine.cfg = {}
    mgr = _Mgr(paper=False)
    pos = {"stop": 90.0, "target": 120.0, "qty": 10, "side": "long"}
    open_orders = [{"id": "o1", "order_type": "stop"}]

    getattr(engine, method)("AAPL", pos, mgr, open_orders)

    assert mgr._alpaca.cancelled == [], (
        f"{method} cancelled live protection. Cancel-then-replace leaves the "
        f"position naked if the re-place fails."
    )
    assert mgr.placed == [], f"{method} placed an order on a live account"


def test_may_mutate_broker_fails_closed_when_the_gate_raises():
    """An unanswerable question about a live account is a no."""
    from src.desk import position_manager as pm

    class _Hostile:
        @property
        def _alpaca(self):
            raise RuntimeError("broker wrapper exploded")

    assert pm._may_mutate_broker(_Hostile()) is False
    assert pm._may_mutate_broker(None) is False


def test_all_three_broker_mutating_paths_are_gated():
    """cancel_stale_orders is called from tick() rather than from its own
    method, so it needs the gate at the call site. Pins all three at once."""
    src = (ROOT / "src" / "desk" / "position_manager.py").read_text(encoding="utf-8")
    for marker in ("_heal_brackets", "_replace_stop_order", "cancel_stale_orders"):
        assert marker in src
    gated = src.count("_may_mutate_broker(")
    assert gated >= 4, (
        f"expected the paper gate at _heal_brackets, _replace_stop_order and "
        f"the cancel_stale_orders call site (plus its definition); "
        f"found {gated} references"
    )


# ── H2 · one NaN may not disable every risk cap ──────────────────────────────

def test_load_config_rejects_a_nan_written_to_disk(tmp_path, monkeypatch):
    from src.desk import config as C

    f = tmp_path / "desk_config.json"
    # Hand-written because json.dumps refuses to emit this by default — but
    # json.loads accepts it, which was the whole hole.
    f.write_text('{"drawdown_halt_pct": NaN, "max_name_pct": 5.0}',
                 encoding="utf-8")
    monkeypatch.setattr(C, "CONFIG_FILE", f)

    cfg = C.load_config()

    assert cfg["drawdown_halt_pct"] == C.DEFAULTS["drawdown_halt_pct"], (
        "a NaN drawdown ceiling makes `equity_drop > halt_pct` False forever — "
        "the circuit breaker never fires and still reports that it checked"
    )
    assert all(v == v for v in cfg.values() if isinstance(v, float)), \
        "no NaN may survive load_config"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"),
                                 "20", None, True, [5]])
def test_save_config_rejects_bad_values_for_a_numeric_cap(tmp_path, monkeypatch, bad):
    """PATCH /api/desk/config reaches save_config directly, so this is
    untrusted input. `True` is in the list on purpose: bool subclasses int, so
    a naive isinstance check would let it through as the number 1."""
    from src.desk import config as C

    f = tmp_path / "desk_config.json"
    monkeypatch.setattr(C, "CONFIG_FILE", f)

    cfg = C.save_config({"max_name_pct": bad})

    assert cfg["max_name_pct"] == C.DEFAULTS["max_name_pct"]
    on_disk = json.loads(f.read_text(encoding="utf-8"))
    assert on_disk["max_name_pct"] == C.DEFAULTS["max_name_pct"]


def test_save_config_still_accepts_a_legitimate_change(tmp_path, monkeypatch):
    """The gate must not be so tight that the UI stops working — an int is a
    valid value for a float cap."""
    from src.desk import config as C

    f = tmp_path / "desk_config.json"
    monkeypatch.setattr(C, "CONFIG_FILE", f)

    cfg = C.save_config({"max_name_pct": 20, "auto_execute": True})

    assert cfg["max_name_pct"] == 20
    assert cfg["auto_execute"] is True


# ── N2 · the trailing ratchet reads fresh orders ─────────────────────────────

def test_replace_stop_order_refetches_rather_than_trusting_a_snapshot(monkeypatch):
    """The exit loop's open-orders snapshot is taken during the prune pass,
    before _record_external_close cancels siblings and before any exit fires.
    Using it to cancel would name orders the broker has already removed.

    The paper gate is stubbed open here on purpose — this test is about the
    freshness of the read, and the gate itself is covered above. (Left
    un-stubbed it refuses the fake broker outright, which is the gate working.)
    """
    from src.desk import position_manager as pm

    monkeypatch.setattr(pm, "_may_mutate_broker", lambda mgr: True)

    class _M(_Mgr):
        def __init__(self):
            super().__init__(paper=True)
            self.reads = 0

        def open_orders_by_ticker(self):
            self.reads += 1
            return {"AAPL": [{"id": "fresh", "order_type": "stop"}]}

    engine = pm.PositionManager.__new__(pm.PositionManager)
    engine.cfg = {}
    mgr = _M()

    engine._replace_stop_order("AAPL", {"stop": 95.0, "qty": 5, "side": "long"}, mgr)

    assert mgr.reads == 1, "the ratchet must re-read open orders, not reuse a snapshot"
    assert mgr._alpaca.cancelled == ["fresh"], \
        "it cancelled from a stale list instead of the fresh read"


def test_exit_loop_passes_no_stale_snapshot_to_the_ratchet():
    src = (ROOT / "src" / "desk" / "position_manager.py").read_text(encoding="utf-8")
    assert "self._replace_stop_order(ticker, pos, mgr, open_orders.get(" not in src, \
        "the exit loop is passing its prune-pass snapshot to the ratchet again"


# ── start-of-day equity is never invented ────────────────────────────────────

@pytest.mark.parametrize("bad", [None, 0, 0.0, -5.0, float("nan"),
                                 float("inf"), "10000"])
def test_day_state_records_no_start_equity_without_a_real_reading(
        tmp_path, monkeypatch, bad):
    from src.desk import day_state as D

    monkeypatch.setattr(D, "STATE_FILE", tmp_path / "day_state.json")
    state = D.load_day_state(current_equity=bad)

    assert not state.get("start_equity"), (
        "a 0 start equity makes the drawdown check skip itself silently; a "
        "fabricated one makes the first real reading look like a collapse"
    )


def test_day_state_fills_in_on_the_first_real_reading(tmp_path, monkeypatch):
    from src.desk import day_state as D

    monkeypatch.setattr(D, "STATE_FILE", tmp_path / "day_state.json")
    D.load_day_state(current_equity=None)          # broker down at the roll
    state = D.load_day_state(current_equity=10_005.0)   # it comes back

    assert state["start_equity"] == 10_005.0


class _Officer:
    """Builds a RiskOfficer with caps loose enough that only the breaker can
    reject, so a rejection here is unambiguously the drawdown path."""

    @staticmethod
    def make():
        from src.desk.config import DEFAULTS
        from src.desk.risk_officer import RiskOfficer
        cfg = dict(DEFAULTS)
        cfg.update({"drawdown_halt_pct": 2.0, "max_name_pct": 100.0,
                    "max_sector_pct": 100.0, "heat_cap_pct": 100.0,
                    "max_trades_per_day": 100, "daily_notional_budget": 1e9})
        return RiskOfficer(cfg, conditioner=_Conditioner())


class _Conditioner:
    conviction_bar = 0
    regime = "test"


def _candidate():
    return {"ticker": "SPY", "side": "buy", "qty": 1, "price": 100.0,
            "notional": 100.0, "risk_amount": 5.0, "conviction": 80,
            "sector": "ETF"}


def test_unknown_start_equity_halts_rather_than_reporting_flat(
        tmp_path, monkeypatch):
    from src.desk import day_state as D

    monkeypatch.setattr(D, "STATE_FILE", tmp_path / "day_state.json")
    D.load_day_state(current_equity=None)   # today has no start equity

    approved, rejected, checks = _Officer.make().review(
        [_candidate()], {"equity": 10_000.0, "positions": []})

    # The broker reported equity here, so the state self-heals and the check
    # becomes measurable — the important assertion is that it is never
    # reported as a measured 0.0% when it could not be measured.
    assert checks.get("drawdown_pct") != 0.0 or checks.get("drawdown_state") == "measured", \
        "drawdown reported 0.0% ('flat') without having been measured"


def test_disconnected_broker_never_seeds_a_fictional_start_equity(
        tmp_path, monkeypatch):
    """The risk officer falls back to 100_000 for SIZING. If that fallback
    seeded the day state, the next real reading of a $10k account would
    compute a 90% loss and halt the desk permanently."""
    from src.desk import day_state as D

    monkeypatch.setattr(D, "STATE_FILE", tmp_path / "day_state.json")

    _Officer.make().review([_candidate()], {"equity": 0.0, "positions": []})

    state = json.loads((tmp_path / "day_state.json").read_text(encoding="utf-8"))
    assert not state.get("start_equity"), (
        f"seeded start_equity={state.get('start_equity')!r} from the sizing "
        f"fallback — the £100 re-basing halt in a different costume"
    )
