"""
Tests for the kill switch, the risk-tolerance profiles, and the fail-closed
asset-class gate on the exit path.

These three exist to stop an order, so the tests are written the pessimistic
way round: each one asserts that something did NOT reach a broker.
"""
from __future__ import annotations

import importlib
import json

import pytest

from src.execution import kill_switch as ks
from src.execution.broker_base import (AssetClass, OrderRequest, OrderResult,
                                       OrderSide, OrderStatus, OrderType)
from src.risk import profiles as rp


# ── kill switch ──────────────────────────────────────────────────────────────

@pytest.fixture
def switch(tmp_path, monkeypatch):
    """Point the switch at a temp file and guarantee a clean environment."""
    monkeypatch.setattr(ks, "STATE_FILE", tmp_path / "kill_switch.json")
    monkeypatch.delenv(ks.ENV_VAR, raising=False)
    return ks


def test_switch_starts_clear(switch):
    assert switch.is_engaged() is False
    assert switch.status()["engaged"] is False
    assert switch.guard() is None


def test_engage_and_release_round_trip(switch):
    st = switch.engage("market gapped", actor="ariyan")
    assert st["engaged"] is True
    assert st["source"] == "file"
    assert st["reason"] == "market gapped"
    assert st["engaged_by"] == "ariyan"
    assert switch.is_engaged() is True

    st = switch.release(actor="ariyan")
    assert st["engaged"] is False
    assert switch.is_engaged() is False


def test_engage_is_idempotent(switch):
    switch.engage("once")
    switch.engage("twice")
    assert switch.is_engaged() is True


def test_env_var_engages_and_cannot_be_released(switch, monkeypatch):
    monkeypatch.setenv(ks.ENV_VAR, "1")
    assert switch.is_engaged() is True
    st = switch.status()
    assert st["source"] == "environment"
    assert st["releasable"] is False

    # A release call must not be able to talk its way out of an external halt.
    switch.release(actor="aria")
    assert switch.is_engaged() is True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "engaged"])
def test_env_truthy_values(switch, monkeypatch, value):
    monkeypatch.setenv(ks.ENV_VAR, value)
    assert switch.is_engaged() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_env_falsy_values_do_not_engage(switch, monkeypatch, value):
    monkeypatch.setenv(ks.ENV_VAR, value)
    assert switch.is_engaged() is False


def test_corrupt_state_file_fails_closed(switch):
    switch.STATE_FILE.write_text("{ not json", encoding="utf-8")
    assert switch.is_engaged() is True
    assert switch.status()["source"] == "corrupt-state"


def test_non_object_state_file_fails_closed(switch):
    switch.STATE_FILE.write_text("[1, 2, 3]", encoding="utf-8")
    assert switch.is_engaged() is True


def test_non_bool_engaged_field_fails_closed(switch):
    switch.STATE_FILE.write_text(json.dumps({"engaged": "no"}), encoding="utf-8")
    assert switch.is_engaged() is True


def test_assert_clear_raises_when_engaged(switch):
    switch.assert_clear()          # clear — no raise
    switch.engage("halt")
    with pytest.raises(ks.KillSwitchEngaged):
        switch.assert_clear("test order")


def test_guard_returns_error_dict_when_engaged(switch):
    switch.engage("halt")
    err = switch.guard("trade 7")
    assert err["ok"] is False
    assert "KILL SWITCH ENGAGED" in err["error"]
    assert err["kill_switch"]["engaged"] is True


def test_write_is_atomic_no_tmp_left_behind(switch):
    switch.engage("halt")
    leftovers = list(switch.STATE_FILE.parent.glob("*.tmp"))
    assert leftovers == []


# ── the switch actually stops a broker ───────────────────────────────────────

def test_engaged_switch_rejects_alpaca_order(tmp_path, monkeypatch):
    """The gate must sit below the connection check: an engaged switch
    rejects the order even on a broker that is wired up and willing."""
    from src.execution import alpaca_broker as ab

    monkeypatch.setattr(ks, "STATE_FILE", tmp_path / "kill_switch.json")
    monkeypatch.delenv(ks.ENV_VAR, raising=False)

    broker = ab.AlpacaBroker.__new__(ab.AlpacaBroker)   # no network, no creds
    sentinel = object()
    broker._client = sentinel                            # "connected"

    req = OrderRequest(ticker="AAPL", side=OrderSide.BUY, qty=1,
                       order_type=OrderType.MARKET)

    ks.engage("test halt")
    result = broker.submit_order(req)

    assert result.status == OrderStatus.REJECTED
    assert "KILL SWITCH ENGAGED" in result.error_message
    assert result.broker_order_id == ""
    # And nothing was sent: the client was never touched.
    assert broker._client is sentinel


def test_ibkr_submit_order_checks_switch_first(tmp_path, monkeypatch):
    from src.execution import ibkr_broker as ib

    monkeypatch.setattr(ks, "STATE_FILE", tmp_path / "kill_switch.json")
    monkeypatch.delenv(ks.ENV_VAR, raising=False)

    broker = ib.IBKRBroker.__new__(ib.IBKRBroker)
    # is_connected() would raise on this half-built object; the switch check
    # must return before anything else runs.
    ks.engage("test halt")
    result = broker.submit_order(
        OrderRequest(ticker="AAPL", side=OrderSide.BUY, qty=1))

    assert result.status == OrderStatus.REJECTED
    assert "KILL SWITCH ENGAGED" in result.error_message


# ── risk profiles ────────────────────────────────────────────────────────────

def test_three_profiles_exist():
    assert rp.VALID_PROFILES == ("CONSERVATIVE", "MODERATE", "AGGRESSIVE")
    assert len(rp.all_profiles()) == 3


def test_unknown_profile_falls_back_to_moderate_not_aggressive():
    assert rp.normalise("YOLO") == "MODERATE"
    assert rp.normalise(None) == "MODERATE"
    assert rp.normalise("") == "MODERATE"
    assert rp.get_profile("YOLO").name == "MODERATE"


def test_profile_names_are_case_insensitive():
    assert rp.get_profile("aggressive").name == "AGGRESSIVE"
    assert rp.get_profile("  Conservative  ").name == "CONSERVATIVE"


def test_risk_tolerance_changes_real_numbers():
    """The point of the whole module: the profiles must actually differ, and
    monotonically, or 'risk tolerance' is decoration."""
    c = rp.get_profile("CONSERVATIVE")
    m = rp.get_profile("MODERATE")
    a = rp.get_profile("AGGRESSIVE")

    assert c.risk_per_trade_pct < m.risk_per_trade_pct < a.risk_per_trade_pct
    assert c.max_name_pct < m.max_name_pct < a.max_name_pct
    assert c.heat_cap_pct < m.heat_cap_pct < a.heat_cap_pct
    assert c.max_trades_per_day < m.max_trades_per_day < a.max_trades_per_day
    # A higher tolerance lowers the bar to act, it does not raise it.
    assert c.min_conviction > m.min_conviction > a.min_conviction


def test_no_profile_exceeds_any_hard_limit():
    for p in rp.all_profiles():
        d = p.to_dict()
        for key, ceiling in rp.HARD_LIMITS.items():
            assert d[key] <= ceiling, f"{p.name}.{key}={d[key]} > {ceiling}"
        for key, floor in rp.HARD_FLOORS.items():
            assert d[key] >= floor, f"{p.name}.{key}={d[key]} < {floor}"


def test_clamp_pulls_back_a_reckless_profile():
    """Someone editing AGGRESSIVE to something wild must not get it."""
    reckless = dict(rp._RAW_PROFILES["AGGRESSIVE"])
    reckless.update(max_name_pct=40.0, heat_cap_pct=90.0,
                    max_gross_exposure_pct=300.0, risk_per_trade_pct=25.0)
    clamped = rp.clamp(reckless)

    assert clamped["max_name_pct"] == rp.HARD_LIMITS["max_name_pct"]
    assert clamped["heat_cap_pct"] == rp.HARD_LIMITS["heat_cap_pct"]
    assert clamped["max_gross_exposure_pct"] == 100.0   # never any margin
    assert clamped["risk_per_trade_pct"] == rp.HARD_LIMITS["risk_per_trade_pct"]


def test_clamp_raises_a_conviction_bar_below_the_floor():
    sloppy = dict(rp._RAW_PROFILES["AGGRESSIVE"], min_conviction=5)
    assert rp.clamp(sloppy)["min_conviction"] == rp.HARD_FLOORS["min_conviction"]


def test_no_profile_permits_margin():
    for p in rp.all_profiles():
        assert p.max_gross_exposure_pct <= 100.0


def test_profiles_only_allow_executable_asset_classes():
    for p in rp.all_profiles():
        assert set(p.allowed_asset_classes) <= set(rp.EXECUTABLE_ASSET_CLASSES)


def test_clamp_drops_non_executable_asset_classes():
    wishful = dict(rp._RAW_PROFILES["AGGRESSIVE"],
                   allowed_asset_classes=("equity", "option", "futures"))
    assert rp.clamp(wishful)["allowed_asset_classes"] == ("equity",)


def test_allows_asset_class_fails_closed():
    p = rp.get_profile("AGGRESSIVE")
    assert rp.allows_asset_class(p, "equity") is True
    assert rp.allows_asset_class(p, "CRYPTO") is True
    assert rp.allows_asset_class(p, "option") is False
    assert rp.allows_asset_class(p, "") is False
    assert rp.allows_asset_class(p, None) is False


def test_to_desk_config_keys_all_exist_in_desk_defaults():
    """A profile key the desk config sanitiser does not know would be dropped
    silently — the profile would look applied and change nothing."""
    from src.desk.config import DEFAULTS
    for p in rp.all_profiles():
        for key in p.to_desk_config():
            assert key in DEFAULTS, f"{key} is not a desk config key"


def test_sized_risk_amount_scales_with_profile():
    eq = 10_000.0
    c = rp.sized_risk_amount(eq, rp.get_profile("CONSERVATIVE"))
    a = rp.sized_risk_amount(eq, rp.get_profile("AGGRESSIVE"))
    assert c == pytest.approx(25.0)
    assert a == pytest.approx(100.0)
    assert c < a


@pytest.mark.parametrize("equity", [0, -1, None, float("nan"), float("inf"), "abc"])
def test_sized_risk_amount_refuses_nonsense_equity(equity):
    """Unknown equity must size to zero, never to a guess."""
    out = rp.sized_risk_amount(equity, rp.get_profile("MODERATE"))
    assert out == 0.0


def test_profile_is_immutable():
    p = rp.get_profile("MODERATE")
    with pytest.raises(Exception):
        p.max_name_pct = 99.0


def test_describe_all_exposes_the_ceiling():
    d = rp.describe_all()
    assert d["default"] == "MODERATE"
    assert len(d["profiles"]) == 3
    assert d["hard_limits"]["max_gross_exposure_pct"] == 100.0
    assert d["executable_asset_classes"] == ["equity", "crypto"]


# ── fail-closed asset class on the exit path ────────────────────────────────

def test_executable_asset_classes_is_the_narrow_truth():
    """Options/futures/commodities are research-only. If this list ever grows,
    the execution path for that class must genuinely exist first."""
    assert rp.EXECUTABLE_ASSET_CLASSES == ("equity", "crypto")


class _FakeBroker:
    """Connected, paper-confirmed, and records anything submitted to it."""
    def __init__(self):
        self.submitted = []
        self.cancelled = []

    def is_connected(self):
        return True

    def submit_order(self, req):
        self.submitted.append(req)
        return OrderResult(broker_order_id="fake-1", status=OrderStatus.FILLED,
                           filled_qty=req.qty, avg_fill_price=100.0)

    def cancel_order(self, oid):
        self.cancelled.append(oid)
        return True


class _FakeMgr:
    def __init__(self, broker):
        self._alpaca = broker

    def open_orders_by_ticker(self):
        return {}


def _drive_close(monkeypatch, asset_class):
    """Run PositionManager._close for a position of `asset_class` against a
    broker that would happily accept the order, and report what happened."""
    from src.desk import position_manager as pm

    broker = _FakeBroker()
    mgr = _FakeMgr(broker)
    pmgr = pm.PositionManager.__new__(pm.PositionManager)
    pmgr.cfg = {}
    # Keep the test off the real data directory and off the network.
    monkeypatch.setattr(pmgr, "_log_execution", lambda rec: None, raising=False)
    monkeypatch.setattr(pmgr, "_log_closed", lambda rec: None, raising=False)
    monkeypatch.setattr("src.desk.notify.push", lambda msg, cfg: None)

    queued = {}
    monkeypatch.setattr(
        pmgr, "_queue_manual_exit",
        lambda ticker, pos, qty, long, reason: queued.update(
            ticker=ticker, qty=qty, reason=reason),
        raising=False)
    # Paper account, so the live-account escape hatch is NOT what we are seeing.
    monkeypatch.setattr("src.execution.live_guard.paper_confirmed",
                        lambda broker: True)

    tracked = {"SPY": {"asset_class": asset_class, "side": "long",
                       "entry_price": 100.0, "qty": 10}}
    bp = {"qty": 10, "side": "long"}
    out = pmgr._close(tracked, "SPY", bp, mgr, reason="stop hit")
    return out, broker, queued


@pytest.mark.parametrize("bad_class", ["option", "futures", "commodity",
                                       "forex", "", "wat"])
def test_unsupported_asset_class_is_queued_not_coerced(monkeypatch, bad_class):
    """An option in the book must never be market-sold as if it were shares.

    The old code coerced any unrecognised class to EQUITY and sent the order;
    `qty` would then have been read as shares rather than contracts.
    """
    out, broker, queued = _drive_close(monkeypatch, bad_class)

    assert broker.submitted == [], f"{bad_class!r} order reached the broker"
    assert queued.get("ticker") == "SPY", "unsupported exit was not queued"
    assert out is not None and "queued" in out["mode"]


def test_supported_asset_class_still_reaches_the_broker(monkeypatch):
    """The guard must not have welded the normal crypto exit shut. Crypto is
    used here because it skips the market-hours deferral."""
    out, broker, queued = _drive_close(monkeypatch, "crypto")

    assert len(broker.submitted) == 1
    assert broker.submitted[0].asset_class == AssetClass.CRYPTO
    assert queued == {}
