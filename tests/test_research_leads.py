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


# --------------------------------------------------------------- throttling

def test_same_host_requests_are_spaced(monkeypatch):
    """A blink fans out across watches at once; without spacing that is a
    burst, and a burst is what gets a 429."""
    import time as time_module

    from src.research import http

    monkeypatch.setattr(http, "_last_request", {})
    slept = []
    monkeypatch.setattr(http.time, "sleep", lambda s: slept.append(s))

    clock = {"t": 1000.0}
    monkeypatch.setattr(http.time, "monotonic", lambda: clock["t"])

    http._throttle("https://www.reddit.com/a")     # first call is free
    assert slept == []

    # A second call at the same instant must wait out the host's interval.
    # The fake clock does not advance on sleep, so break out after one wait.
    def advancing_sleep(seconds):
        slept.append(seconds)
        clock["t"] += seconds
    monkeypatch.setattr(http.time, "sleep", advancing_sleep)

    http._throttle("https://www.reddit.com/b")
    assert slept and abs(sum(slept) - 2.0) < 0.01, "reddit spacing is 2s"

    assert time_module is not None      # imported for clarity, not used


def test_throttling_is_per_host(monkeypatch):
    from src.research import http

    monkeypatch.setattr(http, "_last_request", {})
    monkeypatch.setattr(http.time, "monotonic", lambda: 500.0)
    slept = []
    monkeypatch.setattr(http.time, "sleep", lambda s: slept.append(s))

    http._throttle("https://www.reddit.com/a")
    http._throttle("https://news.google.com/rss")   # different host, no wait
    assert slept == []


# ------------------------------------------------------ ticker disambiguation

def test_ticker_search_is_restricted_to_finance_subreddits(monkeypatch):
    """ERX site-wide returned a Fallout mod. A symbol only means a symbol
    where people discuss symbols."""
    seen = {}

    def fake_get(url, *a, **k):
        seen["url"] = url
        return b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"/>'

    monkeypatch.setattr("src.research.sources.reddit._oauth_token", lambda: None)
    monkeypatch.setattr("src.research.sources.reddit.get", fake_get)

    RedditSource().search_ticker("ERX")
    assert "wallstreetbets" in seen["url"] and "stocks" in seen["url"]
    assert "restrict_sr=on" in seen["url"]


def test_google_news_qualifies_a_bare_ticker(monkeypatch):
    seen = {}

    def fake_get(url, *a, **k):
        seen["url"] = url
        return b'<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'

    monkeypatch.setattr("src.research.sources.news.get", fake_get)
    GoogleNewsSource().search_ticker("NET")
    assert "stock" in seen["url"], "a bare symbol is ambiguous to a news index"


def test_sources_without_an_override_fall_back_to_plain_search():
    from src.research.base import Source

    called = {}

    class Plain(Source):
        name = "plain"

        def can_handle(self, url):
            return False

        def fetch(self, url):
            raise NotImplementedError

        def search(self, query, limit=15):
            called["query"] = query
            return []

    Plain().search_ticker("NVDA")
    assert called["query"] == "NVDA"


def test_hackernews_declines_ticker_searches():
    """HN has no ticker context. NET returns Netflix and Netscape there, so
    silence beats filling the slot with noise."""
    assert HackerNewsSource().search_ticker("NET") == []


def test_edgar_resolves_tickers_through_the_official_cik_map(monkeypatch):
    import src.research.sources.markets as markets

    monkeypatch.setattr(markets, "_ticker_map", {})
    calls = []

    def fake_get_json(url, *a, **k):
        calls.append(url)
        if "company_tickers" in url:
            return {"0": {"ticker": "NET", "cik_str": 1477333}}
        return {"hits": {"hits": [{
            "_id": "0001477333-26-000030:net-10q.htm",
            "_source": {"file_type": "10-Q", "file_date": "2026-08-01",
                        "display_names": ["Cloudflare, Inc. (NET)"]},
        }]}}

    monkeypatch.setattr(markets, "get_json", fake_get_json)

    docs = markets.EdgarSource().search_ticker("NET")
    assert "ciks=0001477333" in calls[-1], "must query by CIK, not by the word"
    assert docs[0].meta["form"] == "10-Q"
    assert "Cloudflare" in docs[0].title


def test_edgar_says_nothing_for_a_ticker_the_sec_does_not_list(monkeypatch):
    """ERX is a leveraged ETF with no CIK. Zero results beats every filing
    containing the word 'erx'."""
    import src.research.sources.markets as markets

    monkeypatch.setattr(markets, "_ticker_map", {"NET": "0001477333"})
    monkeypatch.setattr(markets, "get_json",
                        lambda *a, **k: pytest.fail("must not query EDGAR"))
    assert markets.EdgarSource().search_ticker("ERX") == []


def test_hunt_asks_sources_for_the_symbol_when_given_a_ticker(monkeypatch):
    from src.research import leads

    calls = []
    source = type("S", (), {
        "name": "s",
        "available": lambda self: (True, "ok"),
        "search": lambda self, q, limit=10: calls.append(("search", q)) or [],
        "search_ticker": lambda self, t, limit=10: calls.append(("ticker", t)) or [],
    })()
    monkeypatch.setattr(leads, "_searchable", lambda: iter([source]))
    monkeypatch.setattr(leads.StockTwitsSource if hasattr(leads, "StockTwitsSource")
                        else leads, "_unused", None, raising=False)

    import src.research.sources.markets as markets
    monkeypatch.setattr(markets.StockTwitsSource, "symbol",
                        lambda self, t, limit=30: Document(
                            url="u", title="t", text="", source="stocktwits",
                            backend="b"))

    leads.hunt("ignored phrase", ticker="nvda")
    assert ("ticker", "NVDA") in calls
    assert not any(kind == "search" for kind, _ in calls)


def test_a_429_starts_a_cooldown_so_the_rest_of_the_blink_stops_asking(monkeypatch):
    """Five watches must not each rediscover that Reddit is throttling us."""
    import urllib.error

    from src.research import http

    monkeypatch.setattr(http, "_last_request", {})
    monkeypatch.setattr(http, "_cooldown_until", {})
    monkeypatch.setattr(http.time, "sleep", lambda s: None)

    attempts = []

    def always_429(req, timeout=None):
        attempts.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, None)

    monkeypatch.setattr(http.urllib.request, "urlopen", always_429)

    with pytest.raises(http.SourceError):
        http.get("https://www.reddit.com/search.rss?q=BILL")
    first_round = len(attempts)
    assert first_round == 3, "one attempt plus two retries"

    # Every later watch in the same blink must fail instantly, without asking.
    for ticker in ("BNO", "ERX", "NET", "WEAT"):
        with pytest.raises(http.Throttled):
            http.get(f"https://www.reddit.com/search.rss?q={ticker}")
    assert len(attempts) == first_round, "cooldown must prevent further requests"

    assert http.cooling_down("https://www.reddit.com/x") > 0
    assert http.cooling_down("https://news.google.com/rss") == 0, "per host"


def test_cooldown_expires(monkeypatch):
    from src.research import http

    monkeypatch.setattr(http, "_cooldown_until", {})
    clock = {"t": 100.0}
    monkeypatch.setattr(http.time, "monotonic", lambda: clock["t"])

    http._begin_cooldown("https://www.reddit.com/x", seconds=60)
    assert http.cooling_down("https://www.reddit.com/x") == 60
    clock["t"] += 61
    assert http.cooling_down("https://www.reddit.com/x") == 0
