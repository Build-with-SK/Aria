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
    return sa.SentimentResult(ticker=ticker, n_articles=len(headlines), avg_score=score / 100,
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
