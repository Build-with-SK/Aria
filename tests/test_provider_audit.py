from __future__ import annotations

import io
import json
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest

from src.data.provider_audit import bse_candidate, catalogue_audit, verify_bse_candidate


def listing(**changes):
    return {"symbol": "SAIL.BO", "yahoo": "500113.BO", "name": "STEEL AUTHORITY OF INDIA LTD.",
            "exchange": "BSE", "asset_class": "equity", "currency": "INR", "isin": "INE114A01011", **changes}


def ticker(**changes):
    metadata = {"symbol": "SAIL.BO", "exchangeName": "BSE", "currency": "INR",
                "longName": "Steel Authority of India Limited", **changes}
    bars = pd.DataFrame({key: [100.] for key in ("Open", "High", "Low", "Close")}, index=pd.to_datetime(["2026-10-15"]))
    return SimpleNamespace(history=lambda **_: bars, get_history_metadata=lambda: metadata)


def test_audit_visits_every_asset_without_claiming_live_coverage():
    rows = [listing(), listing(symbol="AAPL", yahoo="AAPL", exchange="NASDAQ", currency="USD")]
    report = catalogue_audit(rows)
    assert report["total"] == 2 and len(report["records"]) == 2
    assert report["live_checked"] == 0
    assert report["issue_counts"] == {"bse_numeric_provider_needs_live_check": 1}
    assert all(row["live_status"] == "not_checked" for row in report["records"])


@pytest.mark.parametrize("changes", [{"exchange": "NSE"}, {"yahoo": "SAIL.BO"}, {"symbol": "../SAIL.BO"}, {"asset_class": "futures"}])
def test_repair_candidates_never_guess_another_venue_or_unsafe_path(changes):
    assert bse_candidate(listing(**changes)) is None


def test_verification_requires_identity_but_does_not_promise_analysis_history():
    result = verify_bse_candidate(listing(), lambda _: ticker())
    assert result["verified"] and result["bars"] == 1
    assert result["candidate"] == "SAIL.BO"


@pytest.mark.parametrize("changes", [{"symbol": "SAIL"}, {"exchangeName": "NSI"}, {"currency": "USD"},
                                    {"longName": "SailPoint Inc"}, {"longName": ""}, {"isin": "DIFFERENT"}])
def test_candles_alone_never_verify_a_mapping(changes):
    obj = ticker(**changes)
    obj.get_info = lambda: {}
    assert not verify_bse_candidate(listing(), lambda _: obj)["verified"]


@pytest.fixture
def manager(tmp_path, monkeypatch):
    from src.data import universe
    from src.core import identity
    monkeypatch.setattr(universe, "DB_PATH", tmp_path / "universe.db")
    monkeypatch.setattr(universe, "ROOT", tmp_path)
    monkeypatch.setattr(universe, "DOSSIER_DIR", tmp_path / "data/cache/dossiers")
    # The real identity guard must see this same test catalogue.
    monkeypatch.setattr(identity, "UNIVERSE_DB", tmp_path / "universe.db")
    obj = universe.UniverseManager()
    obj._ensure_currency_column()
    with obj._conn() as conn:
        row = listing()
        conn.execute("INSERT INTO symbols(symbol,yahoo,name,exchange,asset_class,currency,isin,sector,has_fno) VALUES(?,?,?,?,?,?,?,?,?)",
                     tuple(row[k] for k in ("symbol", "yahoo", "name", "exchange", "asset_class", "currency", "isin")) + ("Metals", 1))
    return obj


def test_maintenance_repair_invalidates_display_caches_and_preserves_listing(manager):
    from src.data import universe
    path = universe.DOSSIER_DIR / "SAIL.BO.json"
    path.write_text("{}")
    cache = universe.ROOT / "data/cache/v5"
    cache.mkdir(parents=True)
    for name in ("SAIL.BO__1d.csv", "SAIL.BO__1d.meta.json", "OTHER__1d.csv"):
        (cache / name).write_text("old")
    with manager._conn() as conn:
        conn.execute("INSERT INTO quotes(symbol,price) VALUES('SAIL.BO',100)")
    before = manager._lookup("SAIL.BO")
    assert manager.repair_bse_provider("SAIL.BO", lambda _: ticker())["applied"]
    after = manager._lookup("SAIL.BO")
    assert after["yahoo"] == "SAIL.BO"
    assert all(before[k] == after[k] for k in before if k not in ("yahoo", "updated_at"))
    assert not path.exists() and not (cache / "SAIL.BO__1d.csv").exists()
    assert (cache / "OTHER__1d.csv").exists()
    with manager._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 0


def test_identity_collision_refuses_repair(manager):
    with manager._conn() as conn:
        conn.execute("INSERT INTO symbols(symbol,yahoo,name,isin) VALUES('OTHER','SAIL.BO','Other company','OTHER-ISIN')")
    result = manager.repair_bse_provider("SAIL.BO", lambda _: ticker())
    assert not result["applied"] and "identity guard" in result["reason"]
    assert manager._lookup("SAIL.BO")["yahoo"] == "500113.BO"


def test_refresh_preserves_verified_same_isin_correction(manager, monkeypatch):
    from src.data import universe
    assert manager.repair_bse_provider("SAIL.BO", lambda _: ticker())["applied"]
    csv = "FinInstrmTp,SctySrs,TckrSymb,FinInstrmId,FinInstrmNm,ISIN\nEQ,A,SAIL,500113,STEEL AUTHORITY OF INDIA LTD.,INE114A01011\n"
    monkeypatch.setattr(universe.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(csv.encode()))
    assert manager._load_bse_equities() == 1
    assert manager._lookup("SAIL.BO")["yahoo"] == "SAIL.BO"


def test_listing_changed_during_verification_is_not_overwritten(manager):
    def factory(_):
        with manager._conn() as conn:
            conn.execute("UPDATE symbols SET name='Changed company' WHERE symbol='SAIL.BO'")
        return ticker()
    result = manager.repair_bse_provider("SAIL.BO", factory)
    assert not result["applied"] and "changed" in result["reason"]


@pytest.mark.parametrize("provider,expected", [("RELIANCE.NS", "INR"), ("GBPJPY=X", "JPY"),
                                               ("BTC-USD", "USD"), ("BA.L", "GBp")])
def test_missing_stored_currency_uses_provider_identity(manager, provider, expected):
    assert manager._currency_for({"yahoo": provider, "currency": None}) == expected
