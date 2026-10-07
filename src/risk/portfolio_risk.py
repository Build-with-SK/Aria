"""
src/risk/portfolio_risk.py
==========================
The book's risk, from THE book. `AccountState` (src/trading/account.py) is the
portfolio state; this module VALUES it in a base currency with cited FX and
measures it. It keeps no copy of the positions.

    value(account, at, data)        -> PortfolioView   positions in base ccy
    analyze(view, at, data)         -> RiskReport      exposure, concentration,
                                                       correlation, volatility,
                                                       risk contributions, factors

WHAT "UNKNOWN" MEANS HERE
-------------------------
A holding whose FX cannot be established is UNVALUED and listed as such; an
instrument without enough history has no volatility; a factor without a proxy
has no exposure. Each is reported with its reason and a COVERAGE figure.
Nothing is filled in to make a report look complete, and the pre-trade check
(src/risk/pretrade.py) treats each gap conservatively.

DIVERSIFICATION IS MEASURED, NOT COUNTED
----------------------------------------
Three positions in AAPL, MSFT and NVDA are three names, one country, one
currency, one sector and — usually — one correlated cluster. Concentration is
reported by every one of those dimensions, by correlated cluster (positions
whose P&L moves together, so a long and a short of correlated names is a
hedge, not a cluster), and as an effective number of independent bets.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from src.risk import covariance as _cov
from src.risk import factors as _factors
from src.risk import fx as _fx

ROOT = Path(__file__).resolve().parent.parent.parent
UNIVERSE_DB = ROOT / "data" / "universe.db"
CORRELATION_THRESHOLD = 0.7

_sector_cache: dict = {}


def sector_of(symbol: str, asset_class: str = "") -> str:
    """Sector from the symbol master, matched on the PROVIDER column only —
    the display column is a different namespace (BA there is BAE Systems). An
    unknown sector is its own bucket, so unknowns never pool into a fake one."""
    key = (symbol, asset_class)
    if key in _sector_cache:
        return _sector_cache[key]
    sector = None
    try:
        if UNIVERSE_DB.exists():
            with sqlite3.connect(str(UNIVERSE_DB)) as conn:
                row = conn.execute("SELECT sector FROM symbols WHERE yahoo = ? LIMIT 1",
                                   (symbol,)).fetchone()
                sector = row[0] if row and row[0] else None
    except Exception:
        sector = None
    if not sector:
        ac = (asset_class or "").lower()
        sector = {"crypto": "Crypto", "forex": "FX", "fx": "FX",
                  "futures": "Futures"}.get(ac, f"Unknown:{symbol}")
    _sector_cache[key] = sector
    return sector


def describe(symbol: str, asset_class: str = "") -> dict:
    """Venue, country and asset class from the session engine's resolution."""
    from src.markets.sessions import resolve
    from src.markets.venues import VENUES
    inst = resolve(symbol, asset_class=asset_class or None)
    spec = VENUES.get(inst.venue) if inst.venue else None
    country = spec.country if spec and spec.country not in ("GLOBAL",) else None
    return {"venue": inst.venue, "country": country,
            "asset_class": (inst.asset_class or asset_class or "equity").lower()}


# ── valuation ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Valued:
    symbol: str
    qty: float
    price: float                     # quote currency
    currency: str
    value_base: Optional[float]      # signed, base currency; None = unvalued
    fx: dict
    asset_class: str
    venue: Optional[str]
    country: Optional[str]
    sector: str


@dataclass
class PortfolioView:
    base: str
    at: str
    known: bool
    why: str
    equity: Optional[float]          # base currency
    cash: Optional[float]
    account_as_of: Optional[str]
    positions: list = field(default_factory=list)
    unvalued: dict = field(default_factory=dict)        # symbol -> why

    def valued(self) -> list:
        return [p for p in self.positions if p.value_base is not None]

    @property
    def gross(self) -> float:
        return sum(abs(p.value_base) for p in self.valued())

    @property
    def net(self) -> float:
        return sum(p.value_base for p in self.valued())

    @property
    def long(self) -> float:
        return sum(p.value_base for p in self.valued() if p.value_base > 0)

    @property
    def short(self) -> float:
        return sum(-p.value_base for p in self.valued() if p.value_base < 0)

    def weights(self) -> dict:
        if not self.equity or self.equity <= 0:
            return {}
        w = {}
        for p in self.valued():
            w[p.symbol] = w.get(p.symbol, 0.0) + p.value_base / self.equity
        return w

    def exposures(self) -> dict:
        eq = self.equity or 0.0
        pct = (lambda v: round(v / eq * 100, 4)) if eq > 0 else (lambda v: None)
        return {"gross": round(self.gross, 6), "net": round(self.net, 6),
                "long": round(self.long, 6), "short": round(self.short, 6),
                "gross_pct": pct(self.gross), "net_pct": pct(self.net),
                "long_pct": pct(self.long), "short_pct": pct(self.short),
                "leverage": round(self.gross / eq, 6) if eq > 0 else None}


def value(account, *, at: datetime, data=None, base: Optional[str] = None) -> PortfolioView:
    """Value an AccountState in `base` (default: the account currency), each
    holding at a cited, point-in-time FX rate."""
    if at.tzinfo is None:
        raise ValueError("valuation needs an aware instant")
    base = (base or account.currency or "USD").upper()
    as_of = account.as_of.isoformat() if getattr(account, "as_of", None) else None
    if not account.known:
        return PortfolioView(base, at.isoformat(), False, account.why_unknown or "unknown",
                             None, None, as_of)

    acct_ccy = account.currency or base

    def convert(amount, ccy):
        if data is None and _fx.major(ccy)[0] != _fx.major(base)[0]:
            r = _fx.FxRate(ccy, base, None, why=f"no market data to price {ccy}->{base}")
        else:
            r = _fx.rate(ccy, base, at=at, data=data)
        return (amount * r.rate if r.ok else None), r

    equity, eq_fx = convert(account.equity, acct_ccy)
    cash, _ = convert(account.cash, acct_ccy)
    view = PortfolioView(base, at.isoformat(), equity is not None,
                         "" if equity is not None else f"account equity: {eq_fx.why}",
                         equity, cash, as_of)
    for h in account.holdings:
        ccy = h.currency or acct_ccy
        meta = describe(h.symbol, h.asset_class)
        v, r = convert(h.market_value, ccy)
        view.positions.append(Valued(
            h.symbol, h.qty, h.market_price, ccy, v, r.to_dict(), meta["asset_class"],
            h.venue or meta["venue"], meta["country"],
            h.sector or sector_of(h.symbol, meta["asset_class"])))
        if v is None:
            view.unvalued[h.symbol] = r.why or "FX unknown"
    return view


# ── concentration ────────────────────────────────────────────────────────────

def _bucket(view: PortfolioView, key) -> dict:
    out = {}
    eq = view.equity or 0.0
    for p in view.valued():
        k = key(p) or "Unknown"
        out[k] = out.get(k, 0.0) + abs(p.value_base)
    return {k: round(v / eq * 100, 4) for k, v in sorted(out.items(), key=lambda kv: -kv[1])} \
        if eq > 0 else {}


def concentration(view: PortfolioView) -> dict:
    eq = view.equity or 0.0
    names = {}
    for p in view.valued():
        names[p.symbol] = names.get(p.symbol, 0.0) + abs(p.value_base)
    sizes = sorted(names.values(), reverse=True)
    gross = sum(sizes)
    hhi = sum((s / gross) ** 2 for s in sizes) if gross > 0 else None
    pct = (lambda v: round(v / eq * 100, 4)) if eq > 0 else (lambda v: None)
    return {
        "positions": len(sizes),
        "largest_pct": pct(sizes[0]) if sizes else 0.0,
        "top5_pct": pct(sum(sizes[:5])), "top10_pct": pct(sum(sizes[:10])),
        "hhi": round(hhi, 6) if hhi is not None else None,
        "effective_names": round(1 / hhi, 4) if hhi else None,
        "by_sector": _bucket(view, lambda p: p.sector),
        "by_country": _bucket(view, lambda p: p.country),
        "by_currency": _bucket(view, lambda p: _fx.major(p.currency)[0]),
        "by_asset_class": _bucket(view, lambda p: p.asset_class),
        "by_venue": _bucket(view, lambda p: p.venue),
    }


def correlated_clusters(weights: dict, corr, threshold: float = CORRELATION_THRESHOLD) -> list:
    """Groups of positions whose P&L moves together: i and j are linked when
    sign(w_i) * sign(w_j) * corr_ij >= threshold (so a hedged pair is not a
    cluster). Connected components; singletons omitted."""
    if corr is None:
        return []
    syms = [s for s in weights if s in corr.index and weights[s] != 0]
    parent = {s: s for s in syms}

    def find(s):
        while parent[s] != s:
            parent[s] = parent[parent[s]]
            s = parent[s]
        return s
    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            if np.sign(weights[a]) * np.sign(weights[b]) * corr.loc[a, b] >= threshold:
                parent[find(a)] = find(b)
    groups = {}
    for s in syms:
        groups.setdefault(find(s), []).append(s)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        pairs = [np.sign(weights[a]) * np.sign(weights[b]) * corr.loc[a, b]
                 for i, a in enumerate(members) for b in members[i + 1:]]
        out.append({"members": sorted(members),
                    "gross_pct": round(sum(abs(weights[m]) for m in members) * 100, 4),
                    "avg_directional_corr": round(float(np.mean(pairs)), 4)})
    return sorted(out, key=lambda c: -c["gross_pct"])


# ── volatility and contributions ────────────────────────────────────────────

def risk_contributions(weights: dict, cov) -> dict:
    """Portfolio volatility with marginal and component contributions
    (Euler: the components sum to the volatility). Only positions inside the
    covariance are covered; coverage says how much of the book that is."""
    if cov is None or not cov.ok:
        return {"ok": False, "why": getattr(cov, "why", "no covariance")}
    syms = [s for s in weights if s in cov.matrix.index]
    gross = sum(abs(v) for v in weights.values()) or 1.0
    covered = sum(abs(weights[s]) for s in syms)
    if not syms:
        return {"ok": False, "why": "no position has a volatility estimate", "coverage": 0.0}
    w = np.array([weights[s] for s in syms])
    m = cov.matrix.loc[syms, syms].to_numpy()
    var = float(w @ m @ w)
    vol = math.sqrt(max(var, 0.0))
    if vol <= 0:
        return {"ok": False, "why": "zero portfolio variance", "coverage": round(covered / gross, 4)}
    mcr = (m @ w) / vol
    ccr = w * mcr
    stand_alone = np.sqrt(np.clip(np.diag(m), 0, None))
    dr = float(np.sum(np.abs(w) * stand_alone) / vol)
    return {"ok": True, "portfolio_vol": round(vol, 8), "frequency": cov.frequency,
            "n_obs": cov.n_obs, "coverage": round(covered / gross, 4),
            "complete": covered >= gross - 1e-9,
            "marginal": {s: round(float(x), 8) for s, x in zip(syms, mcr)},
            "component": {s: round(float(x), 8) for s, x in zip(syms, ccr)},
            "component_pct": {s: round(float(x / vol * 100), 4) for s, x in zip(syms, ccr)},
            "sum_components": round(float(ccr.sum()), 8),
            "diversification_ratio": round(dr, 6),
            "effective_bets": round(dr * dr, 4)}


def factor_volatility(weights: dict, per_instrument: dict, market_vol: Optional[float]) -> dict:
    """Single-index approximation: var = (sum w b)^2 s_m^2 + sum w^2 s_e^2,
    over positions with a measured market beta and residual."""
    if market_vol is None:
        return {"ok": False, "why": "market factor volatility unavailable"}
    beta_sum, idio, covered = 0.0, 0.0, 0.0
    gross = sum(abs(v) for v in weights.values()) or 1.0
    for s, w in weights.items():
        f = per_instrument.get(s)
        ex = f.exposures.get("market") if f else None
        if ex is None or not ex.ok or f.residual_vol is None:
            continue
        beta_sum += w * ex.beta
        idio += (w * f.residual_vol) ** 2
        covered += abs(w)
    if covered == 0:
        return {"ok": False, "why": "no position has a measured market beta"}
    var = (beta_sum * market_vol) ** 2 + idio
    return {"ok": True, "portfolio_vol": round(math.sqrt(var), 8),
            "systematic_vol": round(abs(beta_sum) * market_vol, 8),
            "idiosyncratic_vol": round(math.sqrt(idio), 8),
            "market_beta": round(beta_sum, 6), "coverage": round(covered / gross, 4),
            "method": "single-index (market) approximation"}


# ── the report ───────────────────────────────────────────────────────────────

@dataclass
class RiskReport:
    view: PortfolioView
    concentration: dict
    clusters: list
    volatility: dict
    factor_volatility: dict
    factors: dict
    instruments: dict
    covariance: dict
    timings_ms: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        v = self.view
        return {"base": v.base, "at": v.at, "known": v.known, "why": v.why,
                "equity": v.equity, "cash": v.cash, "account_as_of": v.account_as_of,
                "exposures": v.exposures(), "unvalued": v.unvalued,
                "positions": [{"symbol": p.symbol, "qty": p.qty, "value_base": p.value_base,
                               "currency": p.currency, "fx": p.fx, "venue": p.venue,
                               "country": p.country, "sector": p.sector,
                               "asset_class": p.asset_class} for p in v.positions],
                "concentration": self.concentration, "correlated_clusters": self.clusters,
                "volatility": self.volatility, "factor_volatility": self.factor_volatility,
                "factor_exposures": self.factors,
                "instrument_factors": {k: i.to_dict() for k, i in self.instruments.items()},
                "covariance": self.covariance, "timings_ms": self.timings_ms}


def analyze(view: PortfolioView, *, at: datetime, data=None,
            extra_symbols: tuple = (), asset_classes: Optional[dict] = None,
            frequency: str = "auto", with_factors: bool = True,
            corr_threshold: float = CORRELATION_THRESHOLD) -> RiskReport:
    """Everything measurable about the book at `at`. Without `data` the
    report is exposure and concentration only, and says so."""
    import time
    t0 = time.perf_counter()
    timings = {}
    weights = view.weights()
    conc = concentration(view) if view.known else {}
    timings["concentration"] = round((time.perf_counter() - t0) * 1000, 3)
    if data is None or not view.known:
        why = "no market data supplied" if data is None else "portfolio unknown"
        return RiskReport(view, conc, [], {"ok": False, "why": why},
                          {"ok": False, "why": why}, {}, {}, {"ok": False, "why": why}, timings)
    acs = {p.symbol: p.asset_class for p in view.positions}
    acs.update(asset_classes or {})
    symbols = list(dict.fromkeys(list(weights) + list(extra_symbols)))
    currencies = {p.symbol: p.currency for p in view.positions}
    t1 = time.perf_counter()
    panel = _cov.panel(symbols, base=view.base, at=at, data=data, frequency=frequency,
                       currencies=currencies)
    cov = _cov.covariance(panel)
    corr = _cov.correlation(cov)
    timings["covariance"] = round((time.perf_counter() - t1) * 1000, 3)
    vol = risk_contributions(weights, cov)
    clusters = correlated_clusters(weights, corr, corr_threshold)
    per, fexp, fvol = {}, {}, {"ok": False, "why": "factors not requested"}
    if with_factors and symbols:
        t2 = time.perf_counter()
        per = _factors.exposures(symbols, base=view.base, at=at, data=data,
                                 asset_classes=acs, frequency=panel.frequency
                                 if panel.frequency in _cov.PERIODS else "weekly")
        fexp = _factors.portfolio_exposures(weights, per)
        mret, _ = _factors.factor_returns(["market"], base=view.base, at=at, data=data,
                                          frequency=panel.frequency)
        mv = (float(np.std(mret["market"], ddof=1) * math.sqrt(_cov.PERIODS[panel.frequency]))
              if "market" in mret and len(mret["market"]) > 2 else None)
        fvol = factor_volatility(weights, per, mv)
        timings["factors"] = round((time.perf_counter() - t2) * 1000, 3)
    timings["total"] = round((time.perf_counter() - t0) * 1000, 3)
    cov_d = cov.to_dict()
    cov_d["last_bar"] = panel.last_bar
    return RiskReport(view, conc, clusters, vol, fvol, fexp, per, cov_d, timings)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
