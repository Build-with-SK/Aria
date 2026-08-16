"""
src/inference/router.py
=======================
Tiered inference router with per-provider circuit breakers
(design ported from Conway-Research/automaton, MIT — idiomatic Python).

Callers request a TIER, not a model:

    from src.inference.router import Tier, get_router
    text = get_router().complete(Tier.STANDARD,
                                 [{"role": "user", "content": "..."}])

Tiers → ordered candidate lists of (provider, model), configurable in
configs/inference_tiers.json. Retryable errors back off exponentially and
then fail over; fatal errors skip straight to the next candidate. A circuit
breaker per provider opens after K consecutive failures, cools down, then
half-open-probes — a dead Ollama or rate-limited API never kills a cycle.

`local_only=True` restricts routing to on-box providers (degraded mode).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from src.inference import policy
from src.inference.errors import (AllProvidersFailed, FatalError,
                                  InferenceError, RetryableError)
from src.inference.providers import Provider

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
TIER_CONFIG_FILE = ROOT / "configs" / "inference_tiers.json"


class Tier(str, Enum):
    FAST = "FAST"           # formatting, small extractions
    STANDARD = "STANDARD"   # analyst prose, debate rounds
    DEEP = "DEEP"           # judge synthesis, exit debates, frontier consults


def _default_tiers() -> dict:
    """Sane defaults; configs/inference_tiers.json overrides, and the B2
    Ollama discovery registry can extend the local fallbacks."""
    try:
        from src.desk.config import load_config
        local_model = load_config().get("llm_model", "qwen2.5-coder:7b")
    except Exception:
        local_model = "qwen2.5-coder:7b"
    frontier = "claude-sonnet-4-6"
    try:
        from src.brain.cognitive.reasoner import _consult_config
        frontier = _consult_config().get("frontier_model", frontier)
    except Exception:
        pass
    return {
        "FAST": [["ollama", local_model]],
        "STANDARD": [["ollama", local_model], ["anthropic", frontier]],
        "DEEP": [["anthropic", frontier], ["ollama", local_model]],
    }


def load_tier_config() -> dict:
    cfg = _default_tiers()
    if TIER_CONFIG_FILE.exists():
        try:
            override = json.loads(TIER_CONFIG_FILE.read_text(encoding="utf-8"))
            for tier, candidates in override.items():
                if tier in cfg and isinstance(candidates, list):
                    cfg[tier] = candidates
        except Exception as e:
            logger.warning(f"inference tier config unreadable: {e}")
    # Extend local fallbacks from the discovery registry when present
    try:
        from src.inference.ollama_discovery import registry_candidates
        extras = registry_candidates()
        for tier, cands in cfg.items():
            known = {tuple(c) for c in cands}
            for c in extras.get(tier, []):
                if tuple(c) not in known:
                    cands.append(list(c))
    except Exception:
        pass
    return cfg


# ── circuit breaker ──────────────────────────────────────────────────────────

class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """K consecutive failures → OPEN for cooldown_s; then one HALF_OPEN
    probe; success closes, failure re-opens. In-process, every transition
    logged."""
    provider: str
    threshold: int = 3
    cooldown_s: float = 60.0
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    opened_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def allow(self, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        with self._lock:
            if self.state == BreakerState.CLOSED:
                return True
            if self.state == BreakerState.OPEN:
                if now - self.opened_at >= self.cooldown_s:
                    self.state = BreakerState.HALF_OPEN
                    logger.info(f"breaker[{self.provider}] open → half-open (probe)")
                    return True
                return False
            return True     # HALF_OPEN: allow the probe

    def record_success(self):
        with self._lock:
            if self.state != BreakerState.CLOSED:
                logger.info(f"breaker[{self.provider}] {self.state.value} → closed")
            self.state = BreakerState.CLOSED
            self.failures = 0

    def record_failure(self, now: float | None = None):
        now = now if now is not None else time.monotonic()
        with self._lock:
            self.failures += 1
            if self.state == BreakerState.HALF_OPEN or self.failures >= self.threshold:
                if self.state != BreakerState.OPEN:
                    logger.warning(f"breaker[{self.provider}] → open "
                                   f"({self.failures} consecutive failures, "
                                   f"cooldown {self.cooldown_s}s)")
                self.state = BreakerState.OPEN
                self.opened_at = now


# ── router ───────────────────────────────────────────────────────────────────

@dataclass
class Completion:
    text: str
    provider: str
    model: str
    tier: str
    attempts: int
    #: True when something OTHER than the tier's first choice answered. Only
    #: reachable under degrade="disclose"; the surface that shows this text is
    #: expected to show the flag too. A weaker model answering in her voice
    #: with nothing said about it is the thing decision 4 forbids.
    degraded: bool = False
    #: The candidate that was supposed to answer, when it did not.
    primary: str = ""


class InferenceRouter:
    def __init__(self, providers: dict[str, Provider] | None = None,
                 tiers: dict | None = None,
                 max_retries: int = 2, backoff_base_s: float = 0.5,
                 breaker_threshold: int = 3, breaker_cooldown_s: float = 60.0):
        if providers is None:
            from src.inference.providers.anthropic import AnthropicProvider
            from src.inference.providers.ollama import OllamaProvider
            providers = {"ollama": OllamaProvider(), "anthropic": AnthropicProvider()}
        self.providers = providers
        self.tiers = tiers or load_tier_config()
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.breakers = {name: CircuitBreaker(name, breaker_threshold,
                                              breaker_cooldown_s)
                         for name in providers}

    def complete(self, tier: Tier | str, messages: list[dict], *,
                 system: str = "", max_tokens: int = 400,
                 temperature: float = 0.4, timeout: float = 120.0,
                 local_only: bool = False,
                 degrade: str | None = None) -> Completion:
        tier_name = tier.value if isinstance(tier, Tier) else str(tier).upper()
        candidates = list(self.tiers.get(tier_name) or [])
        return self._run(tier_name, candidates, messages, system=system,
                         max_tokens=max_tokens, temperature=temperature,
                         timeout=timeout, local_only=local_only,
                         degrade=degrade)

    def _run(self, tier_name: str, candidates: list, messages: list[dict], *,
             system: str = "", max_tokens: int = 400,
             temperature: float = 0.4, timeout: float = 120.0,
             local_only: bool = False,
             degrade: str | None = None) -> Completion:
        # ── the two standing decisions, applied before anything is called ──
        # 1. Her brain is weights she runs herself: vendor providers are
        #    filtered out of every tier, by locality rather than by name, so a
        #    provider added next year is excluded on the day it is added.
        # 2. A weaker model is never a silent stand-in: under the default
        #    ABSTAIN policy only the tier's first permitted candidate may
        #    answer. If it cannot, she says so.
        degrade = (degrade or policy.default_degrade()).lower()
        permitted, refused = policy.filter_candidates(candidates, self.providers)
        if refused:
            logger.info("router[%s] policy refused %s — her reasoning path is "
                        "self-hosted (ARIA_ALLOW_VENDOR_BRAIN to override)",
                        tier_name, [f"{p}/{m}" for p, m in refused])
        if not permitted:
            raise policy.BrainUnreachable(
                tier_name,
                [f"no self-hosted candidate is configured for this tier "
                 f"(refused by policy: {[f'{p}/{m}' for p, m in refused]})"
                 if refused else "no candidate is configured for this tier"],
                tried=refused)

        primary = f"{permitted[0][0]}/{permitted[0][1]}"
        to_try = permitted[:1] if degrade == policy.ABSTAIN else permitted

        errors: list[InferenceError] = []
        attempts = 0
        for index, (provider_name, model) in enumerate(to_try):
            provider = self.providers.get(provider_name)
            if provider is None:
                continue
            if local_only and not provider.local:
                continue
            breaker = self.breakers.setdefault(provider_name,
                                               CircuitBreaker(provider_name))
            if not breaker.allow():
                errors.append(RetryableError("circuit open", provider_name, model))
                continue
            for attempt in range(self.max_retries + 1):
                attempts += 1
                try:
                    text = provider.complete(
                        model, messages, system=system, max_tokens=max_tokens,
                        temperature=temperature, timeout=timeout)
                    breaker.record_success()
                    if index > 0:
                        logger.warning(
                            "router[%s] answered by %s/%s, not %s — the caller "
                            "must disclose this", tier_name, provider_name,
                            model, primary)
                    return Completion(text=text, provider=provider_name,
                                      model=model, tier=tier_name,
                                      attempts=attempts, degraded=index > 0,
                                      primary=primary if index > 0 else "")
                except FatalError as e:
                    logger.warning(f"router[{tier_name}] fatal from "
                                   f"{provider_name}/{model}: {e}")
                    breaker.record_failure()
                    errors.append(e)
                    break               # straight to the next candidate
                except RetryableError as e:
                    breaker.record_failure()
                    errors.append(e)
                    if attempt < self.max_retries and breaker.allow():
                        delay = self.backoff_base_s * (2 ** attempt)
                        logger.info(f"router[{tier_name}] retry "
                                    f"{provider_name}/{model} in {delay:.1f}s: {e}")
                        self._sleep(delay)
                    else:
                        break
        # Abstention, not failure-with-a-substitute. The caller is expected to
        # say she cannot answer rather than to reach for something else.
        raise policy.BrainUnreachable(tier_name, errors,
                                      tried=[list(c) for c in to_try])

    def complete_with(self, provider_name: str, model: str,
                      messages: list[dict], *, system: str = "",
                      max_tokens: int = 400, temperature: float = 0.4,
                      timeout: float = 120.0) -> Completion:
        """Route ONE explicit (provider, model) candidate through the same
        breaker + retry machinery — for callers that let the user pick a
        model. Audit M1: candidates are passed directly, no shared tier-table
        mutation, so concurrent brain/desk calls can no longer race."""
        return self._run("_EXPLICIT", [[provider_name, model]], messages,
                         system=system, max_tokens=max_tokens,
                         temperature=temperature, timeout=timeout)

    def _sleep(self, seconds: float):
        time.sleep(seconds)     # patchable in tests

    def status(self) -> dict:
        permitted = {}
        for tier, candidates in self.tiers.items():
            allowed, refused = policy.filter_candidates(candidates, self.providers)
            permitted[tier] = {"permitted": allowed, "refused_by_policy": refused,
                               "primary": (f"{allowed[0][0]}/{allowed[0][1]}"
                                           if allowed else None)}
        return {
            "tiers": self.tiers,
            "policy": policy.describe(),
            "routing": permitted,
            "breakers": {n: {"state": b.state.value, "failures": b.failures}
                         for n, b in self.breakers.items()},
        }


_router: InferenceRouter | None = None
_router_lock = threading.Lock()


def get_router() -> InferenceRouter:
    global _router
    with _router_lock:
        if _router is None:
            _router = InferenceRouter()
        return _router
