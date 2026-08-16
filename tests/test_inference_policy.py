"""
tests/test_inference_policy.py
==============================
Decisions 1 and 4 of docs/ARIA_NEXT_SESSION.md, enforced in the router.

  1. Her brain is open weights she runs herself. No vendor API in the
     reasoning path.
  4. A local model was never meant to be a silent fallback. When her brain is
     unreachable she abstains and says so.

Both are settings a future session could flip back with one line, so both have
tests. The second is the subtler one: "it still answered" looks like success
in every log and every UI, which is exactly why it needs an assertion rather
than a convention.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.inference import policy
from src.inference.errors import AllProvidersFailed, RetryableError
from src.inference.providers import Provider
from src.inference.router import InferenceRouter, Tier

MSG = [{"role": "user", "content": "what do you make of NVDA?"}]


class MockProvider(Provider):
    def __init__(self, name, script, local=False):
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
    r = InferenceRouter(providers=providers, tiers=tiers, backoff_base_s=0.01, **kw)
    r._sleep = lambda s: None
    return r


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """The policy reads the environment, and a stray override in the shell
    that runs the tests would quietly disable the thing under test."""
    monkeypatch.delenv("ARIA_ALLOW_VENDOR_BRAIN", raising=False)
    monkeypatch.delenv("ARIA_DEGRADE", raising=False)


# ── decision 1: no vendor API in the reasoning path ─────────────────────────

def test_self_hosted_only_is_the_default():
    assert policy.self_hosted_only() is True
    assert policy.default_degrade() == policy.ABSTAIN


def test_a_vendor_candidate_is_never_called():
    local = MockProvider("ollama", ["her answer"], local=True)
    cloud = MockProvider("anthropic", ["a vendor's answer"])
    r = make_router({"ollama": local, "anthropic": cloud},
                    {"DEEP": [["anthropic", "claude"], ["ollama", "qwen"]]})
    c = r.complete(Tier.DEEP, MSG)
    assert c.provider == "ollama"
    assert cloud.calls == []          # not called even though it is listed first


def test_a_tier_with_only_vendor_candidates_abstains():
    """Not 'falls back to something' — there is nothing to fall back to that
    she is allowed to speak with."""
    cloud = MockProvider("anthropic", ["a vendor's answer"])
    r = make_router({"anthropic": cloud}, {"DEEP": [["anthropic", "claude"]]})
    with pytest.raises(policy.BrainUnreachable) as ei:
        r.complete(Tier.DEEP, MSG)
    assert cloud.calls == []
    assert "refused by policy" in str(ei.value)


def test_filtering_is_by_locality_not_by_a_blocklist():
    """A vendor adapter added next year is excluded on the day it is added,
    without anyone remembering to update a list of names."""
    future = MockProvider("some-new-vendor-2027", ["answer"], local=False)
    allowed, refused = policy.filter_candidates(
        [["some-new-vendor-2027", "m"]], {"some-new-vendor-2027": future})
    assert allowed == []
    assert refused == [["some-new-vendor-2027", "m"]]


def test_the_owner_can_override_deliberately(monkeypatch):
    """His call to make — but nothing makes it by accident, and no file ARIA
    can edit is consulted."""
    monkeypatch.setenv("ARIA_ALLOW_VENDOR_BRAIN", "1")
    assert policy.self_hosted_only() is False
    cloud = MockProvider("anthropic", ["a vendor's answer"])
    r = make_router({"anthropic": cloud}, {"DEEP": [["anthropic", "claude"]]})
    assert r.complete(Tier.DEEP, MSG).provider == "anthropic"


def test_the_override_is_visible_in_status(monkeypatch):
    assert "weights this machine holds" in policy.describe()["explanation"]
    monkeypatch.setenv("ARIA_ALLOW_VENDOR_BRAIN", "true")
    assert "deliberate owner override" in policy.describe()["explanation"]


# ── decision 4: no silent fallback ──────────────────────────────────────────

def test_a_second_local_model_does_not_answer_by_default():
    """The whole point. A weaker model answering in the same voice, with the
    log line reading 'completed', is the failure decision 4 names."""
    primary = MockProvider("ollama", [RetryableError("model not loaded")], local=True)
    weaker = MockProvider("ollama_small", ["a smaller model's answer"], local=True)
    r = make_router({"ollama": primary, "ollama_small": weaker},
                    {"STANDARD": [["ollama", "qwen7b"], ["ollama_small", "gemma4b"]]},
                    max_retries=0)
    with pytest.raises(policy.BrainUnreachable):
        r.complete(Tier.STANDARD, MSG)
    assert weaker.calls == []


def test_abstention_says_which_thing_is_down():
    """'ARIA is unavailable' sends the owner to the wrong machine."""
    primary = MockProvider(
        "ollama", [RetryableError("connection failed: [Errno 111] refused")],
        local=True)
    r = make_router({"ollama": primary}, {"STANDARD": [["ollama", "qwen7b"]]},
                    max_retries=0)
    with pytest.raises(policy.BrainUnreachable) as ei:
        r.complete(Tier.STANDARD, MSG)
    spoken = ei.value.spoken()
    assert "won't answer from a different one" in spoken
    assert "refused" in spoken          # the actual cause reaches the surface
    assert ei.value.tried == [["ollama", "qwen7b"]]


def test_abstention_is_still_an_all_providers_failed():
    """The six existing callers catch AllProvidersFailed. This narrows what a
    failure MEANS; it does not add an exception they have to learn about."""
    primary = MockProvider("ollama", [RetryableError("down")], local=True)
    r = make_router({"ollama": primary}, {"FAST": [["ollama", "m"]]}, max_retries=0)
    with pytest.raises(AllProvidersFailed):
        r.complete(Tier.FAST, MSG)


def test_disclose_lets_a_second_model_answer_but_flags_it():
    primary = MockProvider("ollama", [RetryableError("busy")], local=True)
    weaker = MockProvider("ollama_small", ["a smaller model's answer"], local=True)
    r = make_router({"ollama": primary, "ollama_small": weaker},
                    {"STANDARD": [["ollama", "qwen7b"], ["ollama_small", "gemma4b"]]},
                    max_retries=0)
    c = r.complete(Tier.STANDARD, MSG, degrade="disclose")
    assert c.text == "a smaller model's answer"
    assert c.degraded is True
    assert c.primary == "ollama/qwen7b"     # the surface can say what was missed


def test_the_first_choice_answering_is_never_flagged_degraded():
    """A flag that fires on the happy path is a flag that gets ignored."""
    primary = MockProvider("ollama", ["her answer"], local=True)
    r = make_router({"ollama": primary}, {"STANDARD": [["ollama", "qwen7b"]]})
    c = r.complete(Tier.STANDARD, MSG, degrade="disclose")
    assert c.degraded is False and c.primary == ""


def test_a_deployment_can_prefer_disclosure(monkeypatch):
    """The mini running unattended overnight might rather have a tagged
    second-best answer than silence. That is a deployment decision, set in the
    environment, not something a caller drifts into."""
    monkeypatch.setenv("ARIA_DEGRADE", "disclose")
    assert policy.default_degrade() == policy.DISCLOSE
    primary = MockProvider("ollama", [RetryableError("busy")], local=True)
    weaker = MockProvider("ollama_small", ["smaller"], local=True)
    r = make_router({"ollama": primary, "ollama_small": weaker},
                    {"STANDARD": [["ollama", "a"], ["ollama_small", "b"]]},
                    max_retries=0)
    assert r.complete(Tier.STANDARD, MSG).degraded is True


def test_an_unrecognised_degrade_value_falls_back_to_abstaining(monkeypatch):
    monkeypatch.setenv("ARIA_DEGRADE", "whatever")
    assert policy.default_degrade() == policy.ABSTAIN


# ── what the status endpoint reports ────────────────────────────────────────

def test_status_names_the_primary_and_what_was_refused():
    local = MockProvider("ollama", ["x"], local=True)
    cloud = MockProvider("anthropic", ["y"])
    r = make_router({"ollama": local, "anthropic": cloud},
                    {"DEEP": [["anthropic", "claude"], ["ollama", "qwen"]]})
    status = r.status()
    assert status["policy"]["self_hosted_only"] is True
    assert status["routing"]["DEEP"]["primary"] == "ollama/qwen"
    assert status["routing"]["DEEP"]["refused_by_policy"] == [["anthropic", "claude"]]
