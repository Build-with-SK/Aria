"""
src/inference/base.py
=====================
One source of truth for where Ollama lives. The split deployment (Mac mini
runs the desk, the laptop runs Ollama) points OLLAMA_BASE at the laptop's
LAN address; everything else defaults to this machine's IPv4 loopback.

Why 127.0.0.1 and not `localhost`: on Windows `localhost` resolves to ::1
first. Once Ollama listens on IPv4 loopback only (OLLAMA_HOST=127.0.0.1, which
keeps it off the network), every connection to `localhost` waits for the IPv6
attempt to be refused before trying 127.0.0.1 — measured 2,048 ms per request
on the owner's laptop, against 1 ms to 127.0.0.1 (3 Nov 2026). The literal
address works with that binding and with the old all-interfaces one.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

DEFAULT_OLLAMA_BASE = "http://127.0.0.1:11434"


def loopback_ipv4(url: str) -> str:
    """`url` with the host `localhost` written as 127.0.0.1; anything else,
    including an explicit [::1] or a LAN address, exactly as given."""
    text = str(url or "").strip()
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    if (parts.hostname or "").lower() != "localhost":
        return text
    netloc = "127.0.0.1" + (f":{parts.port}" if parts.port else "")
    return urlunsplit(parts._replace(netloc=netloc))


def ollama_base() -> str:
    raw = os.environ.get("OLLAMA_BASE", "").strip() or DEFAULT_OLLAMA_BASE
    return loopback_ipv4(raw).rstrip("/")
