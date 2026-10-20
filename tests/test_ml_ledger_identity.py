"""
tests/test_ml_ledger_identity.py
================================
An ML call is recorded on the instrument the model was trained and priced on.

THE DEFECT (live ledger, 2026-10-17 23:40:28)
---------------------------------------------
The ML pass trains on signals.json tickers, which are provider symbols fetched
verbatim: `AAL` is American Airlines. `record_ml_predictions` handed the bare
ticker to `ledger.record_prediction`, which stamped identity through the
symbol master, where the display row `AAL` is Anglo American (AAL.L). So 27
rows on ten tickers (AAL, AZN, BA, BP, EDV, GSK, JD, PRU, RIO, SHEL) carried a
London identity and venue on a US price — AAL.L at $11.88. The grader then
priced AAL.L, found 4,000p against 11.88 and could only skip the call.

The fix passes the identity in: the recorder states the signals-namespace
provider symbol, the ledger stamps it (SOURCE_STATED) where it differs from the
master's reading, and the grader prices the stamped identity.

Everything here writes to a temp ledger and a temp symbol master.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "universe_fixture.py"


@pytest.fixture
def spine(tmp_path, monkeypatch):
    """A temp ledger and the small, true fixture symbol master — in which
    AAL, BA, JD and EDV are display rows for their London listings."""
    spec = importlib.util.spec_from_file_location("ml_identity_universe", FIXTURE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.build(tmp_path / "universe.db")
    from src.core import identity
    monkeypatch.setattr(identity, "UNIVERSE_DB", tmp_path / "universe.db")
    monkeypatch.setenv("ARIA_CORE_DB", str(tmp_path / "spine.db"))
    from src.core import bus as bus_mod
    monkeypatch.setattr(bus_mod, "_initialised", False)
    monkeypatch.setattr(bus_mod, "_subscribers", [])
    from src.core import ledger as ledger_mod
    monkeypatch.setattr(ledger_mod, "_ready", False)
    return ledger_mod


def _payload(ticker: str, p_up: float = 0.30, horizon: int = 5) -> dict:
    return {ticker: {"horizons": {str(horizon): {
        "horizon_days": horizon, "bullish_prob": p_up}}}}


def _record(ticker: str, price: float, *, p_up: float = 0.30, horizon: int = 5,
            asof: str = "2026-10-16", created: str = "2026-10-17T23:40:28") -> dict:
    from src.intelligence.recorders import record_ml_predictions
    return record_ml_predictions(_payload(ticker, p_up, horizon),
                                 data_as_of={ticker: asof},
                                 last_close={ticker: price},
                                 trainer_version="v6.0", created_at=created)


def _row(ledger, subject: str) -> dict:
    rows = [r for r in ledger.predictions(limit=50, source="ml-ensemble")
            if r["subject"] == subject]
    assert len(rows) == 1, rows
    return rows[0]


# ── the write path ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("bare,london,us_price", [
    ("AAL", "AAL.L", 11.88),      # American Airlines, not Anglo American
    ("BA", "BA.L", 190.21),       # Boeing, not BAE Systems
    ("JD", "JD.L", 27.01),        # JD.com, not JD Sports
    ("EDV", "EDV.L", 57.60),      # Vanguard Extended Duration, not Endeavour Mining
])
def test_an_ml_call_on_a_colliding_ticker_records_the_us_listing(spine, bare, london,
                                                                  us_price):
    from src.core import identity
    # The trap is set: alone, the master reads the bare ticker as London.
    assert identity.describe(bare).provider_symbol == london

    assert _record(bare, us_price)["recorded"] == 1
    r = _row(spine, bare)
    assert r["subject"] == bare
    assert r["provider_symbol"] == bare, f"{bare} was stamped {r['provider_symbol']}"
    assert r["identity_basis"] == "SOURCE_STATED"
    assert r["price_at"] == pytest.approx(us_price)
    assert r["venue"] == "XNYS", f"a US call was filed on {r['venue']}"


@pytest.mark.parametrize("ticker,basis", [
    ("AAPL", "UNAMBIGUOUS"),       # one listing anywhere
    ("MRK", "MASTER_RESOLVED"),    # MRK.DE shares the root; the master still picks MRK
])
def test_a_non_colliding_ticker_is_recorded_exactly_as_before(spine, ticker, basis):
    """Where the master already names the instrument the model priced, the
    stated symbol changes nothing: same identity, same basis, same price,
    same venue as a write that states no symbol at all."""
    assert _record(ticker, 150.0)["recorded"] == 1
    ml = _row(spine, ticker)
    spine.record_prediction(subject=ticker, claim="control", source="control",
                            direction="bear", horizon_days=5, price_at=150.0,
                            created_at="2026-10-17T23:40:28", announce=False)
    control = next(r for r in spine.predictions(limit=50, source="control"))
    assert (ml["provider_symbol"], ml["identity_basis"]) == (ticker, basis)
    for col in ("provider_symbol", "identity_basis", "price_at", "venue"):
        assert ml[col] == control[col], col


def test_an_unresolvable_subject_is_still_recorded(spine):
    """Fail-open is kept: no stated symbol and nothing in the master is a
    claim with a NULL identity, never a lost claim."""
    pid = spine.record_prediction(subject="ZZQX", claim="bull", direction="bull",
                                  horizon_days=5, price_at=10.0, announce=False)
    assert pid
    r = spine.predictions(limit=5)[0]
    assert (r["subject"], r["provider_symbol"], r["identity_basis"]) == ("ZZQX", None, None)


def test_a_failing_identity_lookup_never_blocks_the_write(spine, monkeypatch):
    from src.core import identity

    def boom(*a, **k):
        raise RuntimeError("symbol master unreadable")
    monkeypatch.setattr(identity, "describe", boom)
    assert _record("AAL", 11.88)["recorded"] == 1
    r = _row(spine, "AAL")
    # The recorder still said which instrument it priced.
    assert (r["provider_symbol"], r["identity_basis"]) == ("AAL", "SOURCE_STATED")
    assert spine.record_prediction(subject="BA", claim="bull", direction="bull",
                                   horizon_days=5, price_at=190.0, source="desk",
                                   announce=False)
    desk = spine.predictions(limit=5, source="desk")[0]
    assert (desk["provider_symbol"], desk["identity_basis"]) == (None, None)


# ── the grader prices what was stamped ─────────────────────────────────────

def test_the_grader_prices_the_us_listing_a_us_call_was_made_on(spine, monkeypatch):
    made = datetime.now() - timedelta(days=40)
    asof = (made - timedelta(days=1)).date().isoformat()
    _record("AAL", 11.88, p_up=0.70, horizon=1, asof=asof,
            created=made.isoformat(timespec="seconds"))
    asked = []

    def prices(ticker, on):
        asked.append(ticker)
        if ticker == "AAL.L":
            return 4000.0                       # Anglo American, in pence
        if ticker == "AAL":
            return 11.90 if on == asof else 12.60
        return None
    monkeypatch.setattr(spine, "_price_on", prices)
    out = spine.resolve_due()
    assert out["resolved"] == 1 and out["unresolvable"] == 0
    r = _row(spine, "AAL")
    assert r["correct"] == 1
    assert r["actual_return"] == pytest.approx((12.60 - 11.90) / 11.90, abs=1e-4)
    assert set(asked) == {"AAL"}, f"the grader priced {set(asked)}"


def test_an_unstamped_row_is_still_resolved_through_the_master(spine, monkeypatch):
    """Rows written before identity was stamped keep the old rule: the master
    says `BA` is BA.L, and that is what gets priced."""
    made = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
    pid = spine.record_prediction(subject="BA", claim="bull", direction="bull",
                                  horizon_days=10, price_at=2200.0, source="technical",
                                  created_at=made, announce=False)
    with spine.connect() as conn:
        conn.execute("UPDATE predictions SET provider_symbol=NULL, identity_basis=NULL "
                     "WHERE id=?", (pid,))
    asked = []

    def prices(ticker, on):
        asked.append(ticker)
        return 2210.0 if len(asked) == 1 else 2300.0
    monkeypatch.setattr(spine, "_price_on", prices)
    assert spine.resolve_due()["resolved"] == 1
    assert set(asked) == {"BA.L"}
