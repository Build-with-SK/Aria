"""LSE databank client tests — key gating, caching, digests. All mocked."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import src.data.lse_data as lse


def test_no_key_no_calls(monkeypatch):
    monkeypatch.delenv("LSE_API_KEY", raising=False)
    called = []
    monkeypatch.setattr(lse.urllib.request, "urlopen",
                        lambda *a, **k: called.append(1))
    assert lse.available() is False
    assert lse.candles("AAPL") == []
    assert lse.series("US10Y") == []
    assert lse.usage() == {}
    assert called == []          # never touches the network without a key


def _mock_urlopen(payload, seen):
    import io

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=0):
        seen.append(req.full_url)
        return R(json.dumps(payload).encode())
    return opener


def test_get_with_key_and_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("LSE_API_KEY", "lse_live_test")
    monkeypatch.setattr(lse, "CACHE_DIR", tmp_path)
    seen = []
    rows = [{"ts": "2026-07-21", "symbol": "AAPL", "close": 310.0}]
    monkeypatch.setattr(lse.urllib.request, "urlopen", _mock_urlopen(rows, seen))
    out = lse.candles("AAPL", timeframe="1d", limit=10)
    assert out == rows
    assert "symbol=AAPL" in seen[0] and seen[0].startswith(lse.BASE_URL)
    # second call served from cache — no new request
    out2 = lse.candles("AAPL", timeframe="1d", limit=10)
    assert out2 == rows and len(seen) == 1


def test_yield_snapshot_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("LSE_API_KEY", "lse_live_test")
    monkeypatch.setattr(lse, "CACHE_DIR", tmp_path)
    rows = [{"symbol": "US10Y", "date": "2026-07-21", "value": 4.5}] + \
           [{"symbol": "US10Y", "date": "2026-06-20", "value": 4.2}] * 24
    monkeypatch.setattr(lse.urllib.request, "urlopen", _mock_urlopen(rows, []))
    snap = lse.yield_snapshot()
    assert snap["us10y"] == 4.5
    assert snap["us10y_chg_1m"] == 0.3


def test_http_error_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("LSE_API_KEY", "lse_live_test")
    monkeypatch.setattr(lse, "CACHE_DIR", tmp_path)

    def dead(req, timeout=0):
        raise lse.urllib.error.HTTPError(req.full_url, 429, "rate", {}, None)
    monkeypatch.setattr(lse.urllib.request, "urlopen", dead)
    assert lse.series("cpi_yoy") == []


def test_upcoming_events_filters_high_signal(tmp_path, monkeypatch):
    monkeypatch.setenv("LSE_API_KEY", "lse_live_test")
    monkeypatch.setattr(lse, "CACHE_DIR", tmp_path)
    rows = [
        {"datetime": "2026-07-22 12:30", "event": "CPI YoY"},
        {"datetime": "2026-07-22 14:00", "event": "Used Car Index"},
        {"datetime": "2026-07-23 18:00", "event": "FOMC Rate Decision"},
    ]
    monkeypatch.setattr(lse.urllib.request, "urlopen", _mock_urlopen(rows, []))
    events = lse.upcoming_us_events()
    names = [e["event"] for e in events]
    assert "CPI YoY" in names and "FOMC Rate Decision" in names
    assert "Used Car Index" not in names
