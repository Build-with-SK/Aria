"""
tests/test_specialist.py
========================
ARIA answering as the specialist SENTINEL consults — the structured contract.

Pinned: every field says where it came from; confidence is never invented;
stale data and unfollowed structure raise `needs_verification` with reasons; the
service credential reaches this and nothing new besides; no vault, no intent.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

OWNER = "owner-token-for-specialist-tests"
SERVICE = "service-token-for-specialist-tests"
REMOTE = ("203.0.113.9", 55001)

GOOD = """ANALYSIS:
AAPL's composite signal is Buy as of the signals file date.

ASSUMPTIONS:
- the signals file is current
- no earnings surprise this week

RISKS:
- momentum can reverse quickly
"""


@pytest.fixture
def app(monkeypatch):
    import backend.main as m
    from src.auth import hardening
    monkeypatch.setenv("ARIA_OWNER_TOKEN", OWNER)
    monkeypatch.setenv("ARIA_SERVICE_TOKEN", SERVICE)
    monkeypatch.delenv("ARIA_OWNER_TOKEN_HASH", raising=False)
    monkeypatch.setattr(m, "_ollama_available", lambda: True)
    monkeypatch.setattr(m, "_ondemand_ticker_context", lambda msgs: "")
    monkeypatch.setattr(m, "_build_market_context", lambda: "MARKET STATE (dated)")
    hardening._limiter._hits.clear()
    return m


def _model(monkeypatch, text, seen=None):
    from src.inference import router as r

    class Res:
        pass

    class R:
        def complete_with(self, provider, model, msgs, *, system="", **kw):
            if seen is not None:
                seen.append({"provider": provider, "model": model, "system": system})
            out = Res(); out.text = text; out.model = model
            return out
    monkeypatch.setattr(r, "get_router", lambda: R())


def _ask(m, q, token=SERVICE):
    c = TestClient(m.app, client=REMOTE)
    return c.post("/api/specialist/analysis", json={"question": q},
                  headers={"Authorization": f"Bearer {token}"})


def test_the_contract_for_a_named_ticker(app, monkeypatch):
    _model(monkeypatch, GOOD)
    monkeypatch.setattr(app, "_load_optional", lambda f: {"AAPL": {
        "action": "Buy", "confidence": "High", "composite_score": 41.3,
        "bullish_prob": 0.62, "bearish_prob": 0.2, "risk_level": "Medium"}}
        if f == "signals.json" else {})
    r = _ask(app, "Is AAPL a buy right now?")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "success"
    assert d["analysis"].startswith("AAPL's composite signal")
    assert d["assumptions"] == ["the signals file is current", "no earnings surprise this week"]
    assert d["risks"] == ["momentum can reverse quickly"]
    assert d["stated_by_model"] == ["analysis", "assumptions", "risks"]
    assert d["signal"]["confidence_label"] == "High"
    assert d["signal"]["bullish_prob"] == 0.62
    assert {s["name"] for s in d["sources"]} >= {"signals.json", "macro_data.json"}


def test_confidence_is_never_invented(app, monkeypatch):
    """The engine's confidence is a label. It is reported as a label, and the
    numeric field stays null — converting "High" into 0.8 would be making a
    number up."""
    _model(monkeypatch, GOOD)
    d = _ask(app, "What does the macro picture look like?").json()
    assert d["confidence"] is None
    assert "no calibrated confidence" in d["confidence_basis"]


def test_stale_data_requires_verification(app, monkeypatch):
    _model(monkeypatch, GOOD)
    monkeypatch.setattr(app, "_source_age", lambda f, k: ("90 days old, STALE", True))
    d = _ask(app, "What does the macro picture look like?").json()
    assert d["needs_verification"] is True
    assert any("stale" in x for x in d["verification_reasons"])


def test_an_unstructured_answer_is_flagged_not_dressed_up(app, monkeypatch):
    _model(monkeypatch, "Markets look fine to me.")
    d = _ask(app, "How do markets look?").json()
    assert d["analysis"] == "Markets look fine to me."
    assert d["assumptions"] == [] and d["risks"] == []
    assert d["needs_verification"] is True


def test_model_unreachable_is_an_honest_status(app, monkeypatch):
    monkeypatch.setattr(app, "_ollama_available", lambda: False)
    d = _ask(app, "How do markets look?").json()
    assert d["status"] == "unavailable" and d["analysis"] == ""
    assert d["needs_verification"] is True


def test_the_service_peer_gets_the_pinned_small_model_and_no_vault(app, monkeypatch):
    seen = []
    _model(monkeypatch, GOOD, seen)
    called = []
    monkeypatch.setattr(app, "_vault_context", lambda *a, **k: called.append(1) or "SECRET NOTES")
    _ask(app, "How do markets look?")
    assert seen[0]["provider"] == "ollama" and seen[0]["model"] == app.FREE_MODEL
    assert "SECRET NOTES" not in seen[0]["system"]


def test_no_trade_intent_is_ever_recorded(app, monkeypatch):
    from src.desk import intents
    rec = []
    monkeypatch.setattr(intents, "record", lambda *a, **k: rec.append(1))
    _model(monkeypatch, GOOD)
    _ask(app, "buy 20 AAPL")
    assert rec == []


def test_anonymous_callers_are_refused(app, monkeypatch):
    _model(monkeypatch, GOOD)
    c = TestClient(app.app, client=REMOTE)
    assert c.post("/api/specialist/analysis", json={"question": "x"}).status_code == 401


def test_the_service_credential_still_cannot_reach_the_broker(app):
    c = TestClient(app.app, client=REMOTE)
    h = {"Authorization": f"Bearer {SERVICE}"}
    assert c.post("/api/execute/approve/x", headers=h).status_code == 403
    assert c.post("/api/chat", headers=h, json={"messages": []}).status_code == 403
