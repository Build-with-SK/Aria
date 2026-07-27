"""The chat ticker detector: fires on real stock mentions, stays quiet on
ordinary questions. Pure — no network (only _detect_query_ticker's regex path;
the company-name fallback that hits the DB is skipped when regex matches)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# import guarded: backend.main pulls FastAPI etc. Skip cleanly if unavailable.
import pytest
pytest.importorskip("fastapi")

from backend.main import _detect_query_ticker


def test_explicit_ticker_and_market_hint():
    assert _detect_query_ticker("should I sell my SAIL shares on NSE?") == ("SAIL", "NSE")
    assert _detect_query_ticker("tell me about TATAMOTORS from bse") == ("TATAMOTORS", "BSE")
    assert _detect_query_ticker("how's AAPL doing") == ("AAPL", None)


def test_yahoo_style_ticker_passthrough():
    # explicit suffix carries the market; the prefer-hint is then irrelevant
    assert _detect_query_ticker("look up SAIL.NS")[0] == "SAIL.NS"
    assert _detect_query_ticker("price of BTC-USD") == ("BTC-USD", None)


def test_market_hint_words():
    t, m = _detect_query_ticker("what about RELIANCE on the indian market")
    assert t == "RELIANCE" and m == "NSE"


def test_no_false_trigger_on_plain_questions():
    # no uppercase ticker token; company fallback finds nothing meaningful
    for q in ["how is the market today", "what is my P&L", "should I buy or sell"]:
        t, _ = _detect_query_ticker(q)
        # may be None (good) — must never be a stopword
        assert t is None or t not in {"BUY", "SELL", "HOLD", "P&L", "USD"}


def test_stopwords_never_returned():
    t, _ = _detect_query_ticker("USD GBP INR VIX DXY")
    assert t is None
