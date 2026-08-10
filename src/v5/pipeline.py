"""
src/v5/pipeline.py
==================
The orchestrator. One call runs the whole chain in the only order it is allowed
to run in:

    research modules → ensemble synthesis → meta-reasoning → RISK GATE
                     → self-audit → prediction log

Meta-reasoning runs before the risk gate so that a thesis which failed its own
falsification review is *sized* on the reduced confidence rather than on the
ensemble's first impression. The risk gate is still the last word on whether
anything is committed: it can veto a proposal that survived every other stage.
A vetoed idea is still audited and still logged, so the record shows what was
refused and why.

This module never places an order and never calls anything that can. The desk
(src/desk) remains the only execution path, and it still requires human
approval — the brain proposes, the human approves.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from src.v5 import audit as audit_mod
from src.v5 import ensemble as ensemble_mod
from src.v5 import learning, marketdata as md, meta as meta_mod, registry
from src.v5 import risk_gate

logger = logging.getLogger(__name__)


def analyze(ticker: str, *, families: list[str] | None = None,
            log: bool = True, include_research_only: bool = False) -> dict:
    """Full V5 analysis of one instrument. Never raises for data reasons — a
    universe of missing data produces a report that says so."""
    t0 = time.time()
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return _failure("", t0, "No ticker supplied.")

    # Guard: an instrument with no price history is not a hold — it is an
    # unknown symbol. Without this the market-level modules (breadth, credit,
    # global macro) still report, and the chain produces a confident-looking
    # "no position, risk veto" for a ticker that does not exist. Checking first
    # is both honest and fast: no module runs, no vendor retry storm.
    if md.closes(ticker, period="1y") is None:
        # "Unknown symbol" and "every vendor is down" look identical from here
        # unless the vendor attempts are read. Telling a user their ticker does
        # not exist when the truth is an outage sends them to fix the wrong
        # thing, so the two are separated by what the vendors actually said.
        prov = md.provenance(ticker)
        attempts = prov.get("vendor_attempts") or []
        all_vendors_failed = bool(attempts) and not any(a.get("ok") for a in attempts)
        if all_vendors_failed:
            return _failure(
                ticker, t0,
                f"Price history for '{ticker}' could not be loaded because every "
                f"market-data vendor failed. This is a feed outage, not a verdict on "
                f"the instrument, and no analysis was attempted.",
                data_outage=True, data=_data_block(ticker, prov))
        return _failure(
            ticker, t0,
            f"No price history could be loaded for '{ticker}'. It is either not a listed "
            f"symbol, not covered by the data vendor, or the feed is unreachable. No "
            f"analysis was attempted — a verdict here would be about the market, not "
            f"about this instrument.",
            unknown_instrument=True, data=_data_block(ticker, prov))

    reports = registry.run_all(ticker, families=families,
                               include_research_only=include_research_only)
    provenance = md.provenance(ticker)
    ens = ensemble_mod.synthesise(reports, ticker)
    meta = meta_mod.review(ticker, ens, reports, data_quality=provenance)
    risk = risk_gate.assess(ticker, ens, reports, meta)
    self_audit = audit_mod.build(ticker, ens, reports, meta, risk)

    recommendation = _recommendation(ens, risk, meta, provenance)

    prediction_id = None
    if log:
        try:
            prediction_id = learning.log_prediction(
                ticker=ticker,
                direction=ens.direction,
                p_bull=ens.p_bull,
                confidence=meta.confidence_after,
                horizon_days=_ensemble_horizon(ens, reports),
                price=risk.entry_price,
                modules=[r.to_dict() for r in reports],
                rationale=recommendation["headline"],
                risk={"verdict": risk.verdict, "size_pct": risk.position_size_pct},
                weights_version=ens.weights_version)
        except Exception as e:
            logger.warning(f"v5 could not log prediction for {ticker}: {e}")

    return {
        "ticker": ticker,
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "elapsed_ms": int((time.time() - t0) * 1000),
        "prediction_id": prediction_id,
        "data": _data_block(ticker, provenance),
        "recommendation": recommendation,
        "ensemble": ens.to_dict(),
        "risk": risk.to_dict(),
        "meta": meta.to_dict(),
        "self_audit": self_audit.to_dict(),
        "modules": [r.to_dict() for r in reports],
        "module_count": {"total": len(reports),
                         "reporting": ens.n_voting,
                         "abstained": len(reports) - ens.n_voting},
        "weights_version": ens.weights_version,
    }


def _data_block(ticker: str, prov: dict) -> dict:
    """What the analysis was built on, in the response rather than in a log.

    Present on every reply, including failures. A caller must be able to tell a
    signal computed from this morning's prices from one computed from last
    week's cache WITHOUT reading the server logs — that was the whole failure
    mode: the two were byte-identical in the API response.
    """
    stale = bool(prov.get("stale", True)) if prov else True
    block = {
        "source": prov.get("served_from") if prov else None,
        "vendor": prov.get("vendor") if prov else None,
        "primary_vendor_used": (prov.get("served_from") == "vendor:yfinance"
                                if prov else False),
        "last_bar": prov.get("last_bar") if prov else None,
        "last_bar_age_days": prov.get("last_bar_age_days") if prov else None,
        "stale": stale,
        "stale_reason": prov.get("stale_reason", "") if prov else "no data was loaded",
        "citation": md.source_label(ticker),
    }
    if prov.get("vendor_attempts"):
        block["vendor_attempts"] = prov["vendor_attempts"]
    if stale:
        block["warning"] = (
            "This analysis is built on data that is not current. Confidence has "
            "been reduced accordingly, and the numbers below describe the market "
            f"as of {prov.get('last_bar') or 'an unknown date'}.")
    return block


def _failure(ticker: str, t0: float, message: str, **extra) -> dict:
    """Every early exit returns the same shape as a successful one, minus the
    analysis. Callers should never have to branch on which kind of failure they
    got just to read the timestamp."""
    return {
        "ticker": ticker,
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "elapsed_ms": int((time.time() - t0) * 1000),
        "error": message,
        **extra,
    }


def _ensemble_horizon(ens, reports) -> int:
    """The horizon the recommendation is actually made over.

    Taking the maximum would let one 252-day quality module push every
    prediction's resolution a year out, which would starve the learning engine
    of feedback. The weighted mean of the reporting modules' horizons is the
    honest answer — it lands near the house 21-day frame while acknowledging
    the slower engines that contributed.
    """
    voting = [r for r in reports if r.votes]
    if not voting:
        return 21
    wsum = sum(ens.weights.get(r.module, 0.0) for r in voting)
    if wsum <= 0:
        return 21
    mean = sum(ens.weights.get(r.module, 0.0) * r.horizon_days for r in voting) / wsum
    return max(5, min(126, int(round(mean))))


def _recommendation(ens, risk, meta, provenance: dict | None = None) -> dict:
    """The single sentence Soundariyan Karunakaran reads first, and the numbers behind it."""
    conf = meta.confidence_after
    if risk.verdict == "VETO":
        action = "NO POSITION — risk veto"
    elif risk.verdict == "NO_TRADE":
        action = "NO POSITION"
    elif ens.direction == "bull":
        action = "LONG" + (" (reduced size)" if risk.verdict == "REDUCE" else "")
    elif ens.direction == "bear":
        action = "SHORT / AVOID" + (" (reduced size)" if risk.verdict == "REDUCE" else "")
    else:
        action = "NO POSITION"

    # The band widens with the modules' own mean interval width, applied to the
    # edge so the range can never cross 50% and flip the direction.
    half = ens.mean_ci_width / 2
    lo = 0.5 + meta.edge_after * (1 - half) / 2
    hi = min(1.0, 0.5 + meta.edge_after * (1 + half) / 2)
    band = f"{lo:.0%}-{hi:.0%}"
    if action.startswith("NO POSITION"):
        headline = (f"{ens.ticker}: no position. {ens.n_voting} of {ens.n_modules} modules reported, "
                    f"net {ens.net_score:+.0f}, confidence {conf:.0%} — "
                    + ("vetoed by risk." if risk.verdict == "VETO" else
                       "below the threshold that justifies committing capital."))
    else:
        headline = (f"{ens.ticker}: {action.split(' (')[0].lower()} at {risk.position_size_pct:.2f}% of "
                    f"equity, stop {risk.stop_price:.2f}, risking {risk.max_loss_pct:.2f}% of equity. "
                    f"Confidence {band} across {ens.n_voting} reporting modules.")

    # The headline is the one sentence that gets read, quoted and pasted
    # elsewhere. If the data is not current, the sentence says so — a warning
    # that lives only in a sibling field travels nowhere.
    stale = bool(provenance.get("stale")) if provenance else False
    if stale:
        as_of = (provenance or {}).get("last_bar") or "an unknown date"
        headline = f"[STALE DATA — as of {as_of}] " + headline

    return {
        "action": action,
        "headline": headline,
        "data_stale": stale,
        "direction": ens.direction,
        "confidence": round(conf, 4),
        "confidence_band": band,
        "position_size_pct": risk.position_size_pct,
        "entry": risk.entry_price,
        "stop": risk.stop_price,
        "target": risk.target_price,
        "max_loss_pct_of_equity": risk.max_loss_pct,
        "invalidation": risk.invalidation,
        "risk_verdict": risk.verdict,
        "dissent_count": len(ens.dissent),
        "execution_note": "Proposal only. Nothing here places an order; the desk requires explicit "
                          "human approval before any capital moves.",
    }


def screen(tickers: list[str], *, limit: int = 10) -> dict:
    """Run the pipeline across several names and rank by risk-adjusted conviction.
    Logging is off for screens — a screen is a search, not a call."""
    out = []
    for t in tickers[:limit]:
        try:
            res = analyze(t, log=False)
            if "error" in res:
                continue
            out.append({
                "ticker": res["ticker"],
                "action": res["recommendation"]["action"],
                "direction": res["ensemble"]["direction"],
                "net_score": res["ensemble"]["net_score"],
                "confidence": res["recommendation"]["confidence"],
                "size_pct": res["risk"]["position_size_pct"],
                "reporting_modules": res["module_count"]["reporting"],
                "dispersion": res["ensemble"]["dispersion"],
                "headline": res["recommendation"]["headline"],
            })
        except Exception as e:
            logger.warning(f"v5 screen failed on {t}: {e}")
    out.sort(key=lambda r: -(r["confidence"] * (1 if r["direction"] != "neutral" else 0)))
    return {"as_of": datetime.now().isoformat(timespec="seconds"),
            "count": len(out), "results": out}
