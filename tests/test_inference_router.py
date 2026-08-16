"""B1 — router tests with mocked providers: retry→failover order, circuit
open/recover, fatals not retried, tier candidate order."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.inference.errors import (AllProvidersFailed, FatalError,
                                  RetryableError, classify_http)
from src.inference.providers import Provider
from src.inference.router import (BreakerState, CircuitBreaker,
                                  InferenceRouter, Tier)


class MockProvider(Provider):
    def __init__(self, name, script, local=False):
        """script: list of responses; a str returns, an Exception raises.
        The last item repeats once exhausted."""
        self.name = name
        self.local = local
        self.script = list(script)
        self.calls = []

    def complete(self, model, messages, **kw):
        self.calls.append(model)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


def make_router(providers, tiers, **kw):
    r = InferenceRouter(providers=providers, tiers=tiers,
                        backoff_base_s=0.01, **kw)
    r._sleep = lambda s: None      # no real sleeping in tests
    return r


MSG = [{"role": "user", "content": "hi"}]


def test_happy_path_first_candidate():
    ollama = MockProvider("ollama", ["local answer"], local=True)
    cloud = MockProvider("anthropic", ["cloud answer"])
    r = make_router({"ollama": ollama, "anthropic": cloud},
                    {"STANDARD": [["ollama", "m1"], ["anthropic", "c1"]]})
    c = r.complete(Tier.STANDARD, MSG)
    assert c.text == "local answer" and c.provider == "ollama"
    assert cloud.calls == []                      # candidate order respected


# Failover is now opt-in: `degrade="disclose"`. Under the default ABSTAIN
# policy a second candidate never answers at all (see test_inference_policy.py),
# so these machinery tests ask for disclosure explicitly and use two LOCAL
# providers — a vendor candidate would be filtered out before the machinery
# was reached, and would be testing the policy instead of the retry loop.

def test_retryable_retries_then_fails_over():
    ollama = MockProvider("ollama", [RetryableError("timeout")], local=True)
    backup = MockProvider("ollama_b", ["backup answer"], local=True)
    r = make_router({"ollama": ollama, "ollama_b": backup},
                    {"STANDARD": [["ollama", "m1"], ["ollama_b", "m2"]]},
                    max_retries=2)
    c = r.complete(Tier.STANDARD, MSG, degrade="disclose")
    assert c.provider == "ollama_b"
    assert len(ollama.calls) == 3                 # 1 try + 2 retries, then failover
    assert c.degraded and c.primary == "ollama/m1"


def test_fatal_not_retried():
    ollama = MockProvider("ollama", [FatalError("bad auth")], local=True)
    backup = MockProvider("ollama_b", ["backup answer"], local=True)
    r = make_router({"ollama": ollama, "ollama_b": backup},
                    {"DEEP": [["ollama", "m1"], ["ollama_b", "m2"]]},
                    max_retries=3)
    c = r.complete(Tier.DEEP, MSG, degrade="disclose")
    assert c.provider == "ollama_b"
    assert len(ollama.calls) == 1                 # straight to next candidate


def test_all_fail_raises_with_errors():
    ollama = MockProvider("ollama", [RetryableError("down")], local=True)
    r = make_router({"ollama": ollama},
                    {"FAST": [["ollama", "m1"]]}, max_retries=1)
    with pytest.raises(AllProvidersFailed) as ei:
        r.complete(Tier.FAST, MSG)
    assert ei.value.tier == "FAST" and ei.value.errors


def test_local_only_skips_remote():
    cloud = MockProvider("anthropic", ["cloud answer"])
    ollama = MockProvider("ollama", ["local answer"], local=True)
    r = make_router({"ollama": ollama, "anthropic": cloud},
                    {"DEEP": [["anthropic", "c1"], ["ollama", "m1"]]})
    c = r.complete(Tier.DEEP, MSG, local_only=True)
    assert c.provider == "ollama" and cloud.calls == []


def test_breaker_opens_and_recovers():
    b = CircuitBreaker("p", threshold=3, cooldown_s=10)
    for _ in range(3):
        assert b.allow(now=0)
        b.record_failure(now=0)
    assert b.state == BreakerState.OPEN
    assert not b.allow(now=5)                     # still cooling down
    assert b.allow(now=11)                        # half-open probe allowed
    assert b.state == BreakerState.HALF_OPEN
    b.record_success()
    assert b.state == BreakerState.CLOSED and b.failures == 0


def test_half_open_failure_reopens():
    b = CircuitBreaker("p", threshold=1, cooldown_s=10)
    b.record_failure(now=0)
    assert b.state == BreakerState.OPEN
    assert b.allow(now=11)
    b.record_failure(now=11)
    assert b.state == BreakerState.OPEN and b.opened_at == 11


def test_open_breaker_skips_provider_entirely():
    ollama = MockProvider("ollama", [RetryableError("down")], local=True)
    backup = MockProvider("ollama_b", ["backup answer"], local=True)
    r = make_router({"ollama": ollama, "ollama_b": backup},
                    {"STANDARD": [["ollama", "m1"], ["ollama_b", "m2"]]},
                    max_retries=0, breaker_threshold=1)
    r.complete(Tier.STANDARD, MSG, degrade="disclose")   # opens the ollama breaker
    ollama.calls.clear()
    c = r.complete(Tier.STANDARD, MSG, degrade="disclose")
    assert c.provider == "ollama_b" and ollama.calls == []


def test_complete_with_explicit_candidate():
    ollama = MockProvider("ollama", ["picked model answer"], local=True)
    r = make_router({"ollama": ollama}, {"STANDARD": [["ollama", "default"]]})
    c = r.complete_with("ollama", "user-picked:3b", MSG)
    assert c.text == "picked model answer"
    assert ollama.calls == ["user-picked:3b"]
    assert "_EXPLICIT" not in r.tiers             # no tier-table leak


def test_classify_http():
    assert isinstance(classify_http(429, "rate"), RetryableError)
    assert isinstance(classify_http(500, "boom"), RetryableError)
    assert isinstance(classify_http(401, "auth"), FatalError)
    assert isinstance(classify_http(404, "no model"), FatalError)
