"""
src/inference/policy.py
=======================
WHOSE BRAIN, AND WHAT HAPPENS WHEN IT IS DOWN.

Two decisions from docs/ARIA_NEXT_SESSION.md, made executable:

**Decision 1 — her brain is open weights she runs herself.** No Anthropic, no
OpenAI, no Perplexity, no vendor API in the reasoning path. `self_hosted_only()`
is True by default and filters non-local providers out of every tier before
the router sees them. The owner can turn it off deliberately
(`ARIA_ALLOW_VENDOR_BRAIN=1`) — that is his call to make and it is logged when
it happens — but nothing turns it off by accident, and no config file that
ARIA can edit is consulted.

**Decision 4 — a local model was never meant to be a silent fallback.** When
her brain is unreachable she abstains and says so; she never quietly answers
from a weaker model in the same voice. So the router's default degrade policy
is ABSTAIN: the tier's first reachable candidate answers, or `BrainUnreachable`
is raised. A caller that genuinely wants second-best passes
`degrade="disclose"`, and then the answer is tagged `degraded=True` and the
surface has to show it.

The distinction that matters: `abstain` is not "fail closed for safety". It is
"do not produce prose that reads exactly like her when it is not her". A wrong
answer in her voice is worse than no answer, because the voice is the thing the
owner calibrates his trust against.
"""
from __future__ import annotations

import logging
import os

from src.inference.errors import AllProvidersFailed

logger = logging.getLogger(__name__)

#: Degrade policies.
ABSTAIN = "abstain"      # only the tier's first permitted candidate may answer
DISCLOSE = "disclose"    # a later candidate may answer, tagged degraded


class BrainUnreachable(AllProvidersFailed):
    """Her brain did not answer, and no substitute was permitted.

    Subclasses `AllProvidersFailed` so the six existing callers that already
    catch it keep working — this narrows the meaning of a failure, it does not
    add a new failure they must learn about.

    Carries the reasons so a surface can say WHICH thing is down. "ARIA is
    unavailable" sends the owner looking at the wrong machine; "Ollama at
    localhost:11434 refused the connection" does not.
    """
    def __init__(self, tier: str, reasons: list, *, tried: list | None = None):
        super().__init__(tier, reasons)
        self.tier = tier
        self.reasons = reasons
        self.tried = tried or []

    def spoken(self) -> str:
        """What a surface should say. First person, no hedging, no substitute
        answer offered."""
        return ("I can't answer this right now — my reasoning model is "
                "unreachable, and I won't answer from a different one as if it "
                f"were me. ({self.tier} tier: "
                f"{'; '.join(str(r) for r in self.reasons[:2]) or 'no candidate reachable'})")


def self_hosted_only() -> bool:
    """True unless the OWNER has explicitly allowed a vendor brain."""
    return os.environ.get("ARIA_ALLOW_VENDOR_BRAIN", "").strip().lower() not in (
        "1", "true", "yes", "on")


def default_degrade() -> str:
    """ABSTAIN unless overridden. `ARIA_DEGRADE=disclose` lets a whole
    deployment prefer a tagged second-best answer over silence — the mini
    running unattended overnight is the case that might want it."""
    value = os.environ.get("ARIA_DEGRADE", "").strip().lower()
    return DISCLOSE if value == DISCLOSE else ABSTAIN


def filter_candidates(candidates: list, providers: dict) -> tuple[list, list]:
    """Split a tier's candidates into (allowed, refused-by-policy).

    Refusal is by PROVIDER LOCALITY, not by name: a new vendor adapter added
    next year is excluded on the day it is added, without anyone remembering
    to update a blocklist.
    """
    if not self_hosted_only():
        return list(candidates), []
    allowed, refused = [], []
    for candidate in candidates:
        try:
            provider_name, model = candidate[0], candidate[1]
        except (TypeError, IndexError):
            continue
        provider = providers.get(provider_name)
        if provider is not None and getattr(provider, "local", False):
            allowed.append(candidate)
        else:
            refused.append([provider_name, model])
    return allowed, refused


def describe() -> dict:
    """For `/api/inference/status` and the self-state endpoint."""
    return {
        "self_hosted_only": self_hosted_only(),
        "degrade": default_degrade(),
        "explanation": (
            "Her reasoning runs on weights this machine holds. When they are "
            "unreachable she abstains rather than answering from something "
            "else in the same voice."
            if self_hosted_only() else
            "ARIA_ALLOW_VENDOR_BRAIN is set: vendor APIs are permitted in the "
            "reasoning path. This is a deliberate owner override of decision 1."),
    }
