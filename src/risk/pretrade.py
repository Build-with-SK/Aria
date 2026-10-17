"""
src/risk/pretrade.py
====================
HOW MUCH RISK DOES THIS TRADE ADD — and so, may it go?

    signal -> PORTFOLIO CONTEXT -> RISK (this) -> SESSION -> EXECUTION

A signal's confidence is not permission. `assess()` takes a proposed order
and THE book (src/trading/account.AccountState) and answers with one of:

    ACCEPT        the trade fits every limit at the requested size
    REDUCE_SIZE   it fits at a smaller size, given with the binding limit
    DEFER         it cannot be judged now — stale or unknown portfolio,
                  price, FX, a market that is not trading, a spread too wide
                  to deal in — re-evaluate later, never send blind
    REJECT        no size fits: a limit is already full, the asset class is
                  not allowed, the trade would stack correlated positions

HOW THE SIZE IS FOUND
---------------------
Each limit is solved for the largest quantity it allows, and the smallest
wins (`binding`):

    name, sector, gross        % of equity, the path's own limits
    currency                   one foreign currency / all foreign currencies
    country                    one foreign country
    liquidity                  % of average daily volume
    volatility                 portfolio vol after the trade (quadratic in
                               size; a trade that LOWERS vol is never blocked
                               by this)
    market beta                portfolio beta after the trade
    correlated cluster         positions moving with this one, as a group

MISSING DATA MAKES IT MORE CONSERVATIVE, NEVER LESS
---------------------------------------------------
No volatility estimate, or no volume history, and the position may be at most
`unknown_risk_name_fraction` of the normal name limit. An unknown FX rate, an
unvalued holding, a stale portfolio or price: DEFER. Nothing is estimated
from a neighbour, a default or today's value.

REDUCING IS NOT ADDING
----------------------
An order that makes an existing position smaller is ACCEPTED in full (an
exit must never be blocked by the limits it brings the book back under). An
order LABELLED reduce-only that would not reduce is REJECTED.

Pure apart from `data.history()` reads; no broker, no clock beyond `at`.
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import numpy as np

from src.risk import covariance as _cov
from src.risk import factors as _factors
from src.risk import fx as _fx
from src.risk import portfolio_risk as _pr

EPS = 1e-12
# A quote fetched during the check can be milliseconds newer than its start.
MAX_CLOCK_SKEW_S = 5.0


class Verdict(str, Enum):
    ACCEPT = "ACCEPT"
    REDUCE_SIZE = "REDUCE_SIZE"
    DEFER = "DEFER"
    REJECT = "REJECT"


#: base currency -> the home country whose concentration is not "foreign"
HOME_COUNTRY = {"USD": "US", "GBP": "GB", "JPY": "JP", "INR": "IN", "HKD": "HK",
                "AUD": "AU", "CAD": "CA", "CHF": "CH", "CNY": "CN", "SGD": "SG"}


@dataclass(frozen=True)
class PortfolioRiskLimits:
    """Name, sector and gross come from the path's existing limits (never
    invented here); the rest are portfolio-level policy defaults, stated."""
    max_name_pct: float = 10.0
    max_sector_pct: float = 35.0
    max_gross_pct: float = 100.0
    max_correlated_positions: int = 3
    correlation_threshold: float = _pr.CORRELATION_THRESHOLD
    max_single_foreign_currency_pct: float = 25.0
    max_total_foreign_currency_pct: float = 50.0
    max_single_foreign_country_pct: float = 35.0
    max_portfolio_vol_pct: float = 25.0          # annualised
    max_market_beta: float = 1.25
    max_adv_participation_pct: float = 5.0       # = src/v5/risk_gate MAX_ADV_PARTICIPATION
    max_spread_bps: float = 50.0                 # = session_orders SessionPolicy.max_spread_bps
    unknown_risk_name_fraction: float = 0.5
    max_portfolio_age_s: float = 120.0           # = src/trading/risk MAX_ACCOUNT_AGE_S
    max_price_age_s: float = 60.0
    allowed_asset_classes: Optional[tuple] = None
    source: str = "policy defaults"

    @classmethod
    def from_trading_limits(cls, lim) -> "PortfolioRiskLimits":
        """The autonomous trading layer's own hard limits (src/trading/limits)."""
        return cls(max_name_pct=float(lim.max_instrument_exposure_pct),
                   max_sector_pct=float(lim.max_sector_exposure_pct),
                   max_gross_pct=float(lim.max_leverage) * 100.0,
                   max_price_age_s=float(lim.max_quote_age_seconds),
                   source="src/trading/limits (name/sector/gross/price age) + portfolio defaults")

    @classmethod
    def from_profile(cls, profile) -> "PortfolioRiskLimits":
        """The desk's risk profile (src/risk/profiles) — the same numbers its
        RiskOfficer enforces."""
        return cls(max_name_pct=float(profile.max_name_pct),
                   max_sector_pct=float(profile.max_sector_pct),
                   max_gross_pct=float(profile.max_gross_exposure_pct),
                   max_correlated_positions=int(profile.max_correlated_positions),
                   allowed_asset_classes=tuple(getattr(profile, "allowed_asset_classes", ()) or ())
                   or None,
                   source=f"risk profile {profile.name} + portfolio defaults")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ProposedOrder:
    symbol: str
    side: str                                    # buy | sell
    qty: float
    price: Optional[float]                       # quote currency, the side's touch
    price_at: Optional[datetime]
    asset_class: str = "equity"
    currency: Optional[str] = None               # None: resolved from the symbol
    reduce_only: bool = False
    spread_bps: Optional[float] = None
    lot: float = 1.0                             # 1.0 whole units, 1e-6 fractional
    sector: str = ""
    strategy_id: str = ""
    #: The prediction behind the trade (src/intelligence/conviction.Conviction
    #: as a dict), or None. CONTEXT, never permission: it is recorded with the
    #: decision and can only REJECT (it does not support this side) or DEFER
    #: (its data are POOR). It never enlarges an order.
    conviction: Optional[dict] = None


@dataclass
class RiskVerdict:
    verdict: Verdict
    symbol: str
    side: str
    requested_qty: float
    approved_qty: float
    at: str
    reasons: list = field(default_factory=list)
    checks: dict = field(default_factory=dict)
    caps: dict = field(default_factory=dict)          # limit -> largest qty it allows
    binding: Optional[str] = None
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    incremental: dict = field(default_factory=dict)
    liquidity: dict = field(default_factory=dict)
    factors: dict = field(default_factory=dict)
    session: Optional[dict] = None
    plan: Optional[dict] = None
    data: dict = field(default_factory=dict)          # what was used, and as of when
    limits: dict = field(default_factory=dict)
    timings_ms: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def ok(self) -> bool:
        return self.verdict in (Verdict.ACCEPT, Verdict.REDUCE_SIZE) and self.approved_qty > 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        d["ok"] = self.ok
        return d


def _floor(q: float, lot: float) -> float:
    if not math.isfinite(q) or q <= 0:
        return 0.0
    return math.floor(q / lot + 1e-9) * lot


def _liquidity(symbol: str, data, at: datetime) -> dict:
    """Average daily volume (shares), daily volatility, from history known at
    `at`. Missing is reported as missing."""
    if data is None:
        return {"known": False, "why": "no market data supplied"}
    h = data.history(symbol, at)
    if h is None or "Close" not in h or len(h) < 21:
        return {"known": False, "why": "fewer than 21 daily bars known at this instant"}
    out = {"last_bar": h.index[-1].date().isoformat()}
    r = np.log(h["Close"].where(h["Close"] > 0)).diff().dropna().tail(60)
    out["daily_vol"] = float(r.std(ddof=1)) if len(r) > 10 else None
    vol = h["Volume"].tail(20) if "Volume" in h else None
    adv = float(vol.mean()) if vol is not None and len(vol) and vol.mean() > 0 else None
    out["adv_shares"] = adv
    out["known"] = adv is not None and out["daily_vol"] is not None
    if not out["known"]:
        out["why"] = "no volume history" if adv is None else "too little history for volatility"
    return out


def assess(order: ProposedOrder, account, *, at: datetime, limits: PortfolioRiskLimits,
           data=None, session=None) -> RiskVerdict:
    """The pre-trade portfolio-risk decision for one proposed order."""
    if at.tzinfo is None:
        raise ValueError("a risk decision needs an aware instant")
    t0 = time.perf_counter()
    v = RiskVerdict(Verdict.REJECT, order.symbol, order.side, float(order.qty), 0.0,
                    at.isoformat(), limits=limits.to_dict())

    def check(name, ok, detail):
        v.checks[name] = {"ok": bool(ok), "detail": detail}
        if not ok:
            v.reasons.append(f"{name}: {detail}")
        return ok

    def done(verdict, qty=0.0):
        v.verdict, v.approved_qty = verdict, float(qty)
        v.timings_ms["total"] = round((time.perf_counter() - t0) * 1000, 3)
        return v

    side = (order.side or "").lower()
    if side not in ("buy", "sell") or not (order.qty and order.qty > 0):
        check("order_valid", False, f"side {order.side!r} qty {order.qty!r}")
        return done(Verdict.REJECT)

    # ── the state everything else needs ─────────────────────────────────
    v.data["portfolio_as_of"] = account.as_of.isoformat() if getattr(account, "as_of", None) else None
    if not check("portfolio_known", account.known, account.why_unknown or "read from the broker"):
        return done(Verdict.DEFER)
    age = (at - account.as_of).total_seconds() if account.as_of else math.inf
    if not check("portfolio_fresh", -MAX_CLOCK_SKEW_S <= age <= limits.max_portfolio_age_s,
                 f"snapshot {age:.0f}s old (limit {limits.max_portfolio_age_s:.0f}s)"):
        return done(Verdict.DEFER)

    from src.execution import reduce_only as _ro
    held = sum(h.qty for h in account.holdings if _ro.same_symbol(h.symbol, order.symbol))
    delta = order.qty if side == "buy" else -order.qty
    new = held + delta
    reducing = abs(held) > EPS and abs(new) <= abs(held) + EPS and \
        (abs(new) <= EPS or np.sign(new) == np.sign(held))
    if order.reduce_only and not reducing:
        check("reduce_only", False, f"labelled reduce-only but {side} {order.qty:g} against "
                                    f"a position of {held:g} would not reduce it")
        return done(Verdict.REJECT)
    if reducing:
        check("reduces_position", True, f"{side} {order.qty:g} reduces {held:g} to {new:g}")
        return done(Verdict.ACCEPT, order.qty)

    # Pending entries can fill before this order. They are not in the valued
    # holdings yet (market orders often have no price/notional). Defer rather
    # than assuming zero exposure. Existing reducing protections do not block
    # entries; group OCO siblings and reserve each unlinked exit separately.
    reservations: dict[tuple[str, str, object], float] = {}
    for pending in account.pending_orders:
        symbol = pending.get("symbol") or pending.get("ticker") or ""
        pside = str(pending.get("side") or "").lower()
        try:
            qty = float(pending.get("qty", 0))
        except (TypeError, ValueError):
            qty = math.nan
        pheld = sum(h.qty for h in account.holdings if _ro.same_symbol(h.symbol, symbol))
        key = pending.get("parent_id") or pending.get("id") or id(pending)
        group = (symbol, pside, key)
        reservations[group] = max(reservations.get(group, 0.0), qty)
        reducing_pending = (bool(symbol) and math.isfinite(qty) and qty > 0 and
                            pside == _ro.reducing_side(pheld))
        if not reducing_pending:
            check("pending_entries", False, "working entry or unreadable order; "
                  "reconcile the pending book before adding exposure")
            return done(Verdict.DEFER)
    for symbol, pside, _ in reservations:
        pheld = sum(h.qty for h in account.holdings if _ro.same_symbol(h.symbol, symbol))
        reserved = sum(q for (sym, sd, _key), q in reservations.items()
                       if _ro.same_symbol(sym, symbol) and sd == pside)
        if reserved > abs(pheld) + EPS:
            check("pending_entries", False, "pending exits exceed the held position")
            return done(Verdict.DEFER)
    check("pending_entries", True, "no pending exposure-increasing orders")

    if session is not None and not getattr(session, "tradable", False):
        v.session = session.to_dict() if hasattr(session, "to_dict") else dict(session)
        try:
            from src.trading.session_orders import SessionPolicy, TradeIntent, plan_order
            plan = plan_order(TradeIntent(symbol=order.symbol, side=side, qty=order.qty,
                                          reference_price=order.price), session,
                              SessionPolicy(), now=at)
            v.plan = plan.to_dict()
        except Exception as exc:                               # a plan is a courtesy
            v.plan = {"error": str(exc)}
        check("session", False, f"{v.session.get('state')} — the thesis is deferred to a "
                                f"re-evaluation, not executed (plan: "
                                f"{(v.plan or {}).get('kind', 'none')})")
        return done(Verdict.DEFER)

    # ── the prediction behind the trade: context, and a veto, never a lever ─
    # Same rule as src/intelligence/conviction.blocking(), applied to the dict
    # so this module imports nothing that could reach a model.
    if order.conviction is not None:
        c = order.conviction
        v.data["conviction"] = {k: c.get(k) for k in
                                ("model_id", "model_version", "prediction_ref", "direction",
                                 "probability_type", "labels", "routing", "data_quality",
                                 "model_reliability", "disagreement", "calibration_quality",
                                 "research_context")}
        want = {"LONG": "buy", "SHORT": "sell"}.get(c.get("direction") or "")
        if not check("prediction_supports_side", want == side,
                     (f"{c.get('direction')} supports a {side} entry" if want == side else
                      f"the prediction is {c.get('direction') or 'missing'}; a {side} "
                      f"entry is not what it says")):
            return done(Verdict.REJECT)
        dq = c.get("data_quality") or {}
        if not check("prediction_data_usable", dq.get("value") != "POOR",
                     f"prediction data {dq.get('value')}: {dq.get('basis') or ''}".strip()):
            return done(Verdict.DEFER)
    else:
        v.checks["prediction_supports_side"] = {
            "ok": True, "detail": "no prediction attached: the risk layer decides alone"}

    if limits.allowed_asset_classes and \
            (order.asset_class or "").lower() not in limits.allowed_asset_classes:
        check("asset_class_allowed", False, f"{order.asset_class} is not allowed by "
                                            f"{limits.source} ({', '.join(limits.allowed_asset_classes)})")
        return done(Verdict.REJECT)

    price_ok = order.price is not None and math.isfinite(order.price) and order.price > 0
    if not check("price_known", price_ok, f"price {order.price!r}"):
        return done(Verdict.DEFER)
    p_age = (at - order.price_at).total_seconds() if order.price_at else math.inf
    if not check("price_fresh", -MAX_CLOCK_SKEW_S <= p_age <= limits.max_price_age_s,
                 f"price {'is untimed' if not order.price_at else f'{p_age:.0f}s old'} "
                 f"(limit {limits.max_price_age_s:.0f}s)"):
        return done(Verdict.DEFER)
    v.data["price_at"] = order.price_at.isoformat() if order.price_at else None
    if order.spread_bps is not None and not check(
            "spread", order.spread_bps <= limits.max_spread_bps,
            f"spread {order.spread_bps:.1f} bps (limit {limits.max_spread_bps:.0f})"):
        return done(Verdict.DEFER)

    # ── value the book, and this instrument, in the base currency ───────
    t1 = time.perf_counter()
    view = _pr.value(account, at=at, data=data)
    if not check("portfolio_valued", view.known and not view.unvalued,
                 view.why or f"cannot value {sorted(view.unvalued)}: "
                             f"{'; '.join(view.unvalued.values())}"):
        return done(Verdict.DEFER)
    eq = view.equity or 0.0
    if not check("equity_positive", eq > 0, f"equity {eq}"):
        return done(Verdict.REJECT)
    base = view.base
    ccy = order.currency or _fx.native_currency(order.symbol, order.asset_class)
    if not check("currency_known", bool(ccy), f"quote currency of {order.symbol} unknown"):
        return done(Verdict.DEFER)
    if data is None and _fx.major(ccy)[0] != _fx.major(base)[0]:
        rate = _fx.FxRate(ccy, base, None, why="no market data to price FX")
    else:
        rate = _fx.rate(ccy, base, at=at, data=data)
    v.data["fx"] = rate.to_dict()
    if not check("fx_known", rate.ok, rate.why or f"{ccy}->{base}"):
        return done(Verdict.DEFER)
    unit = order.price * rate.rate                       # base currency per unit
    s = 1.0 if side == "buy" else -1.0
    meta = _pr.describe(order.symbol, order.asset_class)
    sector = order.sector or _pr.sector_of(order.symbol, meta["asset_class"])
    major = _fx.major(ccy)[0]
    home = HOME_COUNTRY.get(_fx.major(base)[0])
    v.timings_ms["valuation"] = round((time.perf_counter() - t1) * 1000, 3)

    pos = view.valued()
    name_val = sum(p.value_base for p in pos if _ro.same_symbol(p.symbol, order.symbol))
    sector_g = sum(abs(p.value_base) for p in pos if p.sector == sector)
    gross = view.gross
    ccy_g = sum(abs(p.value_base) for p in pos if _fx.major(p.currency)[0] == major)
    foreign_g = sum(abs(p.value_base) for p in pos if _fx.major(p.currency)[0] != _fx.major(base)[0])
    country = meta["country"]
    country_g = sum(abs(p.value_base) for p in pos if p.country == country) if country else 0.0

    caps = {}
    pct = lambda x: x / 100.0 * eq                                     # noqa: E731
    caps["name"] = (pct(limits.max_name_pct) - s * name_val) / unit
    caps["sector"] = (pct(limits.max_sector_pct) - sector_g) / unit
    caps["gross"] = (pct(limits.max_gross_pct) - gross) / unit
    if major != _fx.major(base)[0]:
        caps["currency"] = (pct(limits.max_single_foreign_currency_pct) - ccy_g) / unit
        caps["foreign_currency_total"] = (pct(limits.max_total_foreign_currency_pct) - foreign_g) / unit
    if country and country != home:
        caps["country"] = (pct(limits.max_single_foreign_country_pct) - country_g) / unit

    # ── what history can say: liquidity, volatility, correlation, factors ─
    t2 = time.perf_counter()
    liq = _liquidity(order.symbol, data, at)
    v.liquidity = dict(liq)
    if liq.get("known"):
        caps["liquidity"] = limits.max_adv_participation_pct / 100.0 * liq["adv_shares"]
        v.data.setdefault("history_last_bar", {})[order.symbol] = liq.get("last_bar")
    weights = view.weights()
    a = s * unit / eq                                     # weight per unit of qty
    vol_known = False
    cov = None
    if data is not None:
        syms = list(dict.fromkeys(list(weights) + [order.symbol]))
        currencies = {p.symbol: p.currency for p in pos}
        currencies[order.symbol] = ccy
        panel = _cov.panel(syms, base=base, at=at, data=data, currencies=currencies)
        cov = _cov.covariance(panel)
        v.data.setdefault("history_last_bar", {}).update(panel.last_bar)
        v.data["covariance"] = {k: cov.to_dict()[k] for k in
                                ("ok", "frequency", "n_obs", "shrinkage", "condition_number",
                                 "psd_repaired", "excluded")}
        vol_known = cov.ok and order.symbol in cov.matrix.index
    v.timings_ms["covariance"] = round((time.perf_counter() - t2) * 1000, 3)
    if not vol_known or not liq.get("known"):
        frac = limits.unknown_risk_name_fraction
        missing = [w for w, ok in (("volatility", vol_known), ("liquidity", liq.get("known")))
                   if not ok]
        caps["unknown_risk"] = (pct(limits.max_name_pct * frac) - s * name_val) / unit
        v.reasons.append(f"conservative: {' and '.join(missing)} unknown — position held to "
                         f"{frac:g} x the name limit")

    vol_before = vol_after_fn = None
    if vol_known and cov is not None:
        cs = list(cov.matrix.index)
        w = np.array([weights.get(x, 0.0) for x in cs])
        m = cov.matrix.to_numpy()
        n = cs.index(order.symbol)
        c0 = float(w @ m @ w)
        A, B = a * a * m[n, n], 2 * a * float((m @ w)[n])
        target = max((limits.max_portfolio_vol_pct / 100.0) ** 2, c0)
        disc = B * B - 4 * A * (c0 - target)
        caps["volatility"] = ((-B + math.sqrt(max(disc, 0.0))) / (2 * A)) if A > 0 else math.inf
        vol_before = math.sqrt(max(c0, 0.0))

        def vol_after_fn(q):
            return math.sqrt(max(c0 + B * q + A * q * q, 0.0))
        corr = _cov.correlation(cov)
        members = [x for x in weights if x in corr.index and x != order.symbol and
                   np.sign(weights[x]) * s * corr.loc[x, order.symbol] >= limits.correlation_threshold]
        cluster_g = sum(abs(weights[x]) for x in members) * eq
        v.incremental["correlated_with"] = sorted(members)
        if len(members) + 1 > limits.max_correlated_positions:
            check("correlated_positions", False,
                  f"would be correlated position #{len(members) + 1} with {sorted(members)} "
                  f"(limit {limits.max_correlated_positions}, corr >= {limits.correlation_threshold})")
            v.caps = {k: round(x, 6) for k, x in caps.items()}
            return done(Verdict.REJECT)
        if members:
            caps["correlated_cluster"] = (pct(limits.max_sector_pct) - cluster_g) / unit

    beta_i = beta_p = None
    if data is not None:
        t3 = time.perf_counter()
        syms = list(dict.fromkeys(list(weights) + [order.symbol]))
        acs = {p.symbol: p.asset_class for p in pos}
        acs[order.symbol] = meta["asset_class"]
        per = _factors.exposures(syms, base=base, at=at, data=data, asset_classes=acs)
        fexp = _factors.portfolio_exposures(weights, per)
        v.factors = {"instrument": per[order.symbol].to_dict(), "portfolio_before": fexp}
        mk = per[order.symbol].exposures.get("market")
        if mk is not None and mk.ok:
            beta_i = mk.beta
            beta_p = (fexp.get("market") or {}).get("exposure", 0.0)
            ab = a * beta_i
            lim = limits.max_market_beta
            if ab > EPS:
                caps["market_beta"] = max(0.0, (max(lim, beta_p) - beta_p) / ab) \
                    if beta_p <= lim else 0.0
            elif ab < -EPS:
                caps["market_beta"] = max(0.0, (beta_p + lim) / -ab) if beta_p >= -lim else 0.0
        v.timings_ms["factors"] = round((time.perf_counter() - t3) * 1000, 3)

    v.caps = {k: round(x, 6) for k, x in caps.items()}
    binding = min(caps, key=lambda key: caps[key])
    q = min(float(order.qty), caps[binding])
    q = _floor(q, order.lot)
    v.binding = binding if caps[binding] < order.qty else None

    # ── before / after, for the record ─────────────────────────────────
    def snap(qty):
        dv = s * qty * unit
        nv = name_val + dv
        g = gross - abs(name_val) + abs(nv)
        out = {"gross_pct": round(g / eq * 100, 4),
               "net_pct": round((view.net + dv) / eq * 100, 4),
               "name_pct": round(abs(nv) / eq * 100, 4),
               "sector": sector, "sector_pct": round((sector_g - abs(name_val) + abs(nv)) / eq * 100, 4),
               "currency": major, "currency_pct": round((ccy_g - abs(name_val) + abs(nv)) / eq * 100, 4),
               "country": country,
               "country_pct": round((country_g - abs(name_val) + abs(nv)) / eq * 100, 4) if country else None}
        if vol_after_fn is not None:
            out["portfolio_vol"] = round(vol_after_fn(qty), 6)
        if beta_i is not None:
            out["market_beta"] = round(beta_p + a * qty * beta_i, 6)
        return out
    v.before, v.after = snap(0.0), snap(q)
    v.incremental.update({
        "gross": round(q * unit, 6), "net": round(s * q * unit, 6),
        "volatility": (round(v.after["portfolio_vol"] - vol_before, 6)
                       if vol_before is not None else None),
        "market_beta": (round(a * q * beta_i, 6) if beta_i is not None else None),
        "currency": major, "fx_rate": rate.rate})
    if liq.get("known") and q > 0:
        part = q / liq["adv_shares"]
        v.liquidity["participation_pct"] = round(part * 100, 6)
        v.liquidity["impact_bps"] = round(liq["daily_vol"] * math.sqrt(part) * 1e4, 4)
        v.liquidity["model"] = "square root: impact = daily vol x sqrt(qty / ADV)"

    if q <= 0:
        check("room", False, f"no size fits — limited by {binding} "
                             f"(largest allowed {max(caps[binding], 0.0):.6g})")
        return done(Verdict.REJECT)
    if q < order.qty - EPS:
        check("size", False, f"reduced from {order.qty:g} to {q:g} by {binding}")
        return done(Verdict.REDUCE_SIZE, q)
    return done(Verdict.ACCEPT, q)


def deferred_plan(symbol: str, side: str, session, at: datetime,
                  qty: float = 0.0, reference_price: Optional[float] = None) -> Optional[dict]:
    """The inert plan for a thesis that met a closed or unknown market
    (src/trading/session_orders): when it may be re-evaluated, and how. A
    preview only — nothing executes a plan; it re-enters the pipeline."""
    try:
        from src.trading.session_orders import SessionPolicy, TradeIntent, plan_order
        return plan_order(TradeIntent(symbol=symbol, side=side, qty=qty,
                                      reference_price=reference_price),
                          session, SessionPolicy(), now=at).to_dict()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
