"""Security/ML regressions with synthetic XML, DNS and snapshots only."""
from datetime import datetime, timedelta
from urllib.error import URLError
from urllib.request import Request
from xml.etree import ElementTree

import pytest

from src.core.xml_security import feed_xml
from src.models.ml_store import dataset_age
from src.research import transport


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
def test_feed_entities_are_rejected_before_expansion(encoding):
    text = '<!DOCTYPE rss [<!ENTITY boom "EXPANDED">]><rss>&boom;</rss>'
    with pytest.raises(ElementTree.ParseError):
        feed_xml(text.encode(encoding))


def test_regular_rss_still_parses_and_large_input_is_rejected():
    assert feed_xml(b"<rss><title>market</title></rss>").findtext("title") == "market"
    with pytest.raises(ElementTree.ParseError):
        feed_xml(b"x" * (8 * 1024 * 1024 + 1))


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "::1"])
def test_dns_private_target_is_refused(monkeypatch, address):
    monkeypatch.setattr(transport.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (address, 80))])
    with pytest.raises(URLError, match="not public"):
        transport._public_target("https://public.example/feed")


def test_redirect_cannot_reach_private_host_or_forward_credentials(monkeypatch):
    monkeypatch.setattr(transport.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443))])
    handler = transport.PublicRedirectHandler()
    req = Request("https://public.example/feed", headers={"Authorization": "fixture-only", "Cookie": "fixture-only"})
    with pytest.raises(ValueError):
        handler.redirect_request(req, None, 302, "found", {}, "http://127.0.0.1/private")
    with pytest.raises(URLError, match="downgrade"):
        handler.redirect_request(req, None, 302, "found", {}, "http://other.example/feed")
    new = handler.redirect_request(req, None, 302, "found", {}, "https://other.example/feed")
    assert not new.has_header("Authorization") and not new.has_header("Cookie")


@pytest.mark.parametrize("body", [{}, {"A": {"generated_at": "broken"}}, {"A": {"generated_at": "2030-01-01T00:00:00"}}])
def test_empty_invalid_or_future_ml_is_never_fresh(body):
    now = datetime(2026, 10, 18, 12)
    assert dataset_age(body, now, now, 20)["stale"] is True


def test_ml_summary_uses_oldest_row_not_recent_file_write():
    now = datetime(2026, 10, 18, 12)
    rows = {"old": {"generated_at": (now-timedelta(hours=30)).isoformat()},
            "new": {"generated_at": now.isoformat()}}
    assert dataset_age(rows, now, now, 20)["rows_over_budget"] == 1
