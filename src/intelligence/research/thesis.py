"""
src/intelligence/research/thesis.py
===================================
A structured research thesis — why a view exists, what supports it, what
contradicts it, and what would end it. Not a signal, and not a number.

DIRECTION (from INDEPENDENT groups, never from raw item counts)
    BULLISH / BEARISH  at least one group points that way and none points the
                       other way; `basis` says whether that rests on
                       MULTIPLE_INDEPENDENT_GROUPS or a SINGLE_EVIDENCE_GROUP
    NEUTRAL            no group is directional (NO_DIRECTIONAL_EVIDENCE), or
                       groups point opposite ways (CONFLICTED)

There is no numeric confidence. The scorecard below states, dimension by
dimension, what kind of support the view has:

    DATA_QUALITY            VALID | DEGRADED | MISSING
    EVIDENCE_DIVERSITY      VALID (>= 3 groups) | LIMITED (2) | NARROW (1) | MISSING
    EVIDENCE_INDEPENDENCE   VALID | CONCENTRATED (one group holds > 60% of items)
                            | INSUFFICIENT_DATA
    HISTORICAL_SUPPORT      DIFFERS_FROM_BASE_RATE | NOT_DEMONSTRATED |
                            INSUFFICIENT_DATA
    REGIME_MATCH            VALID (the consulted model has a measured record in
                            this regime) | INSUFFICIENT_DATA | UNKNOWN
    EVENT_RISK              HIGH | ELEVATED | LOW | UNKNOWN
    FRESHNESS               FRESH | STALE | MISSING (worst component used)
    CONTRADICTION           ALIGNED | CONFLICTED | SINGLE_GROUP | NONE
    PIT_INTEGRITY           VALID | LIVE_ONLY (a component is only valid for a
                            live query) | VIOLATED

and one sentence per group: STRONG / MODERATE / WEAK evidence, where STRONG
means at least two items agreeing with no dissent inside the group, MODERATE
one agreeing item or a majority, WEAK none or a split — a description of the
evidence, not a probability.

A thesis is `executable: False` and `may_size: False`, always.
"""
from __future__ import annotations

from typing import Optional

from src.intelligence.research.sources import GROUPS

DIRECTIONS = ("BULLISH", "BEARISH", "NEUTRAL")


def _strength(g: dict, direction: int) -> str:
    if not g or g["direction"] == 0 and g["state"] == "NONE":
        return "ABSENT" if not g else "NON_DIRECTIONAL"
    agree = g["bullish_items"] if direction > 0 else g["bearish_items"] if direction < 0 else 0
    dissent = g["bearish_items"] if direction > 0 else g["bullish_items"] if direction < 0 else 0
    if direction == 0:
        return "MIXED" if g["state"] == "MIXED" else "NON_DIRECTIONAL"
    if agree >= 2 and dissent == 0:
        return "STRONG"
    if agree >= 1 and agree > dissent:
        return "MODERATE"
    return "WEAK"


def direction_of(agreement: dict) -> tuple[str, str, int]:
    bull = agreement["independent"]["bullish_groups"]
    bear = agreement["independent"]["bearish_groups"]
    if bull and bear:
        return "NEUTRAL", "CONFLICTED", 0
    if bull:
        return "BULLISH", ("MULTIPLE_INDEPENDENT_GROUPS" if len(bull) > 1
                           else "SINGLE_EVIDENCE_GROUP"), 1
    if bear:
        return "BEARISH", ("MULTIPLE_INDEPENDENT_GROUPS" if len(bear) > 1
                           else "SINGLE_EVIDENCE_GROUP"), -1
    return "NEUTRAL", "NO_DIRECTIONAL_EVIDENCE", 0


def invalidators(snap, d: int, ev_ctx: Optional[dict], items: list) -> list[dict]:
    """Concrete conditions that would end the view, from levels and events
    known at T."""
    out = []
    close = snap.frames.get("close")
    if close is not None and len(close) >= 200 and d != 0:
        sma200 = float(close.iloc[-200:].mean())
        lo20, hi20 = float(close.iloc[-20:].min()), float(close.iloc[-20:].max())
        if d > 0:
            out.append({"kind": "PRICE_LEVEL", "condition": f"close below {lo20:.4g}",
                        "why": "a break of the 20-day low"})
            out.append({"kind": "TREND", "condition": f"close below the 200-day average "
                                                      f"({sma200:.4g})",
                        "why": "the trend group would stop supporting the view"})
        else:
            out.append({"kind": "PRICE_LEVEL", "condition": f"close above {hi20:.4g}",
                        "why": "a break of the 20-day high"})
            out.append({"kind": "TREND", "condition": f"close above the 200-day average "
                                                      f"({sma200:.4g})",
                        "why": "the trend group would stop supporting the view"})
    for e in (ev_ctx or {}).get("upcoming", []):
        if e["importance"] in ("HIGH", "MEDIUM"):
            out.append({"kind": "EVENT", "condition": f"{e['kind']} on {e['event_time'][:10]}",
                        "why": f"{e['importance']} importance: {e['importance_basis']}"})
    bench = snap.frames.get("benchmark")
    if bench is not None and len(bench) >= 200:
        b200 = float(bench.iloc[-200:].mean())
        b = float(bench.iloc[-1])
        out.append({"kind": "REGIME", "condition": (f"benchmark closes "
                                                    f"{'below' if b > b200 else 'above'} its "
                                                    f"200-day average ({b200:.4g})"),
                    "why": "the market regime would flip"})
    vol = snap.get("volatility").value or {}
    if vol.get("regime") in ("LOW_VOL", "NORMAL_VOL"):
        out.append({"kind": "VOLATILITY", "condition": "20-day realised vol rises above its "
                                                       "80th percentile of the past year",
                    "why": "a volatility regime change"})
    contra = [i for i in items if i.direction == -d and d != 0]
    for g in sorted({i.group for i in contra}):
        out.append({"kind": "CONTRADICTION", "condition": f"{g} evidence strengthens",
                    "why": f"it already points against the view "
                           f"({sum(1 for i in contra if i.group == g)} item(s))"})
    liq = snap.get("liquidity").value or {}
    if liq.get("adv_shares_20d"):
        out.append({"kind": "LIQUIDITY", "condition": f"20-day average volume falls below "
                                                      f"{0.5 * liq['adv_shares_20d']:.0f} shares",
                    "why": "liquidity halving from today's level"})
    return out


def scorecard(snap, agreement: dict, items: list, analogues: Optional[dict],
              ev_ctx: Optional[dict], models: list, live: bool) -> dict:
    from src.intelligence.research import components as C
    comps = snap.components
    price = comps.get("price")
    dq = ("MISSING" if price is None or price.freshness == C.MISSING else
          "DEGRADED" if price.freshness in (C.STALE, C.UNKNOWN) or
          any(v.freshness in (C.STALE, C.CONFLICTED) for v in comps.values()) else "VALID")
    ng = agreement["independent"]["groups_with_evidence"]
    div = "VALID" if ng >= 3 else "LIMITED" if ng == 2 else "NARROW" if ng == 1 else "MISSING"
    conc = agreement["concentration"]
    if not items:
        ind = "INSUFFICIENT_DATA"
    elif conc["share_of_items"] > 0.6:
        ind = "CONCENTRATED"
    else:
        ind = "VALID"
    hs = (analogues or {}).get("predictive_value") or (analogues or {}).get("status") \
        or "INSUFFICIENT_DATA"
    if str(hs).startswith("DIFFERS"):
        hs = "DIFFERS_FROM_BASE_RATE"
    regime_states = [((m.get("regime_fit") or {}).get("status")) for m in models]
    rm = ("VALID" if "KNOWN" in regime_states else
          "INSUFFICIENT_DATA" if regime_states else "UNKNOWN")
    er = ((ev_ctx or {}).get("event_risk") or {}).get("state", "UNKNOWN")
    # Over what was available: absent components are DATA_QUALITY's business
    # and are listed in the snapshot's missingness.
    used = [v.freshness for k, v in comps.items() if k != "sentiment"
            and v.freshness not in (C.NO_DATA, C.MISSING)]
    fr = C.worst(used) if price is not None and price.freshness != C.MISSING else C.MISSING
    fr = {C.FRESH: "FRESH", C.MISSING: "MISSING", C.CONFLICTED: "CONFLICTED"}.get(fr, "STALE")
    bull = agreement["independent"]["bullish_groups"]
    bear = agreement["independent"]["bearish_groups"]
    contra = ("CONFLICTED" if bull and bear else
              "ALIGNED" if len(bull) + len(bear) > 1 else
              "SINGLE_GROUP" if bull or bear else "NONE")
    pits = {v.pit for v in comps.values() if v.value is not None}
    if not live and C.LIVE_ONLY in pits:
        pit = "VIOLATED"
    else:
        pit = "LIVE_ONLY" if C.LIVE_ONLY in pits else "VALID"
    return {
        "DATA_QUALITY": {"state": dq, "basis": f"price {price.freshness if price else 'MISSING'}"},
        "EVIDENCE_DIVERSITY": {"state": div, "basis": f"{ng} group(s) with evidence"},
        "EVIDENCE_INDEPENDENCE": {"state": ind,
                                  "basis": f"largest group {conc['largest_group']} holds "
                                           f"{conc['share_of_items']:.0%} of items"},
        "HISTORICAL_SUPPORT": {"state": hs, "basis": (analogues or {}).get("why")
                               or f"{(analogues or {}).get('analogue_count')} analogues"},
        "REGIME_MATCH": {"state": rm, "basis": "the consulted models' records in the "
                                               "current regime"},
        "EVENT_RISK": {"state": er, "basis": ((ev_ctx or {}).get("event_risk") or {}).get("basis")},
        "FRESHNESS": {"state": fr, "basis": "worst freshness of the components available"},
        "CONTRADICTION": {"state": contra, "basis": f"bullish groups {bull}, bearish {bear}"},
        "PIT_INTEGRITY": {"state": pit,
                          "basis": "every component cut at T" if pit == "VALID" else
                                   "a live-only snapshot (fundamentals) is in use"},
    }


def build(snap, agreement: dict, items: list, *, analogues: Optional[dict],
          models: list, routing: dict, live: bool) -> dict:
    label, basis, d = direction_of(agreement)
    ev_ctx = snap.get("event_risk").value
    supporting = [i for i in items if d != 0 and i.direction == d]
    contradicting = [i for i in items if (i.direction == -d if d != 0 else i.direction != 0)]
    groups = agreement["groups"]
    strengths = {g: _strength(groups.get(g), d if d else (1 if groups.get(g, {}).get(
        "direction", 0) > 0 else -1)) for g in GROUPS if g in groups}
    card = scorecard(snap, agreement, items, analogues, ev_ctx, models, live)
    fac = (snap.get("factors").value or {}).get("windows", {}).get("21d") or {}
    sentence = _summary(label, basis, strengths, card)
    return {
        "instrument": snap.symbol, "timestamp": snap.information_time,
        "direction": label, "basis": basis,
        "supporting_evidence": [i.to_dict() for i in supporting],
        "contradicting_evidence": [i.to_dict() for i in contradicting],
        "independent_evidence_groups": {g: {**groups[g], "strength": strengths.get(g)}
                                        for g in groups},
        "agreement_statement": agreement["statement"],
        "unexplained_residual": fac.get("residual") if fac.get("status") == "MEASURED" else
                                {"status": "INSUFFICIENT_DATA"},
        "attribution_sentence": fac.get("sentence"),
        "regime": snap.get("regime").value,
        "event_risk": (ev_ctx or {}).get("event_risk"),
        "data_quality": card["DATA_QUALITY"], "freshness": snap.freshness,
        "invalidators": invalidators(snap, d, ev_ctx, items),
        "historical_context": analogues,
        "models": models, "routing": routing,
        "quality_scorecard": card,
        "summary": sentence,
        "confidence": None,
        "confidence_note": "deliberately absent: see quality_scorecard",
        "executable": False, "may_size": False,
    }


def _summary(label: str, basis: str, strengths: dict, card: dict) -> str:
    words = {"STRONG": "strong", "MODERATE": "moderate", "WEAK": "weak", "MIXED": "mixed",
             "NON_DIRECTIONAL": "non-directional", "ABSENT": "no"}
    parts = [f"{words.get(v, v.lower())} {g.lower().replace('_', ' ')} evidence"
             for g, v in strengths.items()]
    ev = card["EVENT_RISK"]["state"].lower()
    hist = {"DIFFERS_FROM_BASE_RATE": "historical analogues that differ from the base "
                                      "rate (in-sample)",
            "NOT_DEMONSTRATED": "historical analogues with no demonstrated edge",
            }.get(card["HISTORICAL_SUPPORT"]["state"], "insufficient historical analogues")
    lead = (f"{label.capitalize()} view ({basis.replace('_', ' ').lower()})"
            if label != "NEUTRAL" else f"No directional view ({basis.replace('_', ' ').lower()})")
    return (f"{lead}: " + (", ".join(parts) if parts else "no evidence") +
            f"; {ev} event risk; {hist}.")
