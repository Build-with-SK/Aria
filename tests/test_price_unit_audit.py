"""
tests/test_price_unit_audit.py
==============================
Audit-mode tests for the GBp/GBP price-unit defect. NOTHING here modifies
production data, the schema, or historical predictions.

WHAT THESE PIN
--------------
The pence model itself, and the defect that used to sit on top of it.

There was an xfail here — the 100x error, held visible so the suite stayed
honest without going red on a known defect. It is GONE, because the defect is
fixed and the test that pinned it now passes for real. That flip is the proof,
and this docstring says so rather than leaving a description of a marker that
no longer exists.

THE DEFECT, IN ONE LINE — now closed
------------------------------------
ARIA contains two independent universes that both key on a bare ticker, and
they disagree about what four of those tickers mean:

    configs/universe.yaml   AAL -> American Airlines, NASDAQ, USD  (signal pipeline)
    src/data/universe.py    AAL -> AAL.L, Anglo American, LSE, GBp (symbol master)

signals.json holds a US price (13.82) under a key whose currency once resolved
— correctly, from the symbol master — to GBp, so anything converting that pair
divided a dollar price by 100. Identity is now resolved from the PROVIDER
symbol, which carries its own venue, so the two readings can no longer be
joined by accident. See tests/test_identity_namespaces.py for the full closure.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.data import currency as cur

# The four tickers where the two universes name different companies. Verified
# against the provider on 2026-08-24, and against configs/universe.yaml.
COLLIDING = {
    # bare : (signal-pipeline company, symbol-master provider symbol + company)
    "AAL": ("American Airlines", "AAL.L", "Anglo American plc"),
    "BA": ("Boeing", "BA.L", "BAE Systems plc"),
    "JD": ("JD.com", "JD.L", "JD Sports Fashion Plc"),
    "EDV": ("Vanguard Ext Duration Treasury", "EDV.L", "Endeavour Mining plc"),
}


@pytest.fixture(autouse=True)
def _clear_ccy_cache():
    """native_currency memoises; these tests care about resolution, not cache."""
    cur._symbol_ccy_cache.clear()
    yield
    cur._symbol_ccy_cache.clear()


# ── regression: the pence MODEL itself is correct and must stay correct ─────

def test_pence_converts_at_one_hundred_to_the_pound():
    """100 GBp is £1.00, not £100. This is the arithmetic the rest of the
    system depends on, and it is right today."""
    assert cur.to_base(100.0, "GBp", "GBP") == pytest.approx(1.0)
    assert cur.to_base(2100.0, "GBp", "GBP") == pytest.approx(21.0)
    # ...and the round trip back into pence.
    assert cur.convert(1.0, "GBP", "GBp") == pytest.approx(100.0, rel=1e-6)


def test_a_london_provider_symbol_resolves_to_pence():
    """`.L` means pence. Resolution by SUFFIX is the path that cannot be
    confused by a display-symbol collision, because a provider symbol names
    exactly one listing."""
    for _, (_, provider, _) in COLLIDING.items():
        assert cur.native_currency(provider) == "GBp", provider
        cur._symbol_ccy_cache.clear()


def test_a_us_provider_symbol_resolves_to_dollars():
    for sym in ("AAPL", "MSFT", "NVDA"):
        assert cur.native_currency(sym) == "USD"
        cur._symbol_ccy_cache.clear()


def test_conversion_is_a_no_op_when_the_currency_is_unknown():
    """An unconverted honest number beats a converted wrong one."""
    assert cur.native_currency("^GSPC") is None
    assert cur.to_base(100.0, None) is None


# ── the defect, pinned ──────────────────────────────────────────────────────

def test_the_two_universes_disagree_about_four_tickers():
    """The root cause, asserted directly so it cannot be quietly 'fixed' by
    editing one side without the other.

    This is a statement of FACT about the current repository, not a desired
    behaviour. When the collision is resolved, this test should be updated
    deliberately — that edit is the record that the decision was taken.
    """
    cfg = Path("configs/universe.yaml").read_text(encoding="utf-8")
    for bare, (signal_name, provider, master_name) in COLLIDING.items():
        assert f"ticker: {bare}," in cfg, (
            f"{bare} is no longer in the signal pipeline's universe")
        assert signal_name in cfg, (
            f"the signal pipeline no longer calls {bare} '{signal_name}'")
        assert provider.endswith(".L")
        assert signal_name != master_name


def test_a_signals_price_survives_conversion_at_its_true_scale():
    """§8 — the 100x failure mode, now closed, demonstrated on real state.

    THIS TEST USED TO BE A STRICT XFAIL. Its flip to PASS is the proof that
    defect:bare-ticker-identity-collision-2026-08 is fixed rather than hidden.

    What changed is not the pence model — 100 GBp is still £1.00, and the tests
    above still pin that. What changed is WHICH SYMBOL the currency is resolved
    from. `signals.json['BA']` is Boeing at $214, fetched by handing the string
    'BA' to the provider. Resolving its currency through the symbol master's
    DISPLAY row returned GBp, because that row belongs to BA.L — BAE Systems.
    Two correct facts joined on a ticker that means different things on each
    side, and $214 became $2.92.

    A signal key IS a provider symbol, so it is now resolved as one.
    """
    signals = json.loads(Path("data/signals.json").read_text(encoding="utf-8"))
    price = signals["BA"]["current_price"]
    assert price > 200, "fixture assumption: signals.json holds Boeing's US price"

    ccy = cur.provider_currency("BA")
    assert ccy == "USD", (
        f"the signal key 'BA' resolved to {ccy}. It is a provider symbol and "
        f"the provider returns Boeing for it; only the DISPLAY row is London.")

    converted = cur.to_base(price, ccy, "USD")
    assert converted == pytest.approx(price, rel=0.02), (
        f"{price} {ccy} became {converted} USD — the price and the currency "
        f"describe different instruments")


def test_the_batch_endpoint_defaults_to_the_signal_namespace():
    """The 100x error reached the UI through `currencies_for()`, which had no
    namespace at all. Its default must be the one its callers actually use."""
    got = cur.currencies_for(["BA", "AAL", "JD", "AAPL"])
    assert got == {"BA": "USD", "AAL": "USD", "JD": "USD", "AAPL": "USD"}

    # The display-symbol reading still exists and still answers London — it is
    # correct for the symbol master. It just has to be asked for.
    master = cur.currencies_for(["BA"], namespace=cur.MASTER_NAMESPACE)
    assert master == {"BA": "GBp"}


def test_a_london_signal_price_is_still_read_as_pence():
    """The other direction of the same defect: fixing the US case must not
    start reading London prices as pounds."""
    assert cur.provider_currency("BA.L") == "GBp"
    assert cur.to_base(2200.0, cur.provider_currency("BA.L"), "GBP") == pytest.approx(22.0)


def test_the_signal_file_carries_its_own_identity():
    """Identity travels WITH the price now, so no reader has to re-derive it.

    This is the structural fix. Re-derivation from a bare ticker is the defect;
    a row that states its own provider symbol and unit cannot be misread by a
    consumer that never saw this discussion.
    """
    signals = json.loads(Path("data/signals.json").read_text(encoding="utf-8"))
    for bare, (_, provider, _) in COLLIDING.items():
        row = signals.get(bare)
        if not row:
            continue
        assert row["provider_symbol"] == bare, (
            f"{bare} should carry itself as its provider symbol, not {provider}")
        assert row["venue"] == "US"
        assert row["price_unit"] == "USD"

    # And the unit is present on every row, or explicitly null — never absent.
    missing = [k for k, v in signals.items()
               if isinstance(v, dict) and "price_unit" not in v]
    assert not missing, f"{len(missing)} signals carry a price with no unit"


def test_no_page_resolves_a_price_currency_from_a_display_symbol():
    """The defect reached a human eye through the frontend currency helper.

    This test used to assert that the two pages consuming it were ORPHANED —
    that the symptom was hidden. That was never a repair, and the test said so.
    Now that identity is resolved from the provider symbol, the pages are safe
    to route again, so what is pinned is the actual safety property instead:
    the batch endpoint must be reached in a namespace, and no caller may ask
    for the display-symbol reading of a price it is about to convert.
    """
    pages = Path("frontend/src/pages")
    hook = (pages.parent / "hooks" / "useApi.js")
    sources = list(pages.glob("*.jsx")) + list((pages.parent / "components").glob("*.jsx"))
    if hook.exists():
        sources.append(hook)

    offenders = []
    for f in sources:
        text = f.read_text(encoding="utf-8")
        if "/api/universe/currencies" not in text:
            continue
        if "namespace=master" in text:
            offenders.append(f.name)
    assert not offenders, (
        f"{offenders} asks for the display-symbol currency of a price. A "
        f"display ticker is a label, not an instrument — that join is "
        f"defect:bare-ticker-identity-collision-2026-08.")


# ── the ledger is NOT affected, and that must stay true ─────────────────────

def test_ledger_prices_for_colliding_tickers_are_london_scale():
    """§9 — the prediction record is internally consistent.

    Every prediction on a colliding ticker came from a subsystem that resolves
    through the symbol master (technical tracker, v5, desk), so its reference
    price is the London one in pence. Re-checking six of them against the
    provider gave a drift of 0.000. They are VALID and need no regrading; this
    pins that so a future 'fix' cannot silently rescale them.
    """
    from src.core.ledger import connect
    with connect() as conn:
        rows = conn.execute(
            "SELECT subject, price_at FROM predictions "
            "WHERE subject IN ('AAL','BA','JD','SHEL') AND price_at IS NOT NULL"
        ).fetchall()
    if not rows:
        pytest.skip("no predictions on colliding tickers in this ledger")

    # London pence prices are ~100x the US dollar price of the namesake.
    floors = {"AAL": 1000, "BA": 1000, "JD": 50, "SHEL": 1000}
    for r in rows:
        assert r["price_at"] >= floors[r["subject"]], (
            f"{r['subject']} reference price {r['price_at']} looks like the US "
            f"listing, not the London one — the ledger's identity has drifted")
