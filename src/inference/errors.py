"""
src/inference/errors.py
=======================
Typed error classification for the inference router (Automaton-inspired).

retryable  — timeouts, 429s, 5xx, connection resets → exponential backoff,
             then next candidate
fatal      — auth failures, malformed requests → skip STRAIGHT to the next
             candidate, never retried
"""
from __future__ import annotations


class InferenceError(Exception):
    """Base for all provider errors."""
    def __init__(self, message: str, provider: str = "", model: str = ""):
        super().__init__(message)
        self.provider = provider
        self.model = model


class RetryableError(InferenceError):
    """Transient: timeout / 429 / 5xx / connection failure."""


class FatalError(InferenceError):
    """Permanent for this candidate: bad auth, bad request, missing model."""


class AllProvidersFailed(InferenceError):
    """Every candidate in the tier failed. Carries the per-candidate errors."""
    def __init__(self, tier: str, errors: list):
        super().__init__(f"all candidates failed for tier {tier}: "
                         + "; ".join(str(e) for e in errors))
        self.tier = tier
        self.errors = errors


def classify_http(status: int, message: str, provider: str = "",
                  model: str = "") -> InferenceError:
    """HTTP status → typed error. 408/429/5xx retry; 4xx are fatal."""
    if status in (408, 429) or status >= 500:
        return RetryableError(f"HTTP {status}: {message}", provider, model)
    return FatalError(f"HTTP {status}: {message}", provider, model)
