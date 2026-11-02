"""
tests/test_chat_stream.py
=========================
ARIA's chat, streamed. The model is a stub; nothing here needs Ollama.

Pinned:
  * the streamed and blocking routes build THE SAME PROMPT — one preparation
    function, so a fix to one cannot silently miss the other;
  * a trade intent typed in chat is recorded exactly once, whichever route;
  * status lines name steps that actually ran; only the answer streams;
  * `done` carries the same body /api/chat returns, plus measured timings;
  * an abstention arrives as an `error` event with what she would say;
  * the router streams only through providers that can, resets on retry, and
    hands a non-streaming provider's answer over in one piece;
  * Ollama's `thinking` deltas never reach a listener;
  * the endpoint is owner-only.
"""
import io
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

TOKEN = "test-owner-token-for-chat-stream"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
REMOTE = ("203.0.113.9", 54001)


@pytest.fixture
def app(monkeypatch):
    import backend.main as m
    from backend.main import app as fastapi_app
    from src.auth import hardening
    # Measured at 24-28 s cold (vault embedder) and 1-3 s (ticker lookup by
    # network search) per call. Neither is what these tests are about, and
    # both would put four minutes on the suite. Everything else in the
    # preparation runs for real.
    monkeypatch.setattr(m, "_vault_context", lambda msgs, role="free": "")
    monkeypatch.setattr(m, "_ondemand_ticker_context", lambda msgs: "")
    monkeypatch.setenv("ARIA_OWNER_TOKEN", TOKEN)
    monkeypatch.delenv("ARIA_OWNER_TOKEN_HASH", raising=False)
    hardening._limiter._hits.clear()
    yield fastapi_app
    hardening._limiter._hits.clear()


@pytest.fixture
def model(monkeypatch):
    """A router whose DEEP tier streams 'Markets are calm.' in three pieces and
    records every prompt it is given."""
    from src.inference import router as r
    seen = []

    class Result:
        text, model, provider, degraded, primary = "Markets are calm.", "stub", "ollama", False, ""

    class FakeRouter:
        def complete(self, tier, msgs, *, system="", max_tokens=400, timeout=120,
                     on_token=None, on_reset=None, **kw):
            seen.append({"system": system, "msgs": msgs, "streamed": on_token is not None})
            if on_token:
                for piece in ("Markets ", "are ", "calm."):
                    on_token(piece)
            return Result()

    monkeypatch.setattr(r, "get_router", lambda: FakeRouter())
    return seen


def _events(text):
    out, ev = [], None
    for line in text.splitlines():
        if line.startswith("event: "):
            ev = line[7:]
        elif line.startswith("data: ") and ev:
            out.append((ev, json.loads(line[6:])))
            ev = None
    return out


def _stream(client, content="How do markets look today?"):
    r = client.post("/api/chat/stream", headers=BEARER,
                    json={"messages": [{"role": "user", "content": content}]})
    assert r.status_code == 200, r.text
    return _events(r.text)


# ── the stream ───────────────────────────────────────────────────────────────

def test_ack_statuses_tokens_done(app, model):
    ev = _stream(TestClient(app, client=REMOTE))
    kinds = [k for k, _ in ev]
    assert kinds[0] == "ack" and kinds[-1] == "done"
    assert [d["text"] for k, d in ev if k == "token"] == ["Markets ", "are ", "calm."]


def test_status_names_the_steps_that_ran(app, model):
    statuses = [d["text"] for k, d in _stream(TestClient(app, client=REMOTE))
                if k == "status"]
    assert statuses[0] == "Reading the market state…"
    assert "Loading ARIA's world model…" in statuses
    assert statuses[-1] == "Writing the answer…"


def test_done_matches_the_blocking_body_plus_timings(app, model):
    client = TestClient(app, client=REMOTE)
    done = _stream(client)[-1][1]
    blocking = client.post("/api/chat", headers=BEARER, json={
        "messages": [{"role": "user", "content": "How do markets look today?"}]}).json()
    assert set(blocking) <= set(done)
    assert done["content"] == blocking["content"] == "Markets are calm."
    t = done["timing"]
    assert t["ttft"] is not None and t["total"] >= t["ttft"] >= 0
    assert t["tokens_streamed"] == 3


def test_both_routes_build_the_same_prompt(app, model):
    """One preparation function. A context fix applied to one route and missed
    by the other would make the streamed ARIA a different ARIA."""
    client = TestClient(app, client=REMOTE)
    body = {"messages": [{"role": "user", "content": "How do markets look today?"}]}
    client.post("/api/chat", headers=BEARER, json=body)
    client.post("/api/chat/stream", headers=BEARER, json=body)
    blocking, streamed = model[-2], model[-1]
    assert blocking["streamed"] is False and streamed["streamed"] is True
    # Timestamps inside the world model may tick between the two calls; the
    # prompt's structure and the messages must be identical.
    strip = lambda s: [l for l in s.splitlines() if not any(c.isdigit() for c in l)]
    assert strip(blocking["system"]) == strip(streamed["system"])
    assert blocking["msgs"] == streamed["msgs"]


def test_a_trade_intent_is_recorded_once_per_streamed_request(app, model, monkeypatch):
    from src.desk import intents
    recorded = []
    monkeypatch.setattr(intents, "record",
                        lambda parsed, **kw: recorded.append(parsed) or {
                            "id": "i1", "side": "buy", "qty": 20, "ticker": "AAPL",
                            "expires_at": "2026-12-31T00:00:00"})
    monkeypatch.setattr(intents, "parse", lambda said: {"side": "buy", "qty": 20,
                                                        "ticker": "AAPL"} if "buy" in said.lower() else None)
    _stream(TestClient(app, client=REMOTE), "buy 20 AAPL")
    assert len(recorded) == 1


def test_an_abstention_is_an_error_event_with_what_she_would_say(app, monkeypatch):
    from src.inference import router as r
    from src.inference.policy import BrainUnreachable

    class Down:
        def complete(self, *a, **k):
            raise BrainUnreachable("DEEP", ["connection refused"], tried=[])

    monkeypatch.setattr(r, "get_router", lambda: Down())
    ev = _stream(TestClient(app, client=REMOTE))
    assert ev[-1][0] == "error"
    assert ev[-1][1]["status"] == 503 and ev[-1][1]["detail"]


def test_timings_are_recorded(app, model):
    import backend.main as m
    _stream(TestClient(app, client=REMOTE))
    last = json.loads(m.CHAT_STREAM_METRICS.read_text().splitlines()[-1])
    assert last["outcome"] == "done" and last["ttft"] is not None


def test_the_stream_is_owner_only(app, model):
    anon = TestClient(app, client=REMOTE)
    r = anon.post("/api/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401


# ── the router ───────────────────────────────────────────────────────────────

def _router_with(provider):
    from src.inference.router import InferenceRouter
    router = InferenceRouter.__new__(InferenceRouter)
    InferenceRouter.__init__(router)
    router.providers = {"ollama": provider}
    router.tiers = {"DEEP": [["ollama", "m"]]}
    router._sleep = lambda s: None
    return router


def test_router_streams_through_a_streaming_provider():
    class P:
        name, local, streams = "ollama", True, True
        def complete(self, model, messages, *, on_token=None, **kw):
            on_token("a"); on_token("b")
            return "ab"
    got = []
    out = _router_with(P()).complete("DEEP", [{"role": "user", "content": "x"}],
                                     on_token=got.append)
    assert got == ["a", "b"] and out.text == "ab"


def test_router_hands_a_non_streaming_answer_over_in_one_piece():
    class P:
        name, local = "ollama", True           # no `streams`
        def complete(self, model, messages, **kw):
            assert "on_token" not in kw, "never pass it to a provider that cannot stream"
            return "whole"
    got = []
    _router_with(P()).complete("DEEP", [{"role": "user", "content": "x"}],
                               on_token=got.append)
    assert got == ["whole"]


def test_router_resets_the_draft_before_a_retry():
    from src.inference.errors import RetryableError
    calls = {"n": 0}

    class P:
        name, local, streams = "ollama", True, True
        def complete(self, model, messages, *, on_token=None, **kw):
            calls["n"] += 1
            on_token("partial")
            if calls["n"] == 1:
                raise RetryableError("timeout", "ollama", model)
            return "full"
    resets = []
    _router_with(P()).complete("DEEP", [{"role": "user", "content": "x"}],
                               on_token=lambda t: None, on_reset=lambda: resets.append(1))
    assert resets == [1]


# ── the Ollama reader ────────────────────────────────────────────────────────

def test_ollama_reader_never_passes_thinking_on():
    from src.inference.providers.ollama import _read_stream
    lines = [{"message": {"thinking": "private working"}},
             {"message": {"content": "Hel"}}, {"message": {"content": "lo"}},
             {"message": {"content": ""}, "done": True}]
    got = []
    out = _read_stream(io.BytesIO(b"\n".join(json.dumps(l).encode() for l in lines)),
                       got.append)
    assert got == ["Hel", "lo"]
    assert out["message"]["content"] == "Hello"


# ── the prompt budget ────────────────────────────────────────────────────────

def test_the_chat_budget_follows_the_model_that_will_answer(app, model, monkeypatch):
    """A profiled model with a 4096 window budgeted against 8192 is how a
    question gets cut off the end of the prompt. Unprofiled: unchanged."""
    from src.inference import context as ctx, model_profiles as mp, router as r
    from src.inference.providers.ollama import DEFAULT_NUM_CTX
    budgets, real = [], ctx.bound
    monkeypatch.setattr(ctx, "bound", lambda s, msgs, blocks=None, budget=None:
                        budgets.append(budget) or real(s, msgs, blocks, budget))
    inner = r.get_router

    class Answering:
        def complete(self, *a, **k):
            return inner().complete(*a, **k)

        def primary_for(self, tier):
            return ("ollama", "big-test-model")

    monkeypatch.setattr(r, "get_router", lambda: Answering())
    monkeypatch.setenv(mp.PROFILES_ENV, json.dumps(
        {"big-test-model": {"num_ctx": 4096, "max_predict": 768}}))
    _stream(TestClient(app, client=REMOTE))
    assert (budgets[-1].context_tokens, budgets[-1].reply_tokens) == (4096, 768)

    monkeypatch.delenv(mp.PROFILES_ENV)
    _stream(TestClient(app, client=REMOTE))
    assert (budgets[-1].context_tokens, budgets[-1].reply_tokens) == (DEFAULT_NUM_CTX, 1024)


# ── a client that goes away ──────────────────────────────────────────────────

def _asgi_post(app, path, body, hang_up_after, spec="2.3"):
    """POST over raw ASGI, the way uvicorn drives the app. TestClient reads
    the whole stream before returning, so it cannot hang up halfway.

    uvicorn here reports spec 2.3: Starlette listens for http.disconnect. At
    2.4 it stops listening and learns of a hang-up only when a send fails,
    which is what a 2.4 server does, so that is simulated too."""
    import asyncio
    from starlette.requests import ClientDisconnect

    async def run():
        hung_up = asyncio.Event()
        state = {"requested": False}

        async def receive():
            if not state["requested"]:
                state["requested"] = True
                return {"type": "http.request", "body": body, "more_body": False}
            await hung_up.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if hung_up.is_set() and spec >= "2.4":
                raise OSError("the client went away")
            if message["type"] == "http.response.body" and \
                    hang_up_after(message.get("body", b"")):
                hung_up.set()

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": spec},
                 "http_version": "1.1", "method": "POST", "scheme": "http",
                 "path": path, "raw_path": path.encode(), "root_path": "",
                 "query_string": b"", "client": REMOTE, "server": ("testserver", 80),
                 "headers": [(b"host", b"testserver"),
                             (b"content-type", b"application/json"),
                             (b"authorization", f"Bearer {TOKEN}".encode())]}
        try:
            await asyncio.wait_for(app(scope, receive, send), timeout=30)
        except (ClientDisconnect, OSError):
            pass                        # how a 2.4 app reports the hang-up

    asyncio.run(run())


@pytest.mark.parametrize("spec", ["2.3", "2.4"])
def test_a_client_that_hangs_up_stops_the_model(app, monkeypatch, spec):
    """Nobody reading means nothing generating. Before, an abandoned reply
    ran to the end and held the GPU for nobody."""
    import threading
    import time
    from src.inference import router as r
    from src.inference.errors import Cancelled
    stopped, emitted = threading.Event(), []

    class Slow:
        def complete(self, tier, msgs, *, on_token=None, cancel=None, **kw):
            for i in range(500):
                if cancel is not None and cancel.is_set():
                    stopped.set()
                    raise Cancelled("the listener went away")
                on_token(f"t{i} ")
                emitted.append(i)
                time.sleep(0.01)
            raise AssertionError("generated to the end for nobody")

    monkeypatch.setattr(r, "get_router", lambda: Slow())
    seen = []

    def after_three_tokens(chunk):
        if b"event: token" in chunk:
            seen.append(chunk)
        return len(seen) >= 3

    body = json.dumps({"messages": [{"role": "user", "content": "How do markets look?"}]})
    _asgi_post(app, "/api/chat/stream", body.encode(), after_three_tokens, spec=spec)
    assert stopped.wait(10), "the model kept generating after the client left"
    assert len(emitted) < 500
