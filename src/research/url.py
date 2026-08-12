"""
src/research/url.py
===================
URL hygiene for content ARIA did not choose.

Every URL that reaches this module came from somewhere untrusted — an RSS
item, a link inside a fetched page, a ticker headline. Treat all of them as
hostile until proven to be public HTTP(S): otherwise a crafted feed entry
turns the research fetcher into an SSRF probe against localhost, the Ollama
port on 11434, or a cloud metadata endpoint.

Adapted from the guard in Panniantong/agent-reach (MIT), which had already
worked out the legacy-IPv4 and userinfo-disguise cases.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

# Names that resolve inside the machine or the datacenter, never on the
# public internet. `.local` covers mDNS, `metadata.google.internal` and
# `instance-data` cover the two cloud credential endpoints.
BLOCKED_HOSTS = {
    "home.arpa",
    "instance-data",
    "internal",
    "ip6-localhost",
    "ip6-loopback",
    "lan",
    "local",
    "localdomain",
    "localhost",
    "metadata.google.internal",
}
BLOCKED_SUFFIXES = (
    ".home.arpa",
    ".internal",
    ".lan",
    ".local",
    ".localdomain",
    ".localhost",
)


def _literal_ip(host: str):
    """Parse an IP literal without DNS, including legacy forms like 0177.0.0.1."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        packed = socket.inet_aton(host)   # accepts octal/short-form IPv4
    except OSError:
        return None
    return ipaddress.IPv4Address(packed)


def normalize_public_url(url: str) -> str:
    """Return a normalized public HTTP(S) URL, or raise ValueError.

    Rejects: non-HTTP schemes, embedded credentials, whitespace/control
    characters (request smuggling), backslashes (Windows path confusion),
    loopback/private/link-local addresses in any spelling, and internal
    hostnames.
    """
    candidate = str(url or "").strip()
    if not candidate or "\\" in candidate or any(
        c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in candidate
    ):
        raise ValueError(f"not a public HTTP(S) URL: {url!r}")

    if "://" not in candidate:
        candidate = f"https://{candidate}"

    try:
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").lower().rstrip(".")
        _ = parsed.port          # accessing .port validates the authority
    except (TypeError, ValueError):
        raise ValueError(f"not a public HTTP(S) URL: {url!r}") from None

    literal = _literal_ip(host)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or "%" in host
        or host in BLOCKED_HOSTS
        or host.endswith(BLOCKED_SUFFIXES)
        # A bare label with no dot is an intranet name unless it is an IP.
        or ("." not in host and literal is None)
        or (literal is not None and not literal.is_global)
    ):
        raise ValueError(f"not a public HTTP(S) URL: {url!r}")

    return parsed.geturl()


def host_matches(url: str, *domains: str) -> bool:
    """True when *url*'s host is one of *domains* or a real subdomain of one.

    Compares the parsed hostname, never a substring: `x.com.evil.test` and
    `x.com@evil.test` must not match `x.com`.
    """
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        _ = parsed.port
    except (TypeError, ValueError):
        return False

    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    if not host or parsed.username is not None or parsed.password is not None:
        return False

    for domain in domains:
        allowed = domain.lower().strip(".")
        if host == allowed or host.endswith("." + allowed):
            return True
    return False
