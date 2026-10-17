"""
tests/test_pipeline_sentiment.py
================================
main.run_sentiment handed analyze_all_sentiment the ticker LIST, but it reads
the universe from the CONFIG — so every pipeline run from 2026-07-22 logged
"Sentiment analysis failed: 'list' object has no attribute 'get'" and the
strategies always received {}. The strategies also read dicts (`score`,
`headlines`), not SentimentResult dataclasses.
"""
import pytest

pytest.importorskip("yaml")

import main  # noqa: E402
from src.sentiment import sentiment_analyzer as sa  # noqa: E402


def _result(ticker, score, headlines):
    # A scored result came from at least one article: the analyzer returns
    # score 0 with n_articles 0 when it finds none (see _no_news below).
    return sa.SentimentResult(ticker=ticker, n_articles=max(1, len(headlines)),
                              avg_score=score / 100,
                              recency_weighted_score=score / 100, sentiment_score=score,
                              label="Bullish" if score > 0 else "Bearish", top_headlines=headlines)


def test_the_analyzer_is_given_the_config_and_the_strategies_get_dicts(monkeypatch):
    seen = {}

    def fake(config):
        seen["arg"] = config
        return {"ACME": _result("ACME", 42.0, ["ACME agrees merger with Widgets"]),
                "BUST": _result("BUST", -30.0, [])}

    monkeypatch.setattr(sa, "analyze_all_sentiment", fake)
    config = {"universe": {"equities": [{"ticker": "ACME"}, {"ticker": "BUST"}]}}
    out = main.run_sentiment(["ACME", "BUST"], config)
    assert seen["arg"] is config
    assert out["ACME"]["score"] == 42.0 and out["BUST"]["score"] == -30.0
    assert out["ACME"]["headlines"] == ["ACME agrees merger with Widgets"]
    assert out["ACME"]["article_count"] == 1 and out["ACME"]["label"] == "Bullish"


def test_the_real_analyzer_accepts_the_config_shape(monkeypatch):
    """No network: the per-ticker fetch is replaced; only the call contract is real."""
    monkeypatch.setattr(sa, "analyze_sentiment",
                        lambda ticker, name="": _result(ticker, 10.0, [f"{name} news"]))
    config = {"universe": {"equities": [{"ticker": "ACME", "name": "Acme Corp"}]}}
    out = main.run_sentiment(["ACME"], config)
    assert out["ACME"]["headlines"] == ["Acme Corp news"]


def test_event_driven_finds_an_ma_headline_and_distressed_reads_the_sign():
    from src.strategies import event_driven
    record = main._sentiment_record(_result("ACME", -5.0, ["Acme to be acquired in merger deal"]))
    event = event_driven._detect_ma_from_sentiment("ACME", {"ACME": record})
    assert event is not None and event.event_type == "ma_target"
    assert record["score"] < 0          # distressed.py reads the sign of `score`


# ── the news source: Ticker.news is empty in yfinance 1.3.0 ──────────────────

class _FakeYF:
    """Stands in for the yfinance module: Ticker(...).news and Search(...).news."""

    def __init__(self, ticker_news, search_news):
        self._t, self._s, self.searched = ticker_news, search_news, []

    def Ticker(self, sym):
        return type("T", (), {"news": self._t})()

    def Search(self, query, news_count=8):
        self.searched.append(query)
        return type("S", (), {"news": self._s})()


def test_search_news_is_used_when_ticker_news_is_empty_and_kept_only_if_tagged(monkeypatch):
    fake = _FakeYF([], [
        {"title": "HSBC beats on profit, raises buyback", "publisher": "Wire",
         "providerPublishTime": 1792057976, "relatedTickers": ["HSBC"]},
        {"title": "HSBC adjusts Nike price target", "publisher": "Wire",
         "providerPublishTime": 1792057976, "relatedTickers": ["NKE"]},
        {"title": "HSBC India commits funds to literacy", "publisher": "PR",
         "providerPublishTime": 1792057976, "relatedTickers": []},
    ])
    monkeypatch.setattr(sa, "yf", fake)
    arts = sa._fetch_yfinance_news("HSBC")
    assert fake.searched == ["HSBC"]
    assert [a.title for a in arts] == ["HSBC beats on profit, raises buyback"]
    assert arts[0].published.startswith("2026-") and arts[0].source == "Wire"


def test_ticker_news_when_present_is_used_without_searching(monkeypatch):
    fake = _FakeYF([{"content": {"title": "Acme wins contract", "pubDate": "2026-10-16T12:00:00Z",
                                 "provider": {"displayName": "Wire"}}}], [])
    monkeypatch.setattr(sa, "yf", fake)
    arts = sa._fetch_yfinance_news("ACME")
    assert [a.title for a in arts] == ["Acme wins contract"] and fake.searched == []


def test_no_tagged_news_anywhere_is_unavailable_end_to_end(monkeypatch):
    monkeypatch.setattr(sa, "yf", _FakeYF([], [
        {"title": "Markets wobble", "relatedTickers": ["^GSPC"], "providerPublishTime": 1792057976}]))
    monkeypatch.setattr(sa, "FEEDPARSER_AVAILABLE", False)
    rec = main._sentiment_record(sa.analyze_sentiment("QUIET", "Quiet Co"))
    assert rec["available"] is False and rec["score"] is None


# ── missing news is unavailable, not neutral (2026-10-16 launch sprint) ──────
# The analyzer reports a ticker with no articles — or whose fetch raised — as
# label "Neutral", score 0.0, the same as news that genuinely nets to zero.

def _no_news(ticker, warning="No news articles found"):
    return sa.SentimentResult(ticker=ticker, n_articles=0, avg_score=0.0,
                              recency_weighted_score=0.0, sentiment_score=0.0,
                              label="Neutral", warning=warning)


def test_a_ticker_with_no_articles_is_unavailable_not_neutral():
    rec = main._sentiment_record(_no_news("QUIET"))
    assert rec["available"] is False
    assert rec["score"] is None and rec["label"] == "Unavailable"
    assert rec["article_count"] == 0 and rec["headlines"] == []
    assert "no news" in rec["warning"].lower()


def test_news_that_nets_to_zero_is_a_real_neutral_reading():
    rec = main._sentiment_record(sa.SentimentResult(
        ticker="FLAT", n_articles=3, avg_score=0.0, recency_weighted_score=0.0,
        sentiment_score=0.0, label="Neutral", top_headlines=["a", "b", "c"]))
    assert rec["available"] is True and rec["score"] == 0.0 and rec["label"] == "Neutral"


def test_a_failed_fetch_is_unavailable_and_logged(monkeypatch, caplog):
    """analyze_all_sentiment turns a per-ticker exception into a no-article
    result; the pipeline must say how many were unavailable, and warn when
    there was no news at all."""
    def boom(ticker, name=""):
        raise ConnectionError("news feed unreachable")

    monkeypatch.setattr(sa, "analyze_sentiment", boom)
    config = {"universe": {"equities": [{"ticker": "AAA"}, {"ticker": "BBB"}]}}
    with caplog.at_level("INFO"):
        out = main.run_sentiment(["AAA", "BBB"], config)
    assert {t: r["available"] for t, r in out.items()} == {"AAA": False, "BBB": False}
    assert all(r["score"] is None for r in out.values())
    assert "unreachable" in out["AAA"]["warning"]
    text = caplog.text
    assert "0 with news, 2 unavailable" in text
    assert "NO ticker had any news" in text


def test_strategies_do_not_read_unavailable_sentiment_as_zero():
    import pandas as pd
    from src.strategies import distressed, event_driven
    unavailable = {"QUIET": main._sentiment_record(_no_news("QUIET"))}

    # event_driven: a +5% day with no news is not a post-earnings-drift signal
    df = pd.DataFrame({"Close": [100.0, 100.0, 100.0, 100.0, 105.0]})
    assert event_driven._post_earnings_drift_signal("QUIET", df, unavailable) is None

    # distressed: the screened name reports no sentiment, not a 0.0 reading
    closes = pd.DataFrame({"Close": [200.0] * 200 + [90.0] * 52},
                          index=pd.date_range("2025-01-01", periods=252, freq="B"))
    info = {"totalAssets": 100.0, "totalDebt": 900.0, "totalStockholderEquity": 50.0,
            "freeCashflow": -10.0, "marketCap": 10.0, "totalRevenue": 5.0,
            "ebitda": -5.0, "retainedEarnings": -50.0, "totalCurrentAssets": 10.0,
            "totalCurrentLiabilities": 40.0, "totalLiab": 900.0}
    name = distressed._screen_ticker("QUIET", closes, info, unavailable, {})
    assert name is not None, "the fixture must be distressed enough to screen"
    assert name.sentiment_score is None
    assert name.recommended_direction != "short", "a short needs NEGATIVE news, not none"
