"""Public research GETs: validate DNS and every redirect before connection.

DNS is checked before urllib connects; production egress rules must also
reject private networks to close the DNS rebinding race at connection time.
"""
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request

from .url import normalize_public_url


def _public_target(url):
    clean = normalize_public_url(url)
    parsed = urllib.parse.urlsplit(clean)
    try:
        answers = socket.getaddrinfo(parsed.hostname, parsed.port or (
            443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError as exc:
        raise urllib.error.URLError("research host could not be resolved") from exc
    if not answers or any(not ipaddress.ip_address(a[4][0]).is_global for a in answers):
        raise urllib.error.URLError("research DNS target is not public")
    return clean


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        clean = _public_target(urllib.parse.urljoin(req.full_url, newurl))
        if urllib.parse.urlsplit(req.full_url).scheme == "https" and not clean.startswith("https://"):
            raise urllib.error.URLError("research HTTPS downgrade refused")
        target = super().redirect_request(req, fp, code, msg, headers, clean)
        if target is not None:
            # urllib normally copies custom headers across origins. Never
            # forward source credentials to the redirect's new host.
            old = urllib.parse.urlsplit(req.full_url)
            new = urllib.parse.urlsplit(clean)
            if (old.scheme, old.netloc) != (new.scheme, new.netloc):
                for name in ("Authorization", "Cookie", "X-api-key"):
                    target.remove_header(name)
        return target


def public_urlopen(request, *, timeout):
    req = request if isinstance(request, urllib.request.Request) else urllib.request.Request(request)
    _public_target(req.full_url)
    return urllib.request.build_opener(PublicRedirectHandler()).open(req, timeout=timeout)
