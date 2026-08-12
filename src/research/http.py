"""
src/research/http.py
====================
One JSON/GET helper for every source. urllib only, per project convention.

Centralized so that the byte cap, the timeout, the UA and the SSRF guard are
not re-decided (or forgotten) once per source.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .base import SourceError
from .url import normalize_public_url

# Several APIs here (Reddit especially) reject or throttle a generic agent and
# ask for a descriptive one. Identifying honestly is also the price of using
# a public endpoint politely.
UA = "ARIA-Research/1.0 (trading research bot; +https://github.com/that-finance-guy)"
TIMEOUT = 25
MAX_BYTES = 8 * 1024 * 1024


# The SEC rejects any request whose User-Agent lacks a contact address — it is
# stated policy on their fair-access page, and the failure is a bare 403 that
# looks like a block rather than a missing header.
SEC_UA = "ARIA Research batspiderchef@gmail.com"


def _agent_for(url: str) -> str:
    from .url import host_matches
    return SEC_UA if host_matches(url, "sec.gov") else UA


def get(url: str, headers: dict | None = None, timeout: int = TIMEOUT) -> bytes:
    clean = normalize_public_url(url)
    req = urllib.request.Request(
        clean, headers={"User-Agent": _agent_for(clean), **(headers or {})}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise SourceError(f"{clean} returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise SourceError(f"{clean} unreachable: {exc}") from exc
    if len(body) > MAX_BYTES:
        raise SourceError(f"{clean} response exceeds {MAX_BYTES} bytes")
    return body


def get_json(url: str, headers: dict | None = None, timeout: int = TIMEOUT):
    body = get(url, headers={"Accept": "application/json", **(headers or {})},
               timeout=timeout)
    try:
        return json.loads(body)
    except ValueError as exc:
        raise SourceError(f"{url} did not return JSON") from exc


def q(**params) -> str:
    """Query string with None values dropped."""
    return urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
