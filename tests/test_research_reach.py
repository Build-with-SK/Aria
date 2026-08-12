"""
tests/test_research_reach.py
============================
The research module's two load-bearing guarantees:

  1. A URL from an untrusted feed can never point ARIA at localhost, the
     Ollama port, or a cloud metadata endpoint.
  2. Everything returned to a caller is archived with provenance first.

Nothing here touches the network.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.research import normalize_public_url, route  # noqa: E402
from src.research.base import Document  # noqa: E402
from src.research.sources.github import GitHubSource, _repo_path  # noqa: E402
from src.research.sources.rss import RSSSource  # noqa: E402
from src.research.sources.web import WebSource, _is_challenge_page  # noqa: E402
from src.research.sources.youtube import YouTubeSource, _vtt_to_text  # noqa: E402


# --------------------------------------------------------------- SSRF guard

@pytest.mark.parametrize("url", [
    "http://localhost:8000/api/keys",
    "http://127.0.0.1:11434/api/tags",       # Ollama
    "http://0177.0.0.1/",                    # legacy octal IPv4
    "http://[::1]/",
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata
    "http://metadata.google.internal/",
    "http://192.168.1.1/",
    "http://10.0.0.5/",
    "http://nas.local/",
    "file:///C:/Users/sound/.env",
    "ftp://example.com/x",
    "https://user:pass@example.com/",        # embedded credentials
    "https://exam ple.com/",                 # whitespace
    "https://example.com/\r\nHost: evil",    # header injection
    "https://intranet",                      # bare label
    "",
])
def test_rejects_non_public_urls(url):
    with pytest.raises(ValueError):
        normalize_public_url(url)


@pytest.mark.parametrize("url", [
    "https://www.reuters.com/markets/",
    "http://example.com/path?q=1",
    "https://github.com/Panniantong/agent-reach",
    "https://8.8.8.8/",
])
def test_accepts_public_urls(url):
    assert normalize_public_url(url).startswith(("http://", "https://"))


def test_bare_host_gets_https():
    assert normalize_public_url("example.com/feed") == "https://example.com/feed"


# ----------------------------------------------------------------- routing

@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=abc123", "youtube"),
    ("https://youtu.be/abc123", "youtube"),
    ("https://github.com/pandas-dev/pandas", "github"),
    ("https://www.federalreserve.gov/feeds/press_all.xml", "rss"),
    ("https://feeds.reuters.com/reuters/businessNews", "rss"),
    ("https://www.reuters.com/markets/rates-bonds/", "web"),
])
def test_routing(url, expected):
    assert route(url).name == expected


def test_lookalike_hosts_do_not_match_specialists():
    """github.com.evil.test must not be routed to the GitHub source."""
    assert not GitHubSource().can_handle("https://github.com.evil.test/x/y")
    assert not YouTubeSource().can_handle("https://youtube.com@evil.test/watch")
    assert route("https://github.com.evil.test/x/y").name == "web"


# ------------------------------------------------------------ source detail

def test_repo_path_parsing():
    assert _repo_path("https://github.com/a/b") == ("a/b", "repo", "")
    assert _repo_path("https://github.com/a/b/issues/42") == ("a/b", "issue", "42")
    assert _repo_path("https://github.com/a/b/pull/7") == ("a/b", "pr", "7")


def test_challenge_page_is_not_content():
    challenge = b"Title: Just a moment...\n\n## Performing security verification"
    assert _is_challenge_page(challenge)
    assert not _is_challenge_page(b"Title: Fed holds rates\n\nThe committee voted...")


def test_vtt_stripped_and_deduplicated():
    vtt = (
        "WEBVTT\nKind: captions\nLanguage: en\n\n"
        "00:00:01.000 --> 00:00:03.000\n<c>inflation is</c> cooling\n\n"
        "00:00:03.000 --> 00:00:05.000\ninflation is cooling\n\n"
        "00:00:05.000 --> 00:00:07.000\nbut services remain sticky\n"
    )
    assert _vtt_to_text(vtt) == "inflation is cooling\nbut services remain sticky"


def test_feed_entries_drop_poisoned_links(monkeypatch):
    """One entry pointing at localhost must not kill the whole feed."""
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Good</title><link>https://example.com/a</link>
            <description>ok</description><pubDate>Wed, 12 Aug 2026 09:00:00 GMT</pubDate></item>
      <item><title>Evil</title><link>http://127.0.0.1:11434/api/tags</link>
            <description>nope</description></item>
    </channel></rss>"""

    class FakeResponse:
        def read(self, _n=None): return feed
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(
        "src.research.sources.rss.urllib.request.urlopen",
        lambda *a, **k: FakeResponse(),
    )
    entries = RSSSource().entries("https://example.com/feed.xml")
    assert [e.title for e in entries] == ["Good"]
    assert entries[0].meta["published"].startswith("Wed, 12 Aug 2026")


# ------------------------------------------------------------------- store

def test_archive_writes_content_once_and_records_every_retrieval(tmp_path, monkeypatch):
    from src.research import store

    monkeypatch.setattr(store, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(store, "INDEX", tmp_path / "index")

    doc = Document(
        url="https://example.com/a",
        title="A",
        text="same bytes",
        source="web",
        backend="jina",
    )
    first = store.archive(doc)
    second = store.archive(doc)

    assert first == second
    assert len(list((tmp_path / "docs").glob("*.md"))) == 1

    index_file = next((tmp_path / "index").glob("*.jsonl"))
    lines = index_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2, "both retrievals must be recorded"

    record = json.loads(lines[0])
    assert record["url"] == "https://example.com/a"
    assert record["backend"] == "jina"
    assert record["chars"] == len("same bytes")
    assert store.load(first) == "same bytes"
    assert "retrieved" in store.citation(record)


def test_read_archives_before_returning(tmp_path, monkeypatch):
    from src.research import reach, store

    monkeypatch.setattr(store, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(store, "INDEX", tmp_path / "index")
    monkeypatch.setattr(
        WebSource,
        "fetch",
        lambda self, url: Document(
            url=url, title="T", text="body", source="web", backend="jina"
        ),
    )

    doc = reach.read("https://www.reuters.com/markets/")
    assert doc.meta["content_id"]
    assert store.load(doc.meta["content_id"]) == "body"


def test_specialist_failure_falls_back_to_web(tmp_path, monkeypatch):
    from src.research import reach, store
    from src.research.base import SourceError

    monkeypatch.setattr(store, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(store, "INDEX", tmp_path / "index")

    def boom(self, url):
        raise SourceError("yt-dlp not available")

    monkeypatch.setattr(YouTubeSource, "fetch", boom)
    monkeypatch.setattr(
        WebSource,
        "fetch",
        lambda self, url: Document(
            url=url, title="T", text="page text", source="web", backend="jina"
        ),
    )

    doc = reach.read("https://www.youtube.com/watch?v=abc123")
    assert doc.backend == "jina"
    assert doc.text == "page text"


def test_status_reports_every_source():
    from src.research import status

    names = {s["name"] for s in status()}
    assert names == {
        "web", "rss", "youtube", "github", "reddit", "stocktwits",
        "edgar", "googlenews", "hackernews", "x", "meta",
    }
    assert all(isinstance(s["available"], bool) for s in status())
    # The free, unauthenticated core must never silently start needing setup.
    free = {s["name"] for s in status() if s["zero_config"]}
    assert {"web", "rss", "reddit", "stocktwits", "edgar",
            "googlenews", "hackernews"} <= free
