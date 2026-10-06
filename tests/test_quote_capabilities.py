"""
tests/test_quote_capabilities.py
================================
The reflex lane may only arm and poll what its broker can trade and quote.

Found 2026-10-06: ~13,000 Alpaca `get_quote` warnings in one log. The reflex
lane armed signal playbooks for every spiking name in signals.json and
labelled anything not ending in -USD as "equity" — so NG=F, KC=F, BTC=F and
ETH=F (futures, research-only) were polled against Alpaca's stock endpoint
every three seconds, alongside crypto pairs Alpaca's validator refuses
(1INCH, API3: digits). The noise buried every warning that mattered, and a
future mislabelled as equity is one routing change away from an order.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.desk import reflex
from src.execution import alpaca_broker as AB


# ── classification ──────────────────────────────────────────────────────────

def test_futures_are_not_executable_and_not_equity():
    for t in ("NG=F", "KC=F", "BTC=F", "ETH=F"):
        ok, aclass, why = reflex.executable(t)
        assert ok is False
        assert aclass == "futures"
        assert "research-only" in why


def test_non_us_listings_are_not_executable_on_this_broker():
    for t in ("RELIANCE.NS", "BARC.L"):
        ok, aclass, why = reflex.executable(t)
        assert ok is False and aclass == "equity"
        assert "US listings only" in why


def test_indices_and_fx_are_not_executable():
    assert reflex.executable("^GSPC")[0] is False
    assert reflex.executable("GBPUSD=X")[0] is False


def test_us_equities_and_crypto_are_executable():
    assert reflex.executable("AAPL") == (True, "equity", "")
    assert reflex.executable("BTC-USD") == (True, "crypto", "")


# ── arming ──────────────────────────────────────────────────────────────────

def test_a_spiking_future_is_not_armed(tmp_path, monkeypatch):
    monkeypatch.setattr(reflex, "signals_freshness",
                        lambda: {"fresh": True, "age_hours": 1.0, "why": ""})
    monkeypatch.setattr(reflex, "PLAYBOOKS_FILE", tmp_path / "pb.json")
    monkeypatch.setattr(reflex, "load_data_json", lambda f: {
        "NG=F": {"current_price": 3.1, "composite_score": 55.0},
        "RELIANCE.NS": {"current_price": 1400.0, "composite_score": 60.0},
        "NVDA": {"current_price": 500.0, "composite_score": 45.0},
        "BTC-USD": {"current_price": 60000.0, "composite_score": 50.0},
    })
    cfg = {"reflex_signal_spike": 40.0, "reflex_playbook_expiry_days": 2}
    armed = {p["ticker"]: p for p in reflex.arm_from_signal(cfg, account={"positions": []})}
    assert set(armed) == {"NVDA", "BTC-USD"}
    assert armed["NVDA"]["asset_class"] == "equity"
    assert armed["BTC-USD"]["asset_class"] == "crypto"


class _Mgr:
    class _A:
        def __init__(self):
            self.asked = []

        def is_connected(self):
            return True

        def get_quote(self, t):
            self.asked.append(t)
            return {}

    def __init__(self):
        self._alpaca = self._A()


def test_already_armed_unexecutable_playbooks_are_retired(tmp_path, monkeypatch):
    """Playbooks armed under the old labelling are rejected with a reason on
    the next scan — and never quoted."""
    pb_file = tmp_path / "pb.json"
    monkeypatch.setattr(reflex, "PLAYBOOKS_FILE", pb_file)
    base = {"side": "buy", "trigger_price": 1.0, "stop": None, "target": None,
            "conviction": 50, "debate_id": "", "source": "signal",
            "armed_at": "2026-10-06T10:00:00", "expires_at": "2099-01-01T00:00:00",
            "thesis": "", "status": "armed"}
    pb_file.write_text(json.dumps([
        {**base, "ticker": "NG=F", "asset_class": "equity"},
        {**base, "ticker": "API3-USD", "asset_class": "crypto"},
    ]), encoding="utf-8")
    mgr = _Mgr()
    import src.desk.auto_executor as ae
    monkeypatch.setattr(ae, "get_order_manager", lambda: mgr)
    monkeypatch.setattr(reflex, "load_config", lambda: {"reflex_enabled": True})
    eng = reflex.ReflexEngine(cfg={"reflex_enabled": True})
    eng.scan_once()
    after = {p["ticker"]: p for p in json.loads(pb_file.read_text(encoding="utf-8"))}
    assert after["NG=F"]["status"] == "rejected"
    assert "research-only" in after["NG=F"]["rejected_reason"]
    assert after["API3-USD"]["status"] == "rejected"
    assert "cannot quote" in after["API3-USD"]["rejected_reason"]
    assert mgr._alpaca.asked == []


# ── the broker's own guard ──────────────────────────────────────────────────

def test_shapes_alpaca_refuses_are_not_requested():
    for t in ("NG=F", "^GSPC", "GBPUSD=X", "RELIANCE.NS", "API3-USD", "1INCH-USD"):
        assert AB.quotable(t) is False, t
    for t in ("AAPL", "BRK.B", "BTC-USD", "ETH/USD"):
        assert AB.quotable(t) is True, t


def test_a_refused_symbol_is_remembered_then_retried(monkeypatch):
    monkeypatch.setattr(AB, "_unquotable", {})
    assert AB.quotable("STETH-USD", now=1000.0) is True
    monkeypatch.setattr(AB.time, "time", lambda: 1000.0)
    AB._mark_unquotable("STETH-USD", "invalid symbol: STETH/USD")
    assert AB.quotable("STETH-USD", now=1001.0) is False
    assert AB.quotable("STETH-USD", now=1000.0 + AB.UNQUOTABLE_TTL_S + 1) is True


def test_get_quote_does_not_call_alpaca_for_an_unquotable_symbol(monkeypatch):
    b = AB.AlpacaBroker.__new__(AB.AlpacaBroker)
    calls = []

    class Data:
        def get_stock_latest_quote(self, req):
            calls.append(req)
            raise RuntimeError("invalid symbol: KC=F")

    b._stock_data = Data()
    b._crypto_data = Data()
    assert b.get_quote("KC=F") == {}
    assert calls == []


def test_an_invalid_symbol_error_is_logged_once(monkeypatch, caplog):
    monkeypatch.setattr(AB, "_unquotable", {})
    b = AB.AlpacaBroker.__new__(AB.AlpacaBroker)
    calls = []

    class Data:
        def get_stock_latest_quote(self, req):
            calls.append(req)
            raise RuntimeError('{"message":"code=40010001, message=invalid symbol: ZZZZ"}')

    b._stock_data = Data()
    b._crypto_data = Data()
    import logging
    with caplog.at_level(logging.WARNING, logger=AB.logger.name):
        for _ in range(50):
            assert b.get_quote("ZZZZ") == {}
    assert len(calls) == 1
    assert sum("cannot quote ZZZZ" in r.getMessage() for r in caplog.records) == 1


def test_other_errors_are_still_logged_every_time(monkeypatch, caplog):
    """Only 'this symbol does not exist here' is cached. A timeout or an auth
    failure is a real fault and must stay loud."""
    monkeypatch.setattr(AB, "_unquotable", {})
    b = AB.AlpacaBroker.__new__(AB.AlpacaBroker)

    class Data:
        def get_stock_latest_quote(self, req):
            raise TimeoutError("read timed out")

    b._stock_data = Data()
    b._crypto_data = Data()
    import logging
    with caplog.at_level(logging.WARNING, logger=AB.logger.name):
        for _ in range(3):
            b.get_quote("AAPL")
    assert sum("timed out" in r.getMessage() for r in caplog.records) == 3
    assert AB.quotable("AAPL") is True
