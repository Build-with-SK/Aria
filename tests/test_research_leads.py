"""
tests/test_research_leads.py
============================
The fan-out's contract:

  1. One failing or throttled source must not take the sweep with it, and
     the failure must be visible in the result — a silently short sweep is
     worse than an error.
  2. Sources needing a credential report why instead of raising.
  3. Nothing reaches the network in these tests.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.research import route  # noqa: E402
from src.research.base import Document, SourceError  # noqa: E402
from src.research.sources.markets import EdgarSource, StockTwitsSource  # noqa: E402
from src.research.sources.news import GoogleNewsSource, HackerNewsSource  # noqa: E402
from src.research.sources.reddit import RedditSource  # noqa: E402
from src.research.sources.walled import MetaSource, XSource, credential  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    from src.research import store
    monkeypatch.setattr(store, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(store, "INDEX", tmp_path / "index")


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch, tmp_path):
    """A developer's real token must not change what these tests assert."""
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr("src.research.sources.walled.KEYS_FILE", tmp_path / "none.json")


# ---------------------------------------------------------------- routing

@pytest.mark.parametrize("url,expected", [
    ("https://www.reddit.com/r/stocks/comments/abc/title/", "reddit"),
    ("https://old.reddit.com/r/wallstreetbets/", "reddit"),
    ("https://stocktwits.com/symbol/NVDA", "stocktwits"),
    ("https://www.sec.gov/Archives/edgar/data/320193/x.htm", "edgar"),
    ("https://news.google.com/rss/search?q=nvidia", "googlenews"),
    ("https://news.ycombinator.com/item?id=1", "hackernews"),
    ("https://x.com/user/status/123", "x"),
    ("https://www.instagram.com/p/abc/", "meta"),
    ("https://example.com/blog/post", "web"),
])
def test_routing_covers_new_sources(url, expected):
    assert route(url).name == expected


# ------------------------------------------------------- walled gardens

def test_walled_sources_report_missing_credentials_not_crash():
    ok, detail = XSource().available()
    assert ok is False and "X_BEARER_TOKEN" in detail

    ok, detail = MetaSource().available()
    assert ok is False and "META_ACCESS_TOKEN" in detail


def test_x_search_without_token_raises_explanatory_error():
    with pytest.raises(SourceError) as exc:
        XSource().search("NVDA")
    assert "X_BEARER_TOKEN" in str(exc.value)


def test_meta_fetch_explains_there_is_no_per_url_read():
    with pytest.raises(SourceError) as exc:
        MetaSource().fetch("https://www.instagram.com/p/abc/")
    assert "no per-URL read endpoint" in str(exc.value)


def test_credential_prefers_env_then_file(monkeypatch, tmp_path):
    keys = tmp_path / "research_keys.json"
    keys.write_text('{"X_BEARER_TOKEN": "from-file"}', encoding="utf-8")
    monkeypatch.setattr("src.research.sources.walled.KEYS_FILE", keys)
    assert credential("X_BEARER_TOKEN") == "from-file"

    monkeypatch.setenv("X_BEARER_TOKEN", "from-env")
    assert credential("X_BEARER_TOKEN") == "from-env"


# ------------------------------------------------------------- parsing

def test_stocktwits_counts_sentiment_and_reports_absent_as_none(monkeypatch):
    payload = {"messages": [
        {"body": "long here", "user": {"username": "a"},
         "entities": {"sentiment": {"basic": "Bullish"}}},
        {"body": "short", "user": {"username": "b"},
         "entities": {"sentiment": {"basic": "Bearish"}}},
        {"body": "no tag", "user": {"username": "c"}, "entities": {}},
    ]}
    monkeypatch.setattr("src.research.sources.markets.get_json", lambda *a, **k: payload)
    doc = StockTwitsSource().symbol("NVDA")
    assert doc.meta["bullish"] == 1 and doc.meta["bearish"] == 1
    assert doc.meta["bull_ratio"] == 0.5
    assert doc.meta["messages"] == 3

    monkeypatch.setattr("src.research.sources.markets.get_json",
                        lambda *a, **k: {"messages": [{"body": "x", "user": {}, "entities": {}}]})
    doc = StockTwitsSource().symbol("NVDA")
    assert doc.meta["bull_ratio"] is None, "no tagged messages is not a neutral reading"


def test_edgar_builds_document_links_from_hit_ids(monkeypatch):
    payload = {"hits": {"hits": [{
        "_id": "0000320193-26-000010:aapl-8k.htm",
        "_source": {"file_type": "8-K", "ciks": ["0000320193"],
                    "display_names": ["Apple Inc. (AAPL)"], "file_date": "2026-08-01",
                    "file_description": "Results of Operations"},
    }]}}
    monkeypatch.setattr("src.research.sources.markets.get_json", lambda *a, **k: payload)
    docs = EdgarSource().search('"supply constraint"', forms="8-K")
    assert docs[0].url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/aapl-8k.htm"
    )
    assert docs[0].meta["form"] == "8-K"
    assert docs[0].meta["company"] == "Apple Inc. (AAPL)"


def test_reddit_oauth_backend_extracts_scores(monkeypatch):
    payload = {"data": {"children": [
        {"data": {"title": "NVDA thread", "permalink": "/r/stocks/comments/x/",
                  "selftext": "body", "score": 412, "subreddit": "stocks",
                  "author": "u1", "num_comments": 88}},
    ]}}
    monkeypatch.setattr("src.research.sources.reddit._oauth_token", lambda: "tok")
    monkeypatch.setattr("src.research.sources.reddit.get_json", lambda *a, **k: payload)

    docs = RedditSource().search("NVDA", subreddit="stocks")
    assert docs[0].url == "https://www.reddit.com/r/stocks/comments/x/"
    assert docs[0].backend == "oauth"
    assert docs[0].meta["score"] == 412


def test_reddit_falls_back_to_atom_without_credentials(monkeypatch):
    """Without an app credential the RSS backend serves, and reports no score.

    `score: None` rather than 0 is the point: this backend cannot see votes,
    which is a different fact from a post having none.
    """
    atom = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>NVDA thread</title>
        <link href="https://www.reddit.com/r/stocks/comments/x/"/>
        <author><name>/u/u1</name></author>
        <updated>2026-08-11T12:00:00+00:00</updated>
        <content type="html">body</content>
      </entry>
      <entry>
        <title>Poisoned</title>
        <link href="http://127.0.0.1:11434/api/tags"/>
      </entry>
    </feed>"""
    monkeypatch.setattr("src.research.sources.reddit._oauth_token", lambda: None)
    monkeypatch.setattr("src.research.sources.reddit.get", lambda *a, **k: atom)

    docs = RedditSource().search("NVDA", subreddit="stocks")
    assert [d.title for d in docs] == ["NVDA thread"]
    assert docs[0].backend == "public-rss"
    assert docs[0].meta["score"] is None
    assert docs[0].meta["author"] == "u1"


def test_reddit_reports_which_backend_is_serving(monkeypatch):
    monkeypatch.setattr("src.research.sources.reddit._oauth_token", lambda: None)
    ok, detail = RedditSource().available()
    assert ok and "REDDIT_CLIENT_ID" in detail

    monkeypatch.setattr("src.research.sources.reddit._oauth_token", lambda: "tok")
    ok, detail = RedditSource().available()
    assert ok and detail.startswith("oauth")


def test_sec_requests_carry_a_contact_user_agent():
    """SEC 403s any agent without a contact address — a bare 403 that reads
    like a block rather than a missing header."""
    from src.research.http import SEC_UA, _agent_for
    assert "@" in SEC_UA
    assert _agent_for("https://efts.sec.gov/LATEST/search-index?q=x") == SEC_UA
    assert _agent_for("https://example.com/") != SEC_UA


def test_google_news_drops_unparseable_links(monkeypatch):
    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Fed holds</title><link>https://reuters.com/a</link>
            <source>Reuters</source><pubDate>Wed, 12 Aug 2026 09:00:00 GMT</pubDate></item>
      <item><title>Bad</title><link>http://localhost/x</link></item>
    </channel></rss>"""
    monkeypatch.setattr("src.research.sources.news.get", lambda *a, **k: xml)
    docs = GoogleNewsSource().search("fed")
    assert [d.title for d in docs] == ["Fed holds"]
    assert docs[0].meta["publisher"] == "Reuters"


def test_hackernews_search_links_back_to_discussion(monkeypatch):
    payload = {"hits": [{"objectID": "999", "title": "Chip shortage",
                         "url": "https://ex.com/a", "points": 250,
                         "author": "u", "num_comments": 40,
                         "created_at": "2026-08-11T00:00:00Z"}]}
    monkeypatch.setattr("src.research.sources.news.get_json", lambda *a, **k: payload)
    docs = HackerNewsSource().search("chip")
    assert docs[0].meta["discussion"] == "https://news.ycombinator.com/item?id=999"


# ---------------------------------------------------------------- sweep

def test_hunt_survives_a_failing_source_and_reports_it(monkeypatch):
    from src.research import leads

    good = type("Good", (), {
        "name": "good",
        "available": lambda self: (True, "ok"),
        "search": lambda self, q, limit=10: [
            Document(url="https://ex.com/1", title="hit", text="t",
                     source="good", backend="b")
        ],
    })()
    broken = type("Broken", (), {
        "name": "broken",
        "available": lambda self: (True, "ok"),
        "search": lambda self, q, limit=10: (_ for _ in ()).throw(SourceError("HTTP 429")),
    })()
    gated = type("Gated", (), {
        "name": "gated",
        "available": lambda self: (False, "no token"),
        "search": lambda self, q, limit=10: [],
    })()

    monkeypatch.setattr(leads, "_searchable", lambda: iter([good, broken, gated]))

    sweep = leads.hunt("nvda")
    assert sweep.ran == {"good": 1}
    assert "429" in sweep.failed["broken"]
    assert sweep.skipped["gated"] == "no token"
    assert len(sweep.leads) == 1
    assert sweep.leads[0].meta["content_id"], "leads must be archived"

    payload = sweep.as_dict()
    assert payload["lead_count"] == 1
    assert payload["failed"]["broken"]


def test_hunt_deduplicates_across_sources(monkeypatch):
    from src.research import leads

    def make(name):
        return type(name, (), {
            "name": name,
            "available": lambda self: (True, "ok"),
            "search": lambda self, q, limit=10: [
                Document(url="https://ex.com/same", title="dupe", text="t",
                         source=name, backend="b")
            ],
        })()

    monkeypatch.setattr(leads, "_searchable", lambda: iter([make("a"), make("b")]))
    sweep = leads.hunt("q")
    assert len(sweep.leads) == 1
    assert sum(sweep.ran.values()) == 1
