"""Server-side source checks: the only way research evidence becomes
VERIFIED_EXTERNAL_FACT (review RQ-01).

A request may *say* a claim is verified; only `SourceResolver` can make it so.
It fetches the claim's `source_url` itself (https only, an owner-allowlisted
host, bounded size and time, no redirect to another host), records the
document's SHA-256 and the fetch time, and accepts the claim only if the claim's
exact text (case and whitespace aside) appears in the fetched document. That
shows "this source said this, at this time" — not that the statement is true.

No HTTP route builds a resolver: fetching URLs named in client input is a
server-side request path with its own risks (an allowlist decision, egress
rules) and needs its own review. Until one is wired, every client-labelled
VERIFIED_EXTERNAL_FACT or OBSERVED claim reaches research output as UNVERIFIED.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
import re
import urllib.error
import urllib.parse
import urllib.request

from pydantic import ValidationError

from src.trading.contracts import Contract, ResearchEvidence, revalidated

MAX_BYTES = 2_000_000
TIMEOUT_SECONDS = 10


class ResolvedSource(Contract):
    evidence_id: str
    url: str
    claim_sha256: str
    content_sha256: str
    fetched_at: datetime


def _normalise(text: str) -> str:
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html.unescape(text).split()).casefold()


def _sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


class _SameHostRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if new.scheme != "https" or new.hostname != old.hostname:
            raise urllib.error.HTTPError(newurl, code, "redirect to another host refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def https_fetch(url: str) -> bytes:
    """Default fetcher: urllib, bounded, same-host redirects only."""
    opener = urllib.request.build_opener(_SameHostRedirects())
    request = urllib.request.Request(url, headers={"User-Agent": "ARIA-research-source-check"})
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("source document too large")
    return body


class SourceResolver:
    """Checks claims against sources it fetches itself, and remembers what it checked."""

    def __init__(self, *, allowed_hosts, fetch=https_fetch, clock=None):
        hosts = frozenset(str(h).lower().strip(".") for h in allowed_hosts)
        if not hosts or any(not h or "/" in h or ":" in h or "@" in h for h in hosts):
            raise ValueError("an explicit host allowlist is required")
        self._hosts = hosts
        self._fetch = fetch
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._records: dict[str, ResolvedSource] = {}

    def host_allowed(self, url: str | None) -> bool:
        if not url:
            return False
        try:
            parts = urllib.parse.urlsplit(url)
            port = parts.port
        except ValueError:
            return False
        host = (parts.hostname or "").lower()
        return (parts.scheme == "https" and parts.username is None and parts.password is None
                and port in (None, 443)
                and any(host == h or host.endswith("." + h) for h in self._hosts))

    def resolve(self, evidence: ResearchEvidence) -> ResearchEvidence:
        """The claim as VERIFIED_EXTERNAL_FACT if its source contains it; otherwise UNVERIFIED."""
        evidence = revalidated(evidence)
        unverified = revalidated(evidence.model_copy(update={"classification": "UNVERIFIED"}))
        url = evidence.provenance.source_url
        claim = _normalise(evidence.claim)
        if not claim or not self.host_allowed(url):
            return unverified
        try:
            body = self._fetch(url)
            if not isinstance(body, bytes) or len(body) > MAX_BYTES:
                return unverified
            fetched_at = self._clock()
            text = _normalise(body.decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001 - an unreachable source verifies nothing
            return unverified
        if claim not in text:
            return unverified
        record = ResolvedSource(evidence_id=evidence.evidence_id, url=url, claim_sha256=_sha256(claim),
                                content_sha256=_sha256(body), fetched_at=fetched_at)
        try:
            provenance = revalidated(evidence.provenance.model_copy(update={
                "retrieved_at": fetched_at, "data_type": f"source_sha256:{record.content_sha256}"}))
            verified = revalidated(evidence.model_copy(update={
                "classification": "VERIFIED_EXTERNAL_FACT", "provenance": provenance}))
        except ValidationError:
            return unverified
        self._records[evidence.evidence_id] = record
        return verified

    def verified(self, evidence: ResearchEvidence, *, as_of: datetime | None = None) -> bool:
        """True only for evidence this resolver itself verified, unchanged since."""
        record = self._records.get(evidence.evidence_id)
        return (record is not None and (as_of is None or record.fetched_at <= as_of)
                and evidence.classification == "VERIFIED_EXTERNAL_FACT"
                and evidence.provenance.source_url == record.url
                and evidence.provenance.data_type == f"source_sha256:{record.content_sha256}"
                and record.claim_sha256 == _sha256(_normalise(evidence.claim)))

    def record(self, evidence_id: str) -> ResolvedSource | None:
        return self._records.get(evidence_id)
