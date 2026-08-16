"""
src/research/http.py
====================
One JSON/GET helper for every source. urllib only, per project convention.

Centralized so that the byte cap, the timeout, the UA and the SSRF guard are
not re-decided (or forgotten) once per source.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import SourceError
from .url import normalize_public_url

logger = logging.getLogger(__name__)

# Several APIs here (Reddit especially) reject or throttle a generic agent and
# ask for a descriptive one. Identifying honestly is also the price of using
# a public endpoint politely.
UA = "ARIA-Research/1.0 (trading research bot; +https://github.com/that-finance-guy)"
TIMEOUT = 25
MAX_BYTES = 8 * 1024 * 1024


# The SEC rejects any request whose User-Agent lacks a contact address — stated
# policy on their fair-access page, and the failure is a bare 403 that reads
# like a block rather than a missing header.
#
# The address is configuration, never source. This repository is public, and a
# personal email committed to a public repo is harvested within days; it is
# also simply the wrong owner once anyone else runs this. Set ARIA_SEC_CONTACT,
# or put SEC_CONTACT in data/research_keys.json (gitignored).
SEC_UA_TEMPLATE = "ARIA Research {contact}"


def sec_contact() -> str:
    """The contact address SEC requires, from the environment or keys file."""
    from .sources.walled import credential
    return (os.environ.get("ARIA_SEC_CONTACT", "")
            or credential("SEC_CONTACT")).strip()


def _agent_for(url: str) -> str:
    from .url import host_matches

    if not host_matches(url, "sec.gov"):
        return UA

    contact = sec_contact()
    if not contact:
        # Fail loudly rather than send an agent SEC will refuse: a bare 403
        # here looks like a network problem and costs an hour to diagnose.
        logger.warning(
            "no SEC contact configured — set ARIA_SEC_CONTACT or SEC_CONTACT in "
            "data/research_keys.json; SEC returns 403 without one"
        )
        return UA
    return SEC_UA_TEMPLATE.format(contact=contact)


# Minimum spacing between requests to the same host, in seconds. A blink over
# five watched tickers fires five sweeps at once, and each sweep asks every
# source — so without this the eye opens by sending Reddit a dozen simultaneous
# requests and gets 429 for all of them. Retrying does not help a burst; not
# making a burst does. Values are per host, since the limits are per host.
_HOST_MIN_INTERVAL = {
    "reddit.com": 2.0,
    "api.stocktwits.com": 1.5,
    "efts.sec.gov": 0.15,        # SEC asks for <10 req/s
    "www.sec.gov": 0.15,
}
_DEFAULT_MIN_INTERVAL = 0.25
_last_request: dict[str, float] = {}
_throttle_lock = threading.Lock()

# After a host says 429, stop asking it for a while. A blink over five tickers
# would otherwise learn nothing from the first refusal and go on to spend four
# more sweeps — each burning its retries and their sleeps — to be refused four
# more times. Backing off is both politer and faster: one honest "cooling down"
# per remaining watch, instantly, instead of a minute of hammering.
_COOLDOWN_SECONDS = 600.0
_cooldown_until: dict[str, float] = {}


class Throttled(SourceError):
    """This host refused us recently; we are deliberately not asking again yet."""


def _host_key(url: str) -> str:
    from urllib.parse import urlsplit
    host = (urlsplit(url).hostname or "").lower()
    return next((h for h in _HOST_MIN_INTERVAL if host.endswith(h)), host)


def cooling_down(url: str) -> float:
    """Seconds remaining before this host may be asked again (0 = go ahead)."""
    with _throttle_lock:
        return max(0.0, _cooldown_until.get(_host_key(url), 0.0) - time.monotonic())


def _begin_cooldown(url: str, seconds: float = _COOLDOWN_SECONDS) -> None:
    with _throttle_lock:
        _cooldown_until[_host_key(url)] = time.monotonic() + seconds


def _throttle(url: str) -> None:
    """Block until this host may be called again. Thread-safe."""
    from urllib.parse import urlsplit
    host = (urlsplit(url).hostname or "").lower()
    key = next((h for h in _HOST_MIN_INTERVAL if host.endswith(h)), host)
    gap = _HOST_MIN_INTERVAL.get(key, _DEFAULT_MIN_INTERVAL)

    while True:
        with _throttle_lock:
            now = time.monotonic()
            earliest = _last_request.get(key, 0.0) + gap
            if now >= earliest:
                _last_request[key] = now
                return
            wait = earliest - now
        # Sleep outside the lock so other hosts are never blocked by this one.
        time.sleep(min(wait, 5.0))


def get(url: str, headers: dict | None = None, timeout: int = TIMEOUT,
        retries: int = 2) -> bytes:
    """GET with one polite retry on 429/503.

    Unauthenticated endpoints — Reddit's Atom feeds especially — throttle a
    burst of requests, and a lead sweep is exactly a burst. Honoring
    Retry-After costs a second and is the difference between a source that
    works and one that reports HTTP 429 every cycle. Retries stop at `retries`
    so a hard block fails fast instead of stalling the sweep.
    """
    clean = normalize_public_url(url)
    request_headers = {"User-Agent": _agent_for(clean), **(headers or {})}

    remaining = cooling_down(clean)
    if remaining:
        raise Throttled(
            f"{_host_key(clean)} rate-limited us; backing off for another "
            f"{remaining:.0f}s rather than asking again"
        )

    for attempt in range(retries + 1):
        _throttle(clean)
        req = urllib.request.Request(clean, headers=request_headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(MAX_BYTES + 1)
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503) and attempt < retries:
                try:
                    wait = float(exc.headers.get("Retry-After", "") or 0)
                except (TypeError, ValueError):
                    wait = 0.0
                time.sleep(min(max(wait, 1.5 * (attempt + 1)), 10.0))
                continue
            if exc.code == 429:
                # Out of retries and still refused: that is the host telling us
                # our rate is wrong, not that this one URL is unlucky.
                _begin_cooldown(clean)
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
