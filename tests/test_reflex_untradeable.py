"""
tests/test_reflex_untradeable.py
================================
What the reflex lane does with a symbol it can never trade, a quote it cannot
get, and a broker that fails — all offline, with a fake broker.

MEASURED 2026-10-10: data/desk/playbooks.json held 1,300 playbooks, none ever
fired. 222 of them were API3-USD, armed and rejected ("the broker cannot quote
this symbol") once per five-minute management tick: `_arm()` asks whether the
asset CLASS is executable but not whether the broker has already refused to
quote the symbol, so the signal re-arms what the next scan rejects.

That loop is noise, not exposure — the tests below pin that an unquotable
symbol is retired before any quote or order — and stopping it means changing
which playbooks the reflex lane arms. That is a reflex policy change held for
the owner's approval, so the last test is a strict expected-failure: it states
the wanted behaviour, fails today, and must be un-marked when the change lands.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.desk.reflex as reflex      # noqa: E402


class _Broker:
    def __init__(self, quote=None, raises=None):
        self._quote, self._raises = quote, raises
        self.quote_calls: list[str] = []
        self.orders: list = []

    def is_connected(self):
        return True

    def get_quote(self, ticker):
        self.quote_calls.append(ticker)
        if self._raises:
            raise self._raises
        return self._quote or {}

    def submit_order(self, req):                       # must never be reached
        self.orders.append(req)
        raise AssertionError("the reflex lane sent an order in an offline test")


class _Mgr:
    def __init__(self, broker):
        self._alpaca = broker


def _playbook(ticker, *, trigger=0.3372, asset_class="crypto"):
    return {"ticker": ticker, "side": "buy", "trigger_price": trigger,
            "stop": trigger * 0.9, "target": trigger * 1.1, "conviction": 60,
            "debate_id": "", "source": "signal", "status": "armed",
            "armed_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(days=2)).isoformat(),
            "asset_class": asset_class, "thesis": "composite spike +60"}


@pytest.fixture
def lane(tmp_path, monkeypatch):
    """A ReflexEngine over a temporary playbook store and a fake broker."""
    store = tmp_path / "playbooks.json"
    monkeypatch.setattr(reflex, "PLAYBOOKS_FILE", store)
    monkeypatch.setattr(reflex, "load_config", lambda: {
        "reflex_enabled": True, "reflex_min_prob": 0.55,
        "reflex_playbook_expiry_days": 2, "reflex_signal_spike": 40.0})
    monkeypatch.setattr("src.desk.desk_daemon.instrument_open",
                        lambda *a, **k: True)
    fired: list[str] = []

    def setup(playbooks, broker, *, quotable=True, signals=None):
        store.write_text(json.dumps(playbooks), encoding="utf-8")
        import src.desk.auto_executor as ae
        monkeypatch.setattr(ae, "get_order_manager", lambda: _Mgr(broker))
        monkeypatch.setattr(ae, "account_snapshot", lambda: {
            "connected": True, "paper": True, "equity": 10_000.0, "positions": []})
        monkeypatch.setattr("src.execution.alpaca_broker.quotable",
                            lambda t, now=None: quotable)
        monkeypatch.setattr(reflex, "load_data_json", lambda f: signals or {})
        engine = reflex.ReflexEngine({"reflex_enabled": True})
        monkeypatch.setattr(engine, "_fire",
                            lambda pb, price, account, mgr: fired.append(pb["ticker"])
                            or {"executed": False})
        return engine
    setup.fired = fired
    setup.store = store
    return setup


# ── safety: nothing untradeable reaches a quote, let alone an order ─────────

def test_an_unquotable_symbol_is_retired_before_any_quote_or_order(lane):
    broker = _Broker(quote={"mid": 0.40})
    engine = lane([_playbook("API3-USD")], broker, quotable=False)

    assert engine.scan_once() == []

    saved = json.loads(lane.store.read_text(encoding="utf-8"))
    assert saved[0]["status"] == "rejected"
    assert saved[0]["rejected_reason"] == "the broker cannot quote this symbol"
    assert broker.quote_calls == [], "it asked the broker for a quote it knows it cannot give"
    assert lane.fired == [] and broker.orders == []


@pytest.mark.parametrize("ticker", ["NG=F", "KC=F", "BTC=F", "RELIANCE.NS", "TSCO.L"])
def test_a_symbol_this_broker_cannot_trade_is_never_armed(tmp_path, monkeypatch, ticker):
    """Futures are research-only and the reflex broker routes US listings only."""
    monkeypatch.setattr(reflex, "PLAYBOOKS_FILE", tmp_path / "pb.json")
    ok, _, why = reflex.executable(ticker)
    assert ok is False and why
    pb = reflex._arm(ticker, "buy", 10.0, 9.0, 11.0, 60, "", "signal", {},
                     {"reflex_playbook_expiry_days": 2}, "probe")
    assert pb is None and reflex.load_playbooks() == []


def test_an_already_armed_untradeable_playbook_is_retired_with_the_reason(lane):
    broker = _Broker(quote={"mid": 3.0})
    engine = lane([_playbook("NG=F", trigger=2.5, asset_class="equity")], broker)
    engine.scan_once()
    saved = json.loads(lane.store.read_text(encoding="utf-8"))
    assert saved[0]["status"] == "rejected" and "research-only" in saved[0]["rejected_reason"]
    assert broker.quote_calls == [] and lane.fired == []


# ── a quote that is missing or failed is not a trigger ───────────────────────

@pytest.mark.parametrize("quote", [{}, {"mid": 0}, {"mid": None}, {"bid": 1.0}])
def test_a_missing_quote_does_not_fire_and_leaves_the_playbook_armed(lane, quote):
    broker = _Broker(quote=quote)
    engine = lane([_playbook("ETH-USD", trigger=3000.0)], broker)
    assert engine.scan_once() == []
    assert lane.fired == [] and broker.orders == []
    assert json.loads(lane.store.read_text(encoding="utf-8"))[0]["status"] == "armed"


def test_a_broker_failure_does_not_fire_on_the_stale_fallback_price(lane):
    """When the quote call raises, `_quote` falls back to the price in
    signals.json. That price is what the trigger was computed FROM (trigger =
    price * (1 + 0.5 * ATR) for a buy), so it sits below the trigger and
    cannot cross it. Pinned so that fallback can never become a way to fire."""
    broker = _Broker(raises=ConnectionError("alpaca unreachable"))
    signals = {"ETH-USD": {"current_price": 2950.0, "atr_pct": 0.03}}
    engine = lane([_playbook("ETH-USD", trigger=2950.0 * 1.015)], broker, signals=signals)
    assert engine.scan_once() == []
    assert broker.quote_calls == ["ETH-USD"]
    assert lane.fired == [] and broker.orders == []


def test_a_disconnected_broker_scans_nothing(lane, monkeypatch):
    broker = _Broker(quote={"mid": 5000.0})
    monkeypatch.setattr(broker, "is_connected", lambda: False)
    engine = lane([_playbook("ETH-USD", trigger=3000.0)], broker)
    assert engine.scan_once() == []
    assert broker.quote_calls == [] and lane.fired == []


def test_an_expired_playbook_is_retired_not_fired(lane):
    pb = _playbook("ETH-USD", trigger=3000.0)
    pb["expires_at"] = (datetime.now() - timedelta(hours=1)).isoformat()
    broker = _Broker(quote={"mid": 5000.0})
    engine = lane([pb], broker)
    engine.scan_once()
    assert json.loads(lane.store.read_text(encoding="utf-8"))[0]["status"] == "expired"
    assert lane.fired == []


# ── the re-arm loop ───────────────────────────────────────────────────────────
# A symbol the broker refused to quote was re-armed every management tick and
# rejected by the next scan (API3-USD: 222 times). _arm now consults quotable();
# owner-approved arming policy, 2026-10-16.

def test_a_symbol_the_broker_refused_to_quote_is_not_armed_again(
        tmp_path, monkeypatch):
    monkeypatch.setattr(reflex, "PLAYBOOKS_FILE", tmp_path / "pb.json")
    monkeypatch.setattr(reflex, "signals_freshness",
                        lambda: {"fresh": True, "age_hours": 1.0, "why": ""})
    monkeypatch.setattr(reflex, "load_data_json", lambda f: {
        "API3-USD": {"current_price": 0.33, "composite_score": 60.0, "atr_pct": 0.04}})
    monkeypatch.setattr("src.execution.alpaca_broker.quotable",
                        lambda t, now=None: False)
    cfg = {"reflex_signal_spike": 40.0, "reflex_playbook_expiry_days": 2}
    assert reflex.arm_from_signal(cfg, account={"positions": []}) == []
