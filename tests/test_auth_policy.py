"""
Access policy: who may reach what.

These tests are deliberately blunt about the three things that are never a
tier. If one of them ever starts passing for a free user, that is a disclosure
of the owner's notes, positions or broker — not a failing assertion.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.auth import policy  # noqa: E402


# ── role resolution ──────────────────────────────────────────────────────────

def test_loopback_is_owner_when_no_token_configured(monkeypatch):
    monkeypatch.delenv("ARIA_OWNER_TOKEN", raising=False)
    assert policy.resolve_role({}, "127.0.0.1") == policy.OWNER


def test_remote_without_a_session_is_anonymous(monkeypatch):
    """No session and not loopback means not signed in — and anonymous callers
    get nothing but the sign-in routes."""
    monkeypatch.delenv("ARIA_OWNER_TOKEN", raising=False)
    assert policy.resolve_role({}, "192.168.1.55") == policy.ANON


def test_token_beats_loopback(monkeypatch):
    """Once a token exists, being on localhost is not enough — otherwise
    anything running on the mini itself would inherit owner rights."""
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "s3cret")
    assert policy.resolve_role({}, "127.0.0.1") == policy.ANON
    hdr = {"authorization": "Bearer s3cret"}
    assert policy.resolve_role(hdr, "10.0.0.9") == policy.OWNER


def test_wrong_and_partial_tokens_get_no_access(monkeypatch):
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "s3cret")
    # A prefix, a case variant and a mangled scheme must all fail. Case matters
    # especially: tokens are compared as bytes, not case-folded. With no session
    # to fall back on, a failed token is anonymous rather than demoted to free.
    for bad in ("", "s3cre", "s3crett", "S3CRET", "bearer s3cret"):
        assert policy.resolve_role({"x-aria-token": bad}, "1.2.3.4") == policy.ANON


def test_surrounding_whitespace_is_trimmed_not_rejected(monkeypatch):
    """HTTP permits optional whitespace around a header value (RFC 7230 OWS),
    so " s3cret " IS the token s3cret. Rejecting it would fail legitimate
    clients without excluding any attacker — anyone who has the padded string
    already has the token."""
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "s3cret")
    assert policy.resolve_role({"x-aria-token": "  s3cret  "}, "1.2.3.4") == policy.OWNER


def test_x_aria_token_header_works(monkeypatch):
    monkeypatch.setenv("ARIA_OWNER_TOKEN", "s3cret")
    assert policy.resolve_role({"x-aria-token": "s3cret"}, "1.2.3.4") == policy.OWNER


# ── the three that are never a tier ──────────────────────────────────────────

OWNER_ONLY = [
    "/api/vault/search", "/api/vault/note", "/api/vault/reindex",
    "/api/execute/approve/abc123", "/api/execute/propose", "/api/execute/queue",
    "/api/portfolio", "/api/portfolio/summary",
    "/api/desk/auto-execute", "/api/desk/state", "/api/desk/debates",
    "/api/brain/status", "/api/brain/consult", "/api/brain/start",
    "/api/fx/gbpinr", "/api/fx/gbpinr/targets",
    "/api/chat",
    "/api/report", "/api/performance", "/api/stats", "/api/run",
    "/api/inference/models",
]


@pytest.mark.parametrize("path", OWNER_ONLY)
def test_free_user_is_denied(path):
    assert policy.is_allowed(path, policy.FREE) is False, path


@pytest.mark.parametrize("path", OWNER_ONLY)
def test_owner_is_allowed(path):
    assert policy.is_allowed(path, policy.OWNER) is True, path


# ── what free users legitimately get ─────────────────────────────────────────

FREE_OK = [
    "/health",
    "/api/v5/analyse/AAPL", "/api/v5/modules",
    "/api/universe/search", "/api/universe/news/AAPL",
    "/api/quote/live/AAPL",
    "/api/technical/AAPL", "/api/signals", "/api/macro",
    "/api/quant/strategy/iron_condor", "/api/summary",
    "/api/chat/local",
]


@pytest.mark.parametrize("path", FREE_OK)
def test_free_user_gets_research(path):
    assert policy.is_allowed(path, policy.FREE) is True, path


def test_chat_local_is_not_shadowed_by_chat():
    """/api/chat is owner-only and /api/chat/local is public. Prefix matching
    must not let the shorter, denied path swallow the longer, allowed one."""
    assert policy.is_allowed("/api/chat", policy.FREE) is False
    assert policy.is_allowed("/api/chat/local", policy.FREE) is True


def test_unknown_paths_default_to_deny():
    """The whole point of an allowlist: a route added tomorrow is invisible to
    free users until someone names it."""
    for path in ("/api/brand-new-feature", "/api/admin", "/", "/api/v6/secret"):
        assert policy.is_allowed(path, policy.FREE) is False, path


# ── the vault gate ───────────────────────────────────────────────────────────

def test_only_owner_reads_the_vault():
    assert policy.can_read_vault(policy.OWNER) is True
    assert policy.can_read_vault(policy.FREE) is False
    for junk in ("", None, "admin", "OWNER", "owner "):
        assert policy.can_read_vault(junk) is False, junk
