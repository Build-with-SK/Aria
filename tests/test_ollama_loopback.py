"""
tests/test_ollama_loopback.py
=============================
ARIA reaches the local Ollama by literal IPv4 loopback.

On Windows `localhost` resolves to ::1 first. With Ollama bound to 127.0.0.1
only — the setting that keeps it off the network — every `localhost`
connection waited for the IPv6 refusal: 2,048 ms per request measured on the
owner's laptop against 1 ms for 127.0.0.1 (3 Nov 2026). The Mac mini path
(OLLAMA_BASE = the laptop's LAN address) must be untouched.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.inference import base

ROOT = Path(__file__).resolve().parent.parent


def test_the_default_is_the_literal_loopback(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE", raising=False)
    assert base.ollama_base() == "http://127.0.0.1:11434"


def test_an_empty_variable_means_the_default(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE", "   ")
    assert base.ollama_base() == "http://127.0.0.1:11434"


@pytest.mark.parametrize("given, expected", [
    ("http://localhost:11434", "http://127.0.0.1:11434"),
    ("http://LOCALHOST:11434/", "http://127.0.0.1:11434"),
    ("http://192.168.1.20:11434", "http://192.168.1.20:11434"),   # mini -> laptop
    ("http://laptop.tailnet.ts.net:11434", "http://laptop.tailnet.ts.net:11434"),
    ("http://[::1]:11434", "http://[::1]:11434"),
    ("http://localhost.example:11434", "http://localhost.example:11434"),
])
def test_only_the_host_localhost_is_rewritten(monkeypatch, given, expected):
    monkeypatch.setenv("OLLAMA_BASE", given)
    assert base.ollama_base() == expected


def test_no_runtime_code_hard_codes_localhost_for_ollama():
    """A stray literal would bring the 2 s penalty back for that one caller.

    scripts/mini_preflight.py is excluded: it runs on the Mac mini and only
    names localhost in its advice text about where Ollama is NOT."""
    offenders = []
    for folder in ("src", "backend"):
        for path in (ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "http://localhost:11434" in text and path.name != "base.py":
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
