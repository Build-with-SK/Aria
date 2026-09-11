"""
src/risk/profiles.py
====================
RISK TOLERANCE — three named profiles that move real numbers.

A risk profile here is not a sentence in a prompt. "Trade more aggressively"
is not a control: an LLM can ignore it, misread it, or be talked out of it,
and nothing downstream can test what it did. A profile is a set of
deterministic parameters that the risk officer and the position sizer already
enforce in code, so choosing AGGRESSIVE changes what those gates compute —
not what the desk is asked to feel.

The parameters a profile sets:

    risk_per_trade_pct    how much equity a single stop-out may cost
    max_name_pct          largest position in one name
    max_sector_pct        largest concentration in one sector
    heat_cap_pct          total simultaneous open risk
    drawdown_halt_pct     intraday loss that halts new entries
    max_trades_per_day    trade-count cap
    min_conviction        the judge's bar a candidate must clear
    max_gross_exposure_pct  total book size (never above 100 — no margin)
    allowed_asset_classes what the profile may trade at all

THE CEILING IS NOT NEGOTIABLE
-----------------------------
Every profile is passed through `HARD_LIMITS` before it is returned. A profile
may be more conservative than the platform ceiling and never less. This is why
the aggressive profile is defined with real numbers rather than by relaxing a
cap: if someone later edits AGGRESSIVE to allow a 40% position, `clamp()`
still returns 10%, the tests still pass, and the desk still refuses. A user
choosing their own risk tolerance is choosing where to sit underneath the
platform's limits, never choosing to raise them.
"""
from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)

CONSERVATIVE = "CONSERVATIVE"
MODERATE = "MODERATE"
AGGRESSIVE = "AGGRESSIVE"

VALID_PROFILES = (CONSERVATIVE, MODERATE, AGGRESSIVE)

#: The platform ceiling. No profile, user setting, config file or model output
#: may exceed these. Raising a number here is a deliberate change to what ARIA
#: is allowed to do with money, and is expected to be reviewed as one.
HARD_LIMITS = {
    "risk_per_trade_pct":     2.0,
    "max_name_pct":          10.0,
    "max_sector_pct":        35.0,
    "heat_cap_pct":          15.0,
    "drawdown_halt_pct":      5.0,   # a halt *further* out than this is not a halt
    "max_trades_per_day":    20,
    "max_gross_exposure_pct": 100.0,  # no margin, ever
}

#: Floors — the other direction matters too. `min_conviction` below this would
#: let a profile trade on noise, so the bar can be raised by a profile but not
#: lowered past the platform's own minimum.
HARD_FLOORS = {
    "min_conviction": 55,
}

#: Asset classes the execution path can genuinely handle end-to-end today.
#: Options, futures and commodities are research-and-data only — see
#: `src/execution/order_manager.py` and the market support table in the README.
EXECUTABLE_ASSET_CLASSES = ("equity", "crypto")


@dataclass(frozen=True)
class RiskProfile:
    name: str
    description: str
    risk_per_trade_pct: float
    max_name_pct: float
    max_sector_pct: float
    heat_cap_pct: float
    drawdown_halt_pct: float
    max_trades_per_day: int
    min_conviction: int
    max_gross_exposure_pct: float
    max_correlated_positions: int
    allowed_asset_classes: tuple = field(default=EXECUTABLE_ASSET_CLASSES)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_desk_config(self) -> dict:
        """The subset that `src/desk/config.py` understands, ready to merge.

        Only keys that already exist in the desk DEFAULTS are emitted — the
        config sanitiser drops unknown keys, and a silently dropped risk
        setting would be a profile that appears to apply and does not.
        """
        return {
            "max_name_pct":           self.max_name_pct,
            "max_sector_pct":         self.max_sector_pct,
            "heat_cap_pct":           self.heat_cap_pct,
            "drawdown_halt_pct":      self.drawdown_halt_pct,
            "max_trades_per_day":     self.max_trades_per_day,
            "min_conviction":         self.min_conviction,
            "max_gross_exposure_pct": self.max_gross_exposure_pct,
            "max_correlated_positions": self.max_correlated_positions,
        }


_RAW_PROFILES = {
    CONSERVATIVE: dict(
        name=CONSERVATIVE,
        description=(
            "Capital preservation first. Small positions, a high conviction "
            "bar, an early halt, and few trades. Expect long stretches of "
            "doing nothing — that is the profile working, not failing."),
        risk_per_trade_pct=0.25,
        max_name_pct=2.5,
        max_sector_pct=15.0,
        heat_cap_pct=5.0,
        drawdown_halt_pct=1.0,
        max_trades_per_day=3,
        min_conviction=75,
        max_gross_exposure_pct=50.0,
        max_correlated_positions=2,
    ),
    MODERATE: dict(
        name=MODERATE,
        description=(
            "The default. Matches the caps the desk has been running and "
            "testing against, and the numbers the risk officer documents."),
        risk_per_trade_pct=0.5,
        max_name_pct=5.0,
        max_sector_pct=25.0,
        heat_cap_pct=10.0,
        drawdown_halt_pct=2.0,
        max_trades_per_day=10,
        min_conviction=65,
        max_gross_exposure_pct=100.0,
        max_correlated_positions=3,
    ),
    AGGRESSIVE: dict(
        name=AGGRESSIVE,
        description=(
            "Larger positions and a lower bar, still inside every platform "
            "limit. This is the most risk ARIA will take; it is not "
            "unlimited, and it does not switch any safety gate off."),
        risk_per_trade_pct=1.0,
        max_name_pct=8.0,
        max_sector_pct=35.0,
        heat_cap_pct=15.0,
        drawdown_halt_pct=4.0,
        max_trades_per_day=20,
        min_conviction=58,
        max_gross_exposure_pct=100.0,
        max_correlated_positions=4,
    ),
}


def clamp(params: dict) -> dict:
    """Force a parameter set inside the platform ceiling and floor.

    Returns a new dict; logs every value it had to pull back, because a
    silently clamped risk setting looks exactly like one that was honoured.
    """
    out = dict(params)
    for key, ceiling in HARD_LIMITS.items():
        if key in out and out[key] is not None and out[key] > ceiling:
            logger.warning("risk profile %s: %s=%s exceeds platform limit %s — clamped",
                           out.get("name", "?"), key, out[key], ceiling)
            out[key] = ceiling
    for key, floor in HARD_FLOORS.items():
        if key in out and out[key] is not None and out[key] < floor:
            logger.warning("risk profile %s: %s=%s below platform floor %s — raised",
                           out.get("name", "?"), key, out[key], floor)
            out[key] = floor
    # Asset classes are an allow-list intersection, not a preference.
    if "allowed_asset_classes" in out:
        requested = tuple(out["allowed_asset_classes"] or ())
        permitted = tuple(a for a in requested if a in EXECUTABLE_ASSET_CLASSES)
        dropped = [a for a in requested if a not in EXECUTABLE_ASSET_CLASSES]
        if dropped:
            logger.warning("risk profile %s: asset classes %s are not executable — dropped",
                           out.get("name", "?"), dropped)
        out["allowed_asset_classes"] = permitted
    return out


def normalise(name: str | None) -> str:
    """Map user input to a valid profile name, defaulting to MODERATE.

    An unrecognised profile does NOT fall through to the most permissive one.
    """
    candidate = (name or "").strip().upper()
    if candidate in VALID_PROFILES:
        return candidate
    if candidate:
        logger.warning("unknown risk profile %r — falling back to %s", name, MODERATE)
    return MODERATE


def get_profile(name: str | None = None) -> RiskProfile:
    """The profile, already clamped. This is the only public constructor."""
    key = normalise(name)
    return RiskProfile(**clamp(dict(_RAW_PROFILES[key])))


def all_profiles() -> list[RiskProfile]:
    return [get_profile(n) for n in VALID_PROFILES]


def describe_all() -> dict:
    """For the API/UI: every profile plus the ceiling it sits under, so the
    limits are visible to whoever is choosing."""
    return {
        "profiles": [p.to_dict() for p in all_profiles()],
        "hard_limits": dict(HARD_LIMITS),
        "hard_floors": dict(HARD_FLOORS),
        "executable_asset_classes": list(EXECUTABLE_ASSET_CLASSES),
        "default": MODERATE,
    }


def allows_asset_class(profile: RiskProfile, asset_class: str) -> bool:
    """Fail closed: an unknown or missing asset class is not allowed."""
    if not asset_class:
        return False
    return str(asset_class).strip().lower() in profile.allowed_asset_classes


def sized_risk_amount(equity: float, profile: RiskProfile,
                      size_multiplier: float = 1.0) -> float:
    """Cash a single trade may risk under this profile.

    Non-finite or negative equity returns 0.0 rather than raising — the
    callers treat 0 as "no trade", which is the safe reading of "I cannot
    tell you how much money there is".
    """
    try:
        eq = float(equity)
        mult = float(size_multiplier)
    except (TypeError, ValueError):
        return 0.0
    # isfinite before the comparison: `inf > 0` is True, and an infinite risk
    # budget is exactly the kind of "unknown read as known" this refuses.
    if not math.isfinite(eq) or not math.isfinite(mult):
        return 0.0
    if not (eq > 0) or not (mult > 0):
        return 0.0
    return eq * (profile.risk_per_trade_pct / 100.0) * mult


def profile_names() -> Iterable[str]:
    return VALID_PROFILES
