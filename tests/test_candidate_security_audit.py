"""Existing anonymous security audit against the real isolated ASGI app."""
import json
import os
from pathlib import Path


def test_candidate_existing_security_audit(monkeypatch):
    from scripts.security_audit import audit, AsgiProbe
    from src.auth import policy, hardening
    monkeypatch.setenv('ARIA_OWNER_TOKEN','candidate-audit-fixture')
    monkeypatch.delenv('ARIA_OWNER_TOKEN_HASH',raising=False)
    monkeypatch.setattr(policy,'owner_token_hash',lambda:'')
    hardening._limiter._hits.clear()
    result=audit(AsgiProbe('backend.main:app'),owner_token='candidate-audit-fixture')
    if os.environ.get('ARIA_SECURITY_AUDIT_OUT'):
        Path(os.environ['ARIA_SECURITY_AUDIT_OUT']).write_text(json.dumps(result,indent=2))
    assert not result['fails'], result
    assert not result['warnings'], result
