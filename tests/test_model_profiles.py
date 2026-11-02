"""
tests/test_model_profiles.py
============================
Per-model limits, typed refusals, the breaker and stream cancellation. No
Ollama: the provider's urlopen is a stub that records the request body.

No profile ships, so the profiled model here is one the owner configured
through ARIA_MODEL_PROFILES.

Pinned:
  * no model is profiled by default, and an ordinary model is sent exactly
    what it was sent before;
  * a profiled model gets its window, a bounded reply, a time cap, thinking
    off, and no num_gpu;
  * a prompt that cannot fit a profiled model is refused before anything is
    sent; an ordinary model is never refused for size;
  * the queue in front of a profiled model is bounded, and so is the wait;
  * overflow, busy and a profiled model's timeout never open the provider
    breaker, and a profiled model's timeout is not retried;
  * discovery never adds a model profiled auto_pick false;
  * primary_for answers after policy, so a refused vendor is never budgeted for;
  * a cancelled stream stops reading and is not retried.
"""
import io
import json
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.inference import model_profiles as mp  # noqa: E402
from src.inference.errors import (Cancelled, ContextOverflow, ModelBusy,  # noqa: E402
                                  ModelTimeout)
from src.inference.providers import ollama as op  # noqa: E402

BIG = "big-test-model"
ORDINARY = "qwen2.5:7b-instruct-q4_K_M"
#: What the owner would configure for a model too big to run like the 7B.
PROFILE = {"num_ctx": 4096, "think": False, "timeout_s": 180, "max_predict": 768,
           "max_waiting": 2, "wait_timeout_s": 90, "refuse_overflow": True,
           "auto_pick": False}


def _profiles(monkeypatch, **overrides):
    monkeypatch.setenv(mp.PROFILES_ENV, json.dumps({BIG: {**PROFILE, **overrides}}))


class _Sent(list):
    state: dict


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


@pytest.fixture
def sent(monkeypatch):
    """Record every body the provider posts; reply with one short answer."""
    bodies = _Sent()
    state = {"delay": 0.0, "lines": None, "raise": None}

    def fake_urlopen(req, timeout=None):
        bodies.append({"body": json.loads(req.data), "timeout": timeout})
        if state["raise"]:
            raise state["raise"]
        if state["delay"]:
            time.sleep(state["delay"])
        if state["lines"] is not None:
            return _Resp(b"\n".join(json.dumps(l).encode() for l in state["lines"]))
        return _Resp(json.dumps({"message": {"content": "ok"}}).encode())

    monkeypatch.setattr(op.urllib.request, "urlopen", fake_urlopen)
    _profiles(monkeypatch)
    mp.reset_for_tests()
    bodies.state = state
    return bodies


def _ask(model, content="hi", **kw):
    return op.OllamaProvider(base_url="http://127.0.0.1:1").complete(
        model, [{"role": "user", "content": content}], **kw)


# ── request shape ────────────────────────────────────────────────────────

def test_no_model_is_profiled_by_default(monkeypatch):
    """The hardening ships without a model: nothing changes for any model
    until the owner configures a profile for it."""
    monkeypatch.delenv(mp.PROFILES_ENV, raising=False)
    assert mp.BUILTIN == {}
    assert mp.profile(ORDINARY) == {} and mp.profile("gemma3:4b") == {}
    assert mp.describe()["profiles"] == {}
    assert isinstance(mp.gate(ORDINARY), mp._NoGate)


def test_an_ordinary_model_is_sent_what_it_was_sent_before(sent):
    _ask(ORDINARY, max_tokens=1024, timeout=90)
    body = sent[0]["body"]
    assert body["options"] == {"temperature": 0.4, "top_p": 0.9,
                               "num_predict": 1024, "num_ctx": op.DEFAULT_NUM_CTX}
    assert "think" not in body
    assert sent[0]["timeout"] == 90


def test_a_profiled_model_gets_its_window_bounds_and_thinking_off(sent):
    _ask(BIG + ":latest", max_tokens=1024, timeout=300)
    body = sent[0]["body"]
    assert body["think"] is False
    assert body["options"]["num_ctx"] == 4096
    assert body["options"]["num_predict"] == 768
    assert "num_gpu" not in body["options"]      # pinning layers defeats eviction
    assert sent[0]["timeout"] == 180              # the profile caps the caller


def test_owner_overrides_merge_over_a_builtin(monkeypatch):
    monkeypatch.setitem(mp.BUILTIN, "shipped-model", {"num_ctx": 4096, "max_predict": 768})
    monkeypatch.setenv(mp.PROFILES_ENV, json.dumps({"shipped-model:latest": {"max_predict": 300}}))
    assert mp.profile("shipped-model") == {"num_ctx": 4096, "max_predict": 300}


def test_unreadable_owner_profiles_change_nothing(monkeypatch):
    monkeypatch.setenv(mp.PROFILES_ENV, "{not json")
    assert mp.profile(BIG) == {}


# ── overflow ─────────────────────────────────────────────────────────────

def test_a_prompt_that_cannot_fit_is_refused_and_nothing_is_sent(sent):
    with pytest.raises(ContextOverflow):
        _ask(BIG, content="x" * 20000, max_tokens=400)
    assert sent == []


def test_an_ordinary_model_is_never_refused_for_size(sent):
    _ask(ORDINARY, content="x" * 20000)
    assert len(sent) == 1


# ── bounded queue ────────────────────────────────────────────────────────

def test_the_queue_is_bounded(sent, monkeypatch):
    _profiles(monkeypatch, max_waiting=1, wait_timeout_s=5)
    sent.state["delay"] = 0.6
    ok, busy = [], []

    def go():
        try:
            _ask(BIG)
            ok.append(1)
        except ModelBusy:
            busy.append(1)

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
        time.sleep(0.05)
    for t in threads:
        t.join(10)
    assert len(ok) == 2 and len(busy) == 2      # one running, one waiting


def test_the_wait_is_bounded(sent, monkeypatch):
    _profiles(monkeypatch, max_waiting=3, wait_timeout_s=0.2)
    sent.state["delay"] = 1.0
    t = threading.Thread(target=_ask, args=(BIG,))
    t.start()
    time.sleep(0.05)
    started = time.monotonic()
    with pytest.raises(ModelBusy):
        _ask(BIG)
    assert time.monotonic() - started < 0.9
    t.join(5)


def test_an_ordinary_model_has_no_gate(sent):
    sent.state["delay"] = 0.3
    threads = [threading.Thread(target=_ask, args=(ORDINARY,)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(sent) == 3


# ── timeouts and the breaker ─────────────────────────────────────────────

def test_a_profiled_timeout_is_model_scoped_and_an_ordinary_one_is_not(sent):
    from src.inference.errors import RetryableError
    sent.state["raise"] = TimeoutError("timed out")
    with pytest.raises(ModelTimeout):
        _ask(BIG)
    with pytest.raises(RetryableError):
        _ask(ORDINARY)


def test_model_scoped_failures_never_open_the_provider_breaker():
    from src.inference.router import InferenceRouter
    calls = {"n": 0}

    class P:
        name, local = "ollama", True

        def complete(self, model, messages, **kw):
            calls["n"] += 1
            raise ModelTimeout("slow", "ollama", model)

    r = InferenceRouter(providers={"ollama": P()}, tiers={"DEEP": [["ollama", BIG]]},
                        breaker_threshold=1)
    r._sleep = lambda s: None
    for _ in range(3):
        with pytest.raises(Exception):
            r.complete("DEEP", [{"role": "user", "content": "x"}])
    assert calls["n"] == 3                       # once per request: never retried
    assert r.breakers["ollama"].state.value == "closed"


def test_an_ordinary_fatal_error_still_opens_the_breaker():
    from src.inference.errors import FatalError
    from src.inference.router import InferenceRouter

    class P:
        name, local = "ollama", True

        def complete(self, model, messages, **kw):
            raise FatalError("model not found", "ollama", model)

    r = InferenceRouter(providers={"ollama": P()}, tiers={"DEEP": [["ollama", ORDINARY]]},
                        breaker_threshold=1)
    r._sleep = lambda s: None
    with pytest.raises(Exception):
        r.complete("DEEP", [{"role": "user", "content": "x"}])
    assert r.breakers["ollama"].state.value != "closed"


# ── tiers ────────────────────────────────────────────────────────────────

def test_discovery_never_adds_a_model_profiled_auto_pick_false(monkeypatch):
    from src.inference import ollama_discovery, router as r
    _profiles(monkeypatch)
    monkeypatch.setattr(ollama_discovery, "registry_candidates", lambda: {
        "FAST": [], "STANDARD": [], "DEEP": [["ollama", BIG + ":latest"],
                                             ["ollama", "qwen3:8b"]]})
    monkeypatch.setattr(r, "TIER_CONFIG_FILE", ROOT / "nonexistent.json")
    deep = r.load_tier_config()["DEEP"]
    assert ["ollama", "qwen3:8b"] in deep
    assert not any(BIG in c[1] for c in deep)


def test_primary_for_answers_after_policy():
    from src.inference.router import InferenceRouter
    vendor = type("A", (), {"local": False, "name": "anthropic"})()
    router = InferenceRouter(providers={"ollama": op.OllamaProvider(), "anthropic": vendor},
                             tiers={"DEEP": [["anthropic", "claude-x"], ["ollama", ORDINARY]]})
    assert router.primary_for("DEEP") == ("ollama", ORDINARY)
    assert router.primary_for("NOPE") is None


# ── streaming ────────────────────────────────────────────────────────────

def test_a_cancelled_stream_stops_reading_and_is_not_retried(sent):
    from src.inference.router import InferenceRouter
    sent.state["lines"] = [{"message": {"content": f"t{i} "}} for i in range(50)] + [
        {"message": {"content": ""}, "done": True}]
    cancel = threading.Event()
    got = []

    def on_token(t):
        got.append(t)
        if len(got) == 3:
            cancel.set()

    router = InferenceRouter(providers={"ollama": op.OllamaProvider(base_url="http://127.0.0.1:1")},
                             tiers={"DEEP": [["ollama", ORDINARY]]})
    with pytest.raises(Cancelled):
        router.complete("DEEP", [{"role": "user", "content": "x"}], on_token=on_token,
                        cancel=cancel)
    assert len(got) == 3 and len(sent) == 1


def test_a_listener_that_raises_cancelled_stops_the_stream():
    def on_token(t):
        raise Cancelled("gone")
    lines = [{"message": {"content": "a"}}, {"message": {"content": "b"}, "done": True}]
    with pytest.raises(Cancelled):
        op._read_stream(io.BytesIO(b"\n".join(json.dumps(l).encode() for l in lines)), on_token)


def test_a_listener_that_fails_otherwise_still_costs_nothing():
    """The rule cancellation sits beside: a broken listener never costs the answer."""
    def on_token(t):
        raise RuntimeError("listener broke")
    lines = [{"message": {"content": "a"}}, {"message": {"content": "b"}, "done": True}]
    out = op._read_stream(io.BytesIO(b"\n".join(json.dumps(l).encode() for l in lines)), on_token)
    assert out["message"]["content"] == "ab"


def test_a_streamed_reply_from_a_profiled_model_has_a_total_deadline(sent, monkeypatch):
    monkeypatch.setenv(mp.PROFILES_ENV, json.dumps({BIG: {"timeout_s": 0.01}}))
    sent.state["lines"] = [{"message": {"content": "a"}}, {"message": {"content": "b"}},
                           {"message": {"content": ""}, "done": True}]
    slow = []

    def on_token(t):
        slow.append(t)
        time.sleep(0.05)
    with pytest.raises(ModelTimeout):
        _ask(BIG, on_token=on_token)
    assert slow == ["a"]
