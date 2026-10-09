"""
tests/test_research_pit_adversarial.py
======================================
The whole research report at instant T may depend only on what was knowable
at T.

For every input — price, volume, fundamentals, news, macro, FX, factor
returns, events, corporate actions, regime, the analogue history and the
models' own performance record — the information AFTER T is rewritten and
the report must be identical. The same input BEFORE T is then rewritten and
the report must change: a test that cannot fail proves nothing.

"Identical" compares a canonical form that drops only what legitimately
differs between two runs of the same query (stage timings and the wall-clock
build time of the evidence snapshot).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("exchange_calendars")

import research_fixtures as F  # noqa: E402
from intel_helpers import clustered_record, row  # noqa: E402
from src.intelligence import registry  # noqa: E402
from src.intelligence.research import engine  # noqa: E402
from src.intelligence.research.pit_data import (Event, FundamentalSnapshot,  # noqa: E402
                                                MacroObservation, NewsItem)

NY = ZoneInfo("America/New_York")
VOLATILE = {"timings_ms", "built_at", "snapshot_built_at", "evidence_snapshot", "cache"}
AFTER = pd.Timestamp("2026-09-16")


@pytest.fixture(autouse=True)
def _status_log(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "STATUS_LOG", tmp_path / "status.jsonl")


def canonical(x):
    if isinstance(x, dict):
        return {k: canonical(v) for k, v in x.items() if k not in VOLATILE}
    if isinstance(x, list):
        return [canonical(v) for v in x]
    return x


def run(data, symbol="AAPL", at=F.T):
    return canonical(engine.research(symbol, at=at, data=data, horizon_days=21))


_BASE: dict = {}


def base(symbol="AAPL"):
    if symbol not in _BASE:
        _BASE[symbol] = run(F.world(), symbol)
    return _BASE[symbol]


def mutate_frames(fn):
    f = F.build_frames()
    fn(f)
    return f


# ── future information: nothing may change ─────────────────────────────────

def fut_price(f):
    for v in f.values():
        v.loc[v.index >= AFTER, ["Open", "High", "Low", "Close"]] *= 3.0


def fut_volume(f):
    f["AAPL"].loc[f["AAPL"].index >= AFTER, "Volume"] *= 50


def fut_factors(f):
    for s in ("IWM", "IWD", "IWF", "MTUM", "QUAL", "USMV", "IEF", "HYG", "UUP", "DBC", "XLK"):
        f[s].loc[f[s].index >= AFTER, "Close"] *= 0.3


def fut_regime(f):
    f["SPY"].loc[f["SPY"].index >= AFTER, "Close"] *= 0.2


def fut_fx(f):
    f["USDJPY=X"].loc[f["USDJPY=X"].index >= AFTER, "Close"] *= 2.0


FUTURE_FRAMES = [fut_price, fut_volume, fut_factors, fut_regime, fut_fx]


@pytest.mark.parametrize("fn", FUTURE_FRAMES, ids=lambda f: f.__name__)
def test_future_market_data_cannot_change_the_report(fn):
    assert run(F.world(frames=mutate_frames(fn))) == base()


def test_future_fx_cannot_change_a_yen_stock_report():
    assert run(F.world(frames=mutate_frames(fut_fx)), "7203.T") == base("7203.T")


def test_future_fundamentals_cannot_change_the_report():
    extra = FundamentalSnapshot("AAPL", {"revenueGrowth": -0.9, "forwardPE": 99.0},
                                F.at_utc(2026, 9, 15, 22), "vendor-a")
    assert run(F.world(fundamentals=F.fundamentals() + [extra])) == base()


def test_future_news_cannot_change_the_report():
    later = [NewsItem(f"f{i}", "Apple (AAPL) collapses as fraud probe widens", f"o{i}",
                      f"https://f/{i}", "googlenews", F.T + timedelta(minutes=i + 1),
                      F.T + timedelta(minutes=i + 2), "AAPL") for i in range(20)]
    seen_late = [NewsItem("s1", "Apple (AAPL) earnings beat by a mile", "wire", "https://s/1",
                          "googlenews", F.T - timedelta(hours=2), F.T + timedelta(hours=1),
                          "AAPL")]
    assert run(F.world(news=F.news() + later + seen_late)) == base()


def test_future_macro_cannot_change_the_report():
    rel = MacroObservation("cpi_yoy", 9.9, "percent", F.T.date(), F.T + timedelta(hours=1),
                           None, "BLS", "monthly")
    assert run(F.world(macro=F.macro() + [rel])) == base()


def test_future_events_and_corporate_actions_cannot_change_the_report():
    evs = F.events() + [
        Event("EARNINGS:AAPL:x", "EARNINGS", "AAPL", F.at_utc(2026, 9, 18), "v", "HIGH",
              observed_at=F.T + timedelta(minutes=5)),
        Event("SPLIT:AAPL:x", "SPLIT", "AAPL", F.at_utc(2026, 9, 17), "v", "MEDIUM",
              outcome={"ratio": 10.0}),
    ]
    assert run(F.world(events=evs)) == base()


def test_future_model_performance_cannot_change_the_report():
    cut = F.T.astimezone().replace(tzinfo=None).isoformat()
    graded_later = clustered_record(40, 8, lambda k: 0.9, source="technical",
                                    start="2026-07-20",
                                    direction_fn=lambda k, j: "bear" if j % 2 else "bull")
    graded_later = [r for r in graded_later if (r["resolved_at"] or "") > cut]
    assert graded_later
    for k, r in enumerate(graded_later):
        r["external_ref"] = r["id"] = f"late{k}"
    new_after = [row(900, source="technical", subject="AAPL", direction="bear",
                     created="2026-09-17T22:00:00", horizon=10, resolved=False)]
    data = F.world(model_records=F.model_records() + graded_later + new_after)
    assert run(data) == base()


def test_duplicate_news_after_t_cannot_change_anything():
    dup = [NewsItem(f"d{i}", "Apple (AAPL) raises outlook after record iPhone demand",
                    f"late-outlet-{i}", f"https://d/{i}", "googlenews",
                    F.T + timedelta(hours=1), F.T + timedelta(hours=1, minutes=i), "AAPL")
           for i in range(10)]
    assert run(F.world(news=F.news() + dup)) == base()


# ── past information: the report must be able to change ───────────────────

def past_price(f):
    w = (f["AAPL"].index < AFTER) & (f["AAPL"].index >= "2026-09-01")
    f["AAPL"].loc[w, ["Open", "High", "Low", "Close"]] *= 1.2


def past_volume(f):
    f["AAPL"].loc[f["AAPL"].index == "2026-09-15", "Volume"] *= 10


def past_factors(f):
    w = (f["XLK"].index < AFTER) & (f["XLK"].index >= "2026-08-15")
    f["XLK"].loc[w, "Close"] *= np.linspace(1, 1.3, int(w.sum()))


def past_regime(f):
    w = (f["SPY"].index < AFTER) & (f["SPY"].index >= "2026-03-01")
    f["SPY"].loc[w, "Close"] *= np.linspace(1, 0.4, int(w.sum()))


def past_analogue_history(f):
    f["AAPL"].loc[f["AAPL"].index < "2025-01-01", "Close"] *= np.linspace(
        0.5, 1.0, int((f["AAPL"].index < "2025-01-01").sum()))


PAST_FRAMES = [past_price, past_volume, past_factors, past_regime, past_analogue_history]


@pytest.mark.parametrize("fn", PAST_FRAMES, ids=lambda f: f.__name__)
def test_past_market_data_can_change_the_report(fn):
    assert run(F.world(frames=mutate_frames(fn))) != base()


def test_past_fx_can_change_a_yen_stock_report():
    def past_fx(f):
        w = (f["USDJPY=X"].index < AFTER) & (f["USDJPY=X"].index >= "2026-08-15")
        f["USDJPY=X"].loc[w, "Close"] *= 1.15
    assert run(F.world(frames=mutate_frames(past_fx)), "7203.T") != base("7203.T")


def test_past_fundamentals_can_change_the_report():
    fs = F.fundamentals()
    fs[1] = FundamentalSnapshot("AAPL", {**fs[1].values, "revenueGrowth": -0.2},
                                fs[1].available_at, fs[1].source)
    assert run(F.world(fundamentals=fs)) != base()


def test_past_news_can_change_the_report():
    extra = NewsItem("p1", "Apple (AAPL) recall hits sales, shares slump", "wire",
                     "https://p/1", "googlenews", F.T - timedelta(hours=5),
                     F.T - timedelta(hours=4), "AAPL")
    assert run(F.world(news=F.news() + [extra])) != base()


def test_past_macro_can_change_the_report():
    m = F.macro()
    m[0] = MacroObservation("cpi_yoy", 5.0, "percent", m[0].observation_date,
                            None, m[0].retrieved_at, "BLS", "monthly")
    assert run(F.world(macro=m)) != base()


def test_past_events_can_change_the_report():
    evs = [e for e in F.events() if e.event_id != "EARNINGS:AAPL:2026-07-30"]
    assert run(F.world(events=evs)) != base()


def test_past_model_performance_can_change_the_report():
    graded = clustered_record(40, 8, lambda k: 0.9, source="technical", start="2026-04-01",
                              direction_fn=lambda k, j: "bear" if j % 2 else "bull")
    assert run(F.world(model_records=F.model_records() + graded)) != base()


# ── independence and boundaries ─────────────────────────────────────────────

def test_duplicate_news_before_t_does_not_add_evidence():
    dup = [NewsItem(f"e{i}", "Apple (AAPL) raises outlook after record iPhone demand",
                    f"extra-outlet-{i}", f"https://e/{i}", "googlenews",
                    F.at_utc(2026, 9, 14, 15, i), F.at_utc(2026, 9, 14, 15, i + 1), "AAPL")
           for i in range(10)]
    rep = run(F.world(news=F.news() + dup))
    news_items = [i for i in rep["evidence"]["items"] if i["group"] == "NEWS"]
    base_items = [i for i in base()["evidence"]["items"] if i["group"] == "NEWS"]
    assert len(news_items) == len(base_items)
    assert rep["thesis"]["direction"] == base()["thesis"]["direction"]


def test_provider_failure_never_borrows_another_value():
    rep = run(F.world(failures={"news": "down", "events": "down", "fundamentals": "down"}))
    comps = rep["snapshot"]["components"]
    assert comps["news"]["freshness"] == "MISSING"
    assert comps["fundamentals"]["value"] is None
    assert rep["thesis"]["quality_scorecard"]["EVENT_RISK"]["state"] == "UNKNOWN"
    assert not any(i["group"] in ("NEWS", "FUNDAMENTAL") for i in rep["evidence"]["items"])


def test_the_session_boundary_is_respected():
    inside = datetime(2026, 9, 15, 15, 59, tzinfo=NY)
    after = datetime(2026, 9, 15, 16, 1, tzinfo=NY)
    a = run(F.world(), at=inside)["snapshot"]["components"]["price"]["as_of"]
    b = run(F.world(), at=after)["snapshot"]["components"]["price"]["as_of"]
    assert (a, b) == ("2026-09-14", "2026-09-15")


def test_a_timezone_is_required():
    with pytest.raises(ValueError):
        engine.research("AAPL", at=datetime(2026, 9, 15, 21), data=F.world())


def test_an_unadjusted_split_is_detected_and_withheld():
    def unadjusted(f):
        f["AAPL"].loc[f["AAPL"].index >= "2026-09-01", ["Open", "High", "Low", "Close"]] /= 4.0
    split = Event("SPLIT:AAPL:2026-09-01", "SPLIT", "AAPL", F.at_utc(2026, 9, 1, 4), "v",
                  "MEDIUM", effective_time=F.at_utc(2026, 9, 1, 4), outcome={"ratio": 4.0})
    rep = run(F.world(frames=mutate_frames(unadjusted), events=F.events() + [split]))
    comps = rep["snapshot"]["components"]
    assert comps["price"]["provenance"]["unadjusted_split_suspected"][0]["ratio"] == 4.0
    assert comps["returns"]["freshness"] == "CONFLICTED"
    assert not any(i["group"] == "PRICE_DERIVED" and i["item_id"].startswith("price.")
                   for i in rep["evidence"]["items"])
    assert rep["analogues"]["status"] == "ANALOGUE_INSUFFICIENT_DATA"
