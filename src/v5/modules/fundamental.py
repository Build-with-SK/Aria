"""
src/v5/modules/fundamental.py
=============================
Fundamental & Style family — Value, Growth, Quality, Factor exposure, Earnings.

These modules score cross-sectionally against the instrument's own peer set
where peers are available, and against stated absolute thresholds where they are
not — declaring which of the two happened, because the absolute version is much
weaker evidence and the interval must widen to say so.

None of these engines has a historical forward base rate for this specific
instrument, so none of them claims a Wilson interval from outcomes. Their
intervals come from the number of independent inputs they actually obtained.
That is the honest ceiling on a fundamental snapshot.
"""
from __future__ import annotations

from typing import Optional

from src.v5 import marketdata as md
from src.v5.contract import Evidence, ModuleReport, insufficient
from src.v5.modules._util import clamp, effective_interval, pct
from src.v5.registry import module

FSRC = "Yahoo Finance fundamentals (via universe dossier)"
NO_BASE_RATE = ("No forward-outcome base rate: this is a point-in-time cross-section, so the "
                "interval reflects input count, not predictive calibration.")
VENDOR = ("Vendor fundamentals can be stale, restated, or wrong — especially for non-US listings "
          "and recent IPOs.")


def _peer_metrics(ticker: str, keys: list[str], n: int = 4) -> dict[str, list[float]]:
    """Peer values for the requested keys. Best-effort: peers that fail to load
    are simply absent, and callers must handle a thin or empty peer set."""
    out: dict[str, list[float]] = {k: [] for k in keys}
    try:
        for p in md.peers(ticker, n) or []:
            sym = p.get("symbol") or p.get("yahoo")
            if not sym:
                continue
            f = md.fundamentals(sym)
            for k in keys:
                v = f.get(k)
                if isinstance(v, (int, float)):
                    out[k].append(float(v))
    except Exception:
        pass
    return out


def _percentile(value: float, sample: list[float], lower_is_better: bool) -> Optional[float]:
    """Where `value` sits in `sample`, as a 0..1 bullishness score."""
    vals = [v for v in sample if isinstance(v, (int, float))]
    if len(vals) < 2:
        return None
    rank = sum(1 for v in vals if v < value) / len(vals)
    return round(1.0 - rank if lower_is_better else rank, 4)


def _band(value: float, cheap: float, rich: float) -> float:
    """Absolute fallback scorer: 1.0 at or below `cheap`, 0.0 at or above `rich`."""
    if rich == cheap:
        return 0.5
    return clamp((rich - value) / (rich - cheap), 0.0, 1.0)


# ── Value ───────────────────────────────────────────────────────────────────

@module("value", "fundamental",
        "Valuation versus peers on earnings, book, cash flow and yield.",
        horizon_days=126)
def value(ticker: str) -> ModuleReport:
    f = md.fundamentals(ticker)
    if not f:
        return insufficient("value", "fundamental", ticker,
                            "no fundamentals available for this instrument "
                            "(index, FX, crypto or unlisted)")
    keys = ["trailingPE", "forwardPE", "priceToBook"]
    peers = _peer_metrics(ticker, keys)
    scores, evidence, used_peers = [], [], False

    specs = [("trailingPE", "trailing P/E", True, 12.0, 40.0),
             ("forwardPE", "forward P/E", True, 11.0, 35.0),
             ("priceToBook", "price/book", True, 1.2, 8.0)]
    for key, label, lower_better, cheap, rich in specs:
        v = f.get(key)
        if not isinstance(v, (int, float)):
            continue
        v = float(v)
        s = _percentile(v, peers.get(key, []), lower_better)
        if s is not None:
            used_peers = True
            basis = f"{len(peers[key])} peers"
        else:
            s = _band(v, cheap, rich)
            basis = "absolute band"
        scores.append(s)
        evidence.append(Evidence(f"{label} {v:.2f} → {s:.0%} attractive vs {basis}",
                                 round(v, 4), FSRC,
                                 "bull" if s > 0.6 else "bear" if s < 0.4 else "neutral"))

    # Dividend yield goes through the units-safe accessor: the vendor field's
    # scale changes between library versions and a 100× error here would silently
    # make every payer look like a deep-value opportunity.
    dy = md.dividend_yield_pct(ticker)
    if dy is not None:
        s = clamp(dy / 5.0, 0.0, 1.0)       # 5% yield scores full marks
        scores.append(s)
        evidence.append(Evidence(f"dividend yield {dy:.2f}% → {s:.0%} attractive vs absolute band",
                                 round(dy, 4), FSRC,
                                 "bull" if s > 0.6 else "neutral"))

    if not scores:
        return insufficient("value", "fundamental", ticker,
                            "none of the four valuation metrics were available")

    p = sum(scores) / len(scores)
    ci = effective_interval(p, len(scores), floor=0.20 if used_peers else 0.30)
    return ModuleReport.from_probability(
        "value", "fundamental", ticker, p, ci,
        thesis=(f"{ticker} scores {p:.0%} on valuation across {len(scores)} metrics, judged "
                f"{'against its peer set' if used_peers else 'against absolute bands (no peer set loaded)'}. "
                f"Cheap is not a catalyst — this is a statement about the price paid, not about timing."),
        evidence=evidence,
        weaknesses=[NO_BASE_RATE, VENDOR,
                    "Value is the classic value trap risk: a low multiple often prices a real "
                    "deterioration the snapshot cannot see.",
                    "" if used_peers else "No peer set loaded, so the comparison is against fixed "
                                          "bands that ignore sector norms — materially weaker."],
        horizon_days=126, n_obs=len(scores))


# ── Growth ──────────────────────────────────────────────────────────────────

@module("growth", "fundamental",
        "Revenue and earnings growth versus peers, with the price already paid for it.",
        horizon_days=126)
def growth(ticker: str) -> ModuleReport:
    f = md.fundamentals(ticker)
    if not f:
        return insufficient("growth", "fundamental", ticker, "no fundamentals available")
    keys = ["revenueGrowth", "earningsGrowth"]
    peers = _peer_metrics(ticker, keys)
    scores, evidence = [], []

    for key, label, cap in [("revenueGrowth", "revenue growth", 0.30),
                            ("earningsGrowth", "earnings growth", 0.40)]:
        v = f.get(key)
        if not isinstance(v, (int, float)):
            continue
        v = float(v)
        s = _percentile(v, peers.get(key, []), lower_is_better=False)
        basis = f"{len(peers[key])} peers" if s is not None else "absolute band"
        if s is None:
            s = clamp(0.5 + v / (2 * cap), 0.0, 1.0)
        scores.append(s)
        evidence.append(Evidence(f"{label} {pct(v)} → {s:.0%} vs {basis}", round(v, 4), FSRC,
                                 "bull" if s > 0.6 else "bear" if s < 0.4 else "neutral"))

    peg = None
    fpe, eg = f.get("forwardPE"), f.get("earningsGrowth")
    if isinstance(fpe, (int, float)) and isinstance(eg, (int, float)) and eg > 0:
        peg = float(fpe) / (float(eg) * 100)
        s = clamp((2.5 - peg) / 2.0, 0.0, 1.0)
        scores.append(s)
        evidence.append(Evidence(f"PEG {peg:.2f} (forward P/E {float(fpe):.1f} over "
                                 f"{pct(eg)} earnings growth)", round(peg, 3), FSRC,
                                 "bull" if peg < 1.5 else "bear"))

    if not scores:
        return insufficient("growth", "fundamental", ticker,
                            "no growth metrics reported for this instrument")

    p = sum(scores) / len(scores)
    ci = effective_interval(p, len(scores), floor=0.22)
    return ModuleReport.from_probability(
        "growth", "fundamental", ticker, p, ci,
        thesis=(f"Growth profile scores {p:.0%} across {len(scores)} inputs"
                + (f", PEG {peg:.2f}" if peg else "") +
                ". Growth is only bullish when it exceeds what the multiple already pays for."),
        evidence=evidence,
        weaknesses=[NO_BASE_RATE, VENDOR,
                    "Trailing growth rates are backward-looking and mean-revert hard at scale.",
                    "A single quarter of distorted comparables can swing these figures without any "
                    "change in the business."],
        horizon_days=126, n_obs=len(scores))


# ── Quality ─────────────────────────────────────────────────────────────────

@module("quality", "fundamental",
        "Margins, returns on equity, leverage and cash generation — business durability.",
        horizon_days=252)
def quality(ticker: str) -> ModuleReport:
    f = md.fundamentals(ticker)
    if not f:
        return insufficient("quality", "fundamental", ticker, "no fundamentals available")

    scores, evidence = [], []
    specs = [
        ("returnOnEquity", "return on equity", False, 0.0, 0.30),
        ("profitMargins", "net margin", False, 0.0, 0.25),
        ("operatingMargins", "operating margin", False, 0.0, 0.30),
        ("debtToEquity", "debt/equity", True, 30.0, 200.0),
    ]
    peers = _peer_metrics(ticker, [k for k, *_ in specs])
    for key, label, lower_better, lo, hi in specs:
        v = f.get(key)
        if not isinstance(v, (int, float)):
            continue
        v = float(v)
        s = _percentile(v, peers.get(key, []), lower_better)
        basis = f"{len(peers[key])} peers" if s is not None else "absolute band"
        if s is None:
            s = _band(v, lo, hi) if lower_better else clamp((v - lo) / (hi - lo), 0.0, 1.0)
        scores.append(s)
        shown = f"{v:.1f}" if key == "debtToEquity" else pct(v)
        evidence.append(Evidence(f"{label} {shown} → {s:.0%} vs {basis}", round(v, 4), FSRC,
                                 "bull" if s > 0.6 else "bear" if s < 0.4 else "neutral"))

    fcf, cash, debt = f.get("freeCashflow"), f.get("totalCash"), f.get("totalDebt")
    if isinstance(fcf, (int, float)):
        s = 1.0 if fcf > 0 else 0.0
        scores.append(s)
        evidence.append(Evidence(f"Free cash flow {float(fcf) / 1e9:+.2f}B",
                                 round(float(fcf), 0), FSRC, "bull" if fcf > 0 else "bear"))
    if isinstance(cash, (int, float)) and isinstance(debt, (int, float)) and debt:
        cover = float(cash) / float(debt)
        scores.append(clamp(cover, 0.0, 1.0))
        evidence.append(Evidence(f"Cash covers {cover:.0%} of total debt", round(cover, 3), FSRC,
                                 "bull" if cover > 0.5 else "bear" if cover < 0.2 else "neutral"))

    if not scores:
        return insufficient("quality", "fundamental", ticker, "no quality metrics reported")

    p = sum(scores) / len(scores)
    ci = effective_interval(p, len(scores), floor=0.20)
    return ModuleReport.from_probability(
        "quality", "fundamental", ticker, p, ci,
        thesis=(f"Business quality scores {p:.0%} across {len(scores)} inputs. Quality is a survival "
                f"argument on a one-year view, not a one-month directional call."),
        evidence=evidence,
        weaknesses=[NO_BASE_RATE, VENDOR,
                    "Quality is the slowest-moving family here; on a 21-day horizon it carries almost "
                    "no timing information and is weighted down accordingly.",
                    "Debt/equity is not comparable across sectors — banks and utilities are structurally "
                    "levered and the peer comparison partly corrects for this, the absolute band does not."],
        horizon_days=252, n_obs=len(scores))


# ── Factor exposure ─────────────────────────────────────────────────────────

@module("factor_exposure", "fundamental",
        "Where the name sits on the classic style factors: size, value, quality, low-vol, momentum.",
        horizon_days=126)
def factor_exposure(ticker: str) -> ModuleReport:
    f = md.fundamentals(ticker)
    r = md.returns(ticker, period="2y")
    if not f and r is None:
        return insufficient("factor_exposure", "fundamental", ticker,
                            "neither fundamentals nor return history available")

    tilts, evidence = {}, []
    mcap = f.get("marketCap")
    if isinstance(mcap, (int, float)):
        b = float(mcap) / 1e9
        tilts["size"] = clamp(1.0 - (b / 500.0), 0.0, 1.0)   # small = high loading
        evidence.append(Evidence(f"Market cap ${b:.1f}B → size loading {tilts['size']:.2f}",
                                 round(b, 2), FSRC, "neutral"))
    pb = f.get("priceToBook")
    if isinstance(pb, (int, float)) and float(pb) > 0:
        tilts["value"] = clamp(1.0 - (float(pb) / 8.0), 0.0, 1.0)
        evidence.append(Evidence(f"Price/book {float(pb):.2f} → value loading {tilts['value']:.2f}",
                                 round(float(pb), 3), FSRC, "neutral"))
    roe = f.get("returnOnEquity")
    if isinstance(roe, (int, float)):
        tilts["quality"] = clamp(float(roe) / 0.30, 0.0, 1.0)
        evidence.append(Evidence(f"ROE {pct(float(roe))} → quality loading {tilts['quality']:.2f}",
                                 round(float(roe), 4), FSRC, "neutral"))
    beta = f.get("beta")
    if isinstance(beta, (int, float)):
        tilts["low_vol"] = clamp(1.5 - float(beta), 0.0, 1.0)
        evidence.append(Evidence(f"Beta {float(beta):.2f} → low-volatility loading "
                                 f"{tilts['low_vol']:.2f}", round(float(beta), 3), FSRC,
                                 "bear" if float(beta) > 1.3 else "neutral"))
    if r is not None and len(r) > 252:
        c = md.closes(ticker, period="2y")
        m = float(c.iloc[-1] / c.iloc[-252] - 1)
        tilts["momentum"] = clamp(0.5 + m, 0.0, 1.0)
        evidence.append(Evidence(f"12-month return {pct(m)} → momentum loading "
                                 f"{tilts['momentum']:.2f}", round(m, 4),
                                 md.source_label(ticker), "bull" if m > 0 else "bear"))

    if len(tilts) < 2:
        return insufficient("factor_exposure", "fundamental", ticker,
                            f"only {len(tilts)} factor loadings could be computed")

    # Factors that have historically carried a positive premium, weighted equally.
    # This is a stated prior about factor premia, not a fitted expectation.
    premium_factors = ["value", "quality", "momentum", "low_vol"]
    active = [tilts[k] for k in premium_factors if k in tilts]
    p = clamp(0.40 + 0.30 * (sum(active) / len(active)), 0.0, 1.0) if active else 0.5
    ci = effective_interval(p, len(active), floor=0.26)

    return ModuleReport.from_probability(
        "factor_exposure", "fundamental", ticker, p, ci,
        thesis=(f"{ticker} loads on " + ", ".join(f"{k} {v:.2f}" for k, v in tilts.items()) +
                f". Weighted across the factors with a historical premium, the tilt scores {p:.0%}."),
        evidence=evidence,
        weaknesses=[NO_BASE_RATE,
                    "Loadings are computed from characteristics, not from a regression against real "
                    "factor return series — they are proxies.",
                    "Factor premia are decade-scale and have long drawdowns; they say almost nothing "
                    "about the next month.",
                    "Equal weighting of the premium factors is a stated assumption, not an optimisation."],
        horizon_days=126, n_obs=len(active))


# ── Earnings ────────────────────────────────────────────────────────────────

@module("earnings", "fundamental",
        "Earnings trajectory and the event risk of the next report.",
        horizon_days=63)
def earnings(ticker: str) -> ModuleReport:
    f = md.fundamentals(ticker)
    if not f:
        return insufficient("earnings", "fundamental", ticker, "no fundamentals available")

    scores, evidence = [], []
    eg, rg = f.get("earningsGrowth"), f.get("revenueGrowth")
    if isinstance(eg, (int, float)):
        scores.append(clamp(0.5 + float(eg), 0.0, 1.0))
        evidence.append(Evidence(f"Earnings growth {pct(float(eg))}", round(float(eg), 4), FSRC,
                                 "bull" if eg > 0 else "bear"))
    if isinstance(rg, (int, float)):
        scores.append(clamp(0.5 + float(rg) * 2, 0.0, 1.0))
        evidence.append(Evidence(f"Revenue growth {pct(float(rg))}", round(float(rg), 4), FSRC,
                                 "bull" if rg > 0 else "bear"))
    if isinstance(eg, (int, float)) and isinstance(rg, (int, float)):
        lev = float(eg) - float(rg)
        scores.append(clamp(0.5 + lev, 0.0, 1.0))
        evidence.append(Evidence(
            f"Operating leverage: earnings growth exceeds revenue growth by {lev * 100:+.1f}pp"
            if lev > 0 else
            f"Negative operating leverage: earnings growth trails revenue growth by {abs(lev) * 100:.1f}pp",
            round(lev, 4), FSRC, "bull" if lev > 0 else "bear"))

    tpe, fpe = f.get("trailingPE"), f.get("forwardPE")
    if isinstance(tpe, (int, float)) and isinstance(fpe, (int, float)) and float(tpe) > 0:
        implied = float(tpe) / float(fpe) - 1
        scores.append(clamp(0.5 + implied, 0.0, 1.0))
        evidence.append(Evidence(f"Forward P/E {float(fpe):.1f} vs trailing {float(tpe):.1f} — the "
                                 f"market prices {implied * 100:+.1f}% earnings change",
                                 round(implied, 4), FSRC, "bull" if implied > 0 else "bear"))

    if not scores:
        return insufficient("earnings", "fundamental", ticker,
                            "no earnings figures reported for this instrument")

    p = sum(scores) / len(scores)
    ci = effective_interval(p, len(scores), floor=0.24)
    return ModuleReport.from_probability(
        "earnings", "fundamental", ticker, p, ci,
        thesis=(f"Earnings trajectory scores {p:.0%} across {len(scores)} inputs. The consensus "
                f"revision path and the exact report date are NOT in this dataset — treat any position "
                f"held through a print as an unhedged event bet."),
        evidence=evidence,
        weaknesses=[NO_BASE_RATE, VENDOR,
                    "No earnings calendar and no analyst revision data in this feed — the single "
                    "largest driver of short-horizon earnings moves is missing entirely.",
                    "Trailing-versus-forward P/E infers expectations from two vendor numbers computed "
                    "on different bases; the implied change is indicative only."],
        horizon_days=63, n_obs=len(scores))
