"""
src/trading/drift.py
====================
Strategies stop working. This notices, and suspends them.

Six comparisons between a baseline window (the evidence a strategy was promoted
on) and a recent window:

    performance   is the realised edge still there?
    volatility    is the return distribution the same shape?
    correlation   has its relationship with the market changed?
    execution     are orders still being filled as they were?
    slippage      is the cost model still right?
    regime        is the market the one it was validated in?

Two drifted checks, or one severe one, suspends the strategy. Suspension is a
reduction in capability, so — like the kill switch — it happens automatically
and needs no approval; coming back does, and at PAPER at best (see
`lifecycle.reinstate`).

The thresholds are conservative on purpose: with a handful of trades a month,
waiting for statistical certainty means waiting past the point where it matters.
A false suspension costs nothing but a review.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

#: recent realised edge may fall this far below baseline before it counts
MAX_EDGE_DROP_BPS = 20.0
#: recent volatility may be this multiple of baseline
MAX_VOL_RATIO = 2.0
MIN_VOL_RATIO = 0.4
#: realised slippage may exceed the baseline figure by this much
MAX_SLIPPAGE_OVERRUN_BPS = 5.0
#: change in correlation with the market that counts as a regime change for it
MAX_CORRELATION_CHANGE = 0.5
#: rejected or unfilled orders as a share of attempts
MAX_REJECT_RATE = 0.25
#: below this many recent trades, drift is not assessed at all
MIN_RECENT_TRADES = 5


@dataclass
class DriftReport:
    strategy_id: str
    verdict: str = "OK"                 # OK | WATCH | SUSPEND | INSUFFICIENT_DATA
    checks: dict = field(default_factory=dict)
    drifted: list = field(default_factory=list)
    severe: list = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {"strategy_id": self.strategy_id, "verdict": self.verdict,
                "checks": self.checks, "drifted": self.drifted,
                "severe": self.severe, "note": self.note}


def _edge(rows: list[dict]) -> list[float]:
    out = []
    for r in rows:
        notional = float(r.get("notional") or 0)
        if notional > 0:
            out.append(float(r.get("realised_pnl", 0)) / notional * 1e4)
    return out


def _corr(a: list[float], b: list[float]) -> Optional[float]:
    n = min(len(a), len(b))
    if n < 3:
        return None
    a, b = a[-n:], b[-n:]
    try:
        return statistics.correlation(a, b)
    except Exception:
        return None


def assess(strategy_id: str, *, baseline: list[dict], recent: list[dict],
           baseline_market: Optional[list[float]] = None,
           recent_market: Optional[list[float]] = None,
           attempts: int = 0, rejects: int = 0,
           baseline_regime: str = "", recent_regime: str = "") -> DriftReport:
    """Compare two windows of closed-trade rows (outcomes.outcomes())."""
    rep = DriftReport(strategy_id)

    def check(name: str, ok: bool, detail: str, severe: bool = False) -> None:
        rep.checks[name] = {"ok": ok, "detail": detail, "severe": severe and not ok}
        if not ok:
            rep.drifted.append(name)
            if severe:
                rep.severe.append(name)

    if len(recent) < MIN_RECENT_TRADES:
        rep.verdict = "INSUFFICIENT_DATA"
        rep.note = (f"{len(recent)} recent trades; at least {MIN_RECENT_TRADES} "
                    f"are needed before drift means anything")
        return rep

    be, re_ = _edge(baseline), _edge(recent)
    b_mean = statistics.fmean(be) if be else 0.0
    r_mean = statistics.fmean(re_) if re_ else 0.0
    drop = b_mean - r_mean
    check("performance",
          drop <= MAX_EDGE_DROP_BPS or r_mean > 0,
          f"realised edge {b_mean:.1f} -> {r_mean:.1f} bps (drop {drop:.1f})",
          severe=(r_mean < 0 and drop > MAX_EDGE_DROP_BPS))

    b_sd = statistics.pstdev(be) if len(be) > 1 else 0.0
    r_sd = statistics.pstdev(re_) if len(re_) > 1 else 0.0
    ratio = (r_sd / b_sd) if b_sd > 0 else None
    check("volatility",
          ratio is None or MIN_VOL_RATIO <= ratio <= MAX_VOL_RATIO,
          f"return volatility {b_sd:.1f} -> {r_sd:.1f} bps"
          + (f" (x{ratio:.2f})" if ratio else " (no baseline)"))

    bc = _corr(be, baseline_market or [])
    rc = _corr(re_, recent_market or [])
    if bc is None or rc is None:
        check("correlation", True, "no market series supplied — not assessed")
    else:
        check("correlation", abs(rc - bc) <= MAX_CORRELATION_CHANGE,
              f"correlation with the market {bc:+.2f} -> {rc:+.2f}")

    if attempts > 0:
        rate = rejects / attempts
        check("execution", rate <= MAX_REJECT_RATE,
              f"{rejects}/{attempts} orders rejected or unfilled ({rate:.0%})",
              severe=rate > 0.5)
    else:
        check("execution", True, "no order attempts recorded in the window")

    b_slip = statistics.fmean([float(r.get("slippage_bps", 0)) for r in baseline]) \
        if baseline else 0.0
    r_slip = statistics.fmean([float(r.get("slippage_bps", 0)) for r in recent])
    check("slippage", r_slip - b_slip <= MAX_SLIPPAGE_OVERRUN_BPS,
          f"slippage {b_slip:.1f} -> {r_slip:.1f} bps",
          severe=(r_slip - b_slip > 3 * MAX_SLIPPAGE_OVERRUN_BPS))

    if baseline_regime and recent_regime:
        check("regime", baseline_regime == recent_regime,
              f"validated in {baseline_regime}, now trading in {recent_regime}")
    else:
        check("regime", True, "regime not labelled — not assessed")

    if rep.severe or len(rep.drifted) >= 2:
        rep.verdict = "SUSPEND"
    elif rep.drifted:
        rep.verdict = "WATCH"
    rep.note = ("suspend: " + ", ".join(rep.drifted)) if rep.verdict == "SUSPEND" else ""
    return rep


def monitor(strategy_id: str, *, window: int = 20, **kwargs) -> DriftReport:
    """Assess from recorded outcomes and act: SUSPEND suspends. Reducing what a
    strategy may do never needs approval."""
    from . import outcomes as _out
    rows = _out.outcomes(strategy_id)
    rows = [r for r in rows if r.get("exit_at")]
    recent, baseline = rows[-window:], rows[:-window]
    rep = assess(strategy_id, baseline=baseline or rows[:window], recent=recent, **kwargs)
    if rep.verdict == "SUSPEND":
        try:
            from . import lifecycle
            lifecycle.suspend(strategy_id, reason=f"drift: {', '.join(rep.drifted)}",
                              actor="drift-monitor")
        except Exception as exc:
            logger.error("drift: could not suspend %s: %s", strategy_id, exc)
            rep.note += f" (suspension failed: {exc})"
        _out.record_failure("strategy drift", **rep.to_dict())
    return rep
