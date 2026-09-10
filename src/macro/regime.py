"""
src/macro/regime.py
===================
The market regime, with the evidence that produced it.

WHY THIS MODULE EXISTS
----------------------
`macro_data._classify_regime()` is a real classifier — a growth/inflation
quadrant over CPI, the 10Y-2Y spread, VIX and unemployment — and it is NOT
hardcoded. Feed it different numbers and it returns a different regime; the
tests here prove that by doing exactly that.

What it could not do is say how much of its answer it actually knew:

    cpi    = snap.cpi_yoy            or 3.0
    spread = snap.yield_spread_10y2y or 0.0
    vix    = snap.vix                or 20.0
    unemp  = snap.unemployment_rate  or 4.5

Every one of those defaults lands on the benign side of its own threshold
(3.0 < 3.5, 0.0 is not < 0, 20 < 25, 4.5 < 5.5). So a snapshot with NOTHING in
it classifies as "Expansion (Goldilocks)" with total confidence. The regime was
never fabricated; the CERTAINTY was, and a reader cannot tell the two apart
from the string alone.

So this module returns the regime together with:

    inputs      every value, whether it was OBSERVED or ASSUMED, and its source
    coverage    the fraction of the classification's weight that was observed
    confidence  derived from coverage — never from how strong the reading looks
    evidence    which test fired, in words
    caveats     what would change the answer

A regime carried by two observed inputs out of four is still the best available
reading. It just must not be presented as the same thing as one carried by all
four.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: The quadrant model, restated here as data so the thresholds are inspectable
#: rather than buried in an if-chain. Weights say how much each input
#: contributes to KNOWING the regime — not how bullish it is.
INPUTS: dict[str, dict] = {
    "cpi_yoy": {
        "label": "CPI year-on-year",
        "weight": 0.30,
        "default": 3.0,
        "axis": "inflation",
        "unit": "%",
    },
    "yield_spread_10y2y": {
        "label": "10Y-2Y spread",
        "weight": 0.30,
        "default": 0.0,
        "axis": "growth",
        "unit": "pp",
    },
    "vix": {
        "label": "VIX",
        "weight": 0.25,
        "default": 20.0,
        "axis": "growth",
        "unit": "",
    },
    "unemployment_rate": {
        "label": "Unemployment",
        "weight": 0.15,
        "default": 4.5,
        "axis": "growth",
        "unit": "%",
    },
}

HIGH_INFLATION_ABOVE = 3.5
INVERTED_BELOW = 0.0
VIX_STRESS_ABOVE = 25.0
UNEMPLOYMENT_STRESS_ABOVE = 5.5

EXPANSION = "Expansion (Goldilocks)"
STAGFLATION = "Stagflation Risk"
SLOWDOWN = "Slowdown / Recovery"
RECESSION = "Recession Risk"

REGIMES = (EXPANSION, STAGFLATION, SLOWDOWN, RECESSION)

#: What each regime MEANS, in the one sentence a reader needs. Kept next to the
#: classifier so the label and its meaning cannot drift apart.
MEANING = {
    EXPANSION: ("inflation contained and no growth stress — the quadrant that "
                "historically rewards risk-taking"),
    STAGFLATION: ("inflation running hot while growth still holds — the "
                  "quadrant where both stocks and bonds can fall together"),
    SLOWDOWN: ("inflation contained but growth is stressed — the quadrant "
               "where duration usually works and cyclicals do not"),
    RECESSION: ("inflation hot AND growth stressed — the quadrant with the "
                "fewest places to hide"),
}


def classify(snapshot: Any) -> dict:
    """Classify the regime and report how much of it was actually observed.

    `snapshot` is anything with the four attributes, or a dict of them — a
    `MacroSnapshot`, a parsed `macro_data.json`, or a hand-built dict in a test.
    """
    get = (snapshot.get if isinstance(snapshot, dict)
           else lambda k, d=None: getattr(snapshot, k, d))

    inputs: dict[str, dict] = {}
    observed_weight = 0.0
    for key, spec in INPUTS.items():
        raw = get(key)
        seen = raw is not None
        value = float(raw) if seen else spec["default"]
        if seen:
            observed_weight += spec["weight"]
        inputs[key] = {
            "label": spec["label"],
            "value": round(value, 4),
            "unit": spec["unit"],
            "status": "OBSERVED" if seen else "ASSUMED",
            "assumed_default": None if seen else spec["default"],
            "weight": spec["weight"],
            "axis": spec["axis"],
        }

    cpi = inputs["cpi_yoy"]["value"]
    spread = inputs["yield_spread_10y2y"]["value"]
    vix = inputs["vix"]["value"]
    unemp = inputs["unemployment_rate"]["value"]

    high_inflation = cpi > HIGH_INFLATION_ABOVE
    stress_tests = {
        "inverted_curve": spread < INVERTED_BELOW,
        "elevated_vix": vix > VIX_STRESS_ABOVE,
        "rising_unemployment": unemp > UNEMPLOYMENT_STRESS_ABOVE,
    }
    low_growth = any(stress_tests.values())

    if not high_inflation and not low_growth:
        regime = EXPANSION
    elif high_inflation and not low_growth:
        regime = STAGFLATION
    elif not high_inflation and low_growth:
        regime = SLOWDOWN
    else:
        regime = RECESSION

    evidence = [
        (f"CPI {cpi:.2f}% is "
         + ("ABOVE" if high_inflation else "below")
         + f" the {HIGH_INFLATION_ABOVE}% high-inflation threshold"),
        (f"10Y-2Y spread {spread:+.2f}pp is "
         + ("INVERTED" if stress_tests["inverted_curve"] else "positive")),
        (f"VIX {vix:.1f} is "
         + ("ABOVE" if stress_tests["elevated_vix"] else "below")
         + f" the stress level of {VIX_STRESS_ABOVE}"),
        (f"Unemployment {unemp:.1f}% is "
         + ("ABOVE" if stress_tests["rising_unemployment"] else "below")
         + f" the {UNEMPLOYMENT_STRESS_ABOVE}% stress level"),
    ]

    assumed = [i["label"] for i in inputs.values() if i["status"] == "ASSUMED"]
    coverage = round(observed_weight, 4)

    caveats: list[str] = []
    if assumed:
        caveats.append(
            f"{len(assumed)} of {len(INPUTS)} inputs were not observed and fell "
            f"back to a neutral default ({', '.join(assumed)}). Every default "
            f"sits on the benign side of its own threshold, so a thin snapshot "
            f"drifts towards '{EXPANSION}' — read this regime as "
            f"{coverage:.0%} evidenced, not as confirmed.")
    if not high_inflation and not low_growth and coverage < 1.0:
        caveats.append(
            "'Expansion' here means no threshold was crossed, which is not the "
            "same as growth having been measured.")

    # Nearest alternative: what single change would flip it?
    flips = _flips(cpi, spread, vix, unemp, regime)

    return {
        "regime": regime,
        "meaning": MEANING[regime],
        "inflation": "high" if high_inflation else "contained",
        "growth": "stressed" if low_growth else "steady",
        "inputs": inputs,
        "stress_tests": stress_tests,
        "coverage": coverage,
        "observed_inputs": len(INPUTS) - len(assumed),
        "total_inputs": len(INPUTS),
        "assumed_inputs": assumed,
        # Confidence is COVERAGE, not conviction. A classifier cannot be more
        # certain than its inputs, and dressing a thin reading in a high number
        # is the specific dishonesty this module was written to remove.
        "confidence": coverage,
        "confidence_basis": (
            "the fraction of the classifier's weight that came from an observed "
            "value rather than a default"),
        "evidence": evidence,
        "caveats": caveats,
        "would_flip_to": flips,
        "classified_at": datetime.now().isoformat(timespec="seconds"),
    }


def _flips(cpi: float, spread: float, vix: float, unemp: float,
           current: str) -> list[dict]:
    """What single move would change the regime — the invalidation conditions."""
    out = []
    if cpi <= HIGH_INFLATION_ABOVE:
        out.append({"if": f"CPI rises above {HIGH_INFLATION_ABOVE}%",
                    "distance": round(HIGH_INFLATION_ABOVE - cpi, 2),
                    "unit": "pp"})
    else:
        out.append({"if": f"CPI falls below {HIGH_INFLATION_ABOVE}%",
                    "distance": round(cpi - HIGH_INFLATION_ABOVE, 2),
                    "unit": "pp"})
    if spread >= INVERTED_BELOW:
        out.append({"if": "the 10Y-2Y spread inverts",
                    "distance": round(spread - INVERTED_BELOW, 2), "unit": "pp"})
    if vix <= VIX_STRESS_ABOVE:
        out.append({"if": f"VIX rises above {VIX_STRESS_ABOVE}",
                    "distance": round(VIX_STRESS_ABOVE - vix, 2), "unit": "pts"})
    if unemp <= UNEMPLOYMENT_STRESS_ABOVE:
        out.append({"if": f"unemployment rises above {UNEMPLOYMENT_STRESS_ABOVE}%",
                    "distance": round(UNEMPLOYMENT_STRESS_ABOVE - unemp, 2),
                    "unit": "pp"})
    return out


def current(macro: Optional[dict] = None) -> dict:
    """The live regime, read from whatever macro state the system holds.

    Falls back through: the passed dict → data/macro_data.json. The freshness of
    that file is reported, never smoothed over: a regime computed from May's
    numbers is not today's regime, and saying so is the whole point.
    """
    if macro is None:
        from pathlib import Path
        import json
        root = Path(__file__).resolve().parent.parent.parent
        path = root / "data" / "macro_data.json"
        try:
            macro = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("regime: no macro snapshot readable: %s", e)
            macro = {}

    out = classify(macro)
    out["as_of"] = macro.get("data_date")
    out["macro_score"] = macro.get("macro_score")
    out["stored_regime"] = macro.get("regime")

    age_days = None
    if out["as_of"]:
        try:
            age_days = (datetime.now()
                        - datetime.fromisoformat(str(out["as_of"])[:10])).days
        except Exception:
            pass
    out["age_days"] = age_days
    out["stale"] = bool(age_days is not None and age_days > 7)
    if out["stale"]:
        out["caveats"].insert(0, (
            f"the macro snapshot is {age_days} days old ({out['as_of']}). This "
            f"is what the regime WAS on that date; it is not a live reading."))

    # The stored regime and the recomputed one must agree, or the reader is
    # looking at a cached string that no longer follows from the numbers
    # beneath it — the exact failure a "LIVE MARKET STATE" heading over a
    # three-month-old file already produced here once.
    if out["stored_regime"] and out["stored_regime"] != out["regime"]:
        out["caveats"].append(
            f"the stored regime says '{out['stored_regime']}' but these inputs "
            f"classify as '{out['regime']}' — the cached label is stale.")
    return out
