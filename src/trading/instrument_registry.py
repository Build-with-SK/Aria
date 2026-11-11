"""Conventional contract sizes, used only to catch gross mapping errors.

These are industry conventions, not any broker's published figures: one FX lot
is 100,000 units of the base currency, one gold lot is 100 troy ounces, one
silver lot is 5,000 ounces (some brokers use 1,000). A specification outside
the set for its instrument is returned UNVERIFIED (review CR-03) until a human
confirms the broker's own figure and adds it here.

Instruments with no conventional size (indices, energy, crypto, equity CFDs
vary by broker) have no entry; for them the broker's own profit calculation is
the only contract-size check, and it is mandatory for every instrument.
"""
from decimal import Decimal

from .contracts import AssetClass, Instrument

_FIAT_FX_LOT = frozenset({Decimal("100000")})

# (asset class, base currency) -> allowed contract sizes. None base = any base.
_CONVENTIONAL: dict[tuple[AssetClass, str | None], frozenset[Decimal]] = {
    (AssetClass.FOREX, None): _FIAT_FX_LOT,
    (AssetClass.METALS, "XAU"): frozenset({Decimal("100")}),
    (AssetClass.METALS, "XAG"): frozenset({Decimal("5000"), Decimal("1000")}),
    (AssetClass.METALS, "XPT"): frozenset({Decimal("100")}),
    (AssetClass.METALS, "XPD"): frozenset({Decimal("100")}),
}


def expected_contract_sizes(instrument: Instrument) -> frozenset[Decimal] | None:
    """Allowed sizes for this instrument, or None when no convention applies."""
    specific = _CONVENTIONAL.get((instrument.asset_class, instrument.base_currency))
    if specific is not None:
        return specific
    return _CONVENTIONAL.get((instrument.asset_class, None))
