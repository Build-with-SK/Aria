"""
tests/test_research_live_semantics.py
=====================================
Two properties of LIVE research, found on real data on 2026-10-09 and pinned
here without touching a network:

  * a live query fixes its instant AFTER its inputs are fetched, so an
    earnings date fetched for the query is known at T (it was being hidden,
    by the visibility rule, because it was observed seconds after T);
  * a news source that does not cover the instrument (the eye does not watch
    it; there is no NewsAPI key) makes news MISSING — "we do not know" — never
    NO_DATA, which would claim nothing was published.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("exchange_calendars")

import research_fixtures as F  # noqa: E402
from src.intelligence.research import engine  # noqa: E402
from src.intelligence.research import pit_data as P  # noqa: E402
from src.risk.market_data import FrameMarketData  # noqa: E402


class OfflineLive(P.LiveResearchData):
    """The live class, with every vendor replaced by in-memory data."""

    def __init__(self, frames, *, event_time):
        super().__init__(refresh=False)
        self._frames = FrameMarketData(frames)
        self._event_time = event_time
        self.fetches = 0

    def history(self, symbol, at):
        return self._frames.history(symbol, at)

    def _events(self, symbol):
        self.fetches += 1
        return [P.Event(f"EARNINGS:{symbol}:next", "EARNINGS", symbol, self._event_time,
                        "stub", "HIGH", observed_at=datetime.now(timezone.utc))]

    def _fundamentals(self, symbol):
        return []

    def _macro(self):
        return []


def test_a_schedule_fetched_for_a_live_query_is_known_at_its_instant():
    """Real clock: the fixture frames run to the end of 2026, so "now" has a
    fresh last bar; the event is observed at fetch time, ten days out."""
    from datetime import timedelta
    frames = F.build_frames()
    data = OfflineLive(frames, event_time=datetime.now(timezone.utc) + timedelta(days=10))
    before = datetime.now(timezone.utc)
    rep = engine.research("AAPL", data=data, horizon_days=21, analogues=False,
                          use_cache=False)
    up = rep["snapshot"]["components"]["event_risk"]["value"]["upcoming"]
    assert [e["kind"] for e in up] == ["EARNINGS"]
    assert rep["thesis"]["quality_scorecard"]["EVENT_RISK"]["state"] == "HIGH"
    assert datetime.fromisoformat(rep["at"]) >= before   # T fixed after the fetch
    assert data.fetches == 1                             # prefetched once, then memoised


def test_a_news_source_that_does_not_cover_the_instrument_is_missing(monkeypatch, tmp_path):
    obs = tmp_path / "observations.jsonl"
    obs.write_text(json.dumps({"watch_id": "ticker:net", "title": "x", "seen_at":
                               "2026-09-14T12:00:00+00:00"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(P, "OBSERVATIONS", obs)
    monkeypatch.delenv("NEWS_API_KEY", raising=False)
    from src.data import enrichment
    monkeypatch.setattr(enrichment, "enrich", lambda s, *a, **k: {
        "fetched_at": "2026-09-15T12:00:00", "news": []})
    live = P.LiveResearchData(refresh=True)
    items = live.news("AAPL")
    assert items == []
    assert live.provider_status()["news_eye"] == "NOT_WATCHED"
    assert live.provider_status()["news_api"] == "NO_KEY"
    from src.intelligence.research import news as N
    out = N.analyse([], F.T, provider_status={k: v for k, v in live.provider_status().items()
                                               if k.startswith("news")})
    assert out["status"] == "MISSING"


def test_a_covering_source_with_nothing_found_is_no_data(monkeypatch, tmp_path):
    from src.intelligence.research import news as N
    out = N.analyse([], F.T, provider_status={"news_eye": "OK", "news_api": "NO_KEY"})
    assert out["status"] == "NO_DATA"
    out = N.analyse([], F.T, provider_status={"news_api": "OK_EMPTY"})
    assert out["status"] == "NO_DATA"
