"""
src/v5/risk_gate.py
===================
The non-negotiable gate (spec §5).

No recommendation reaches Soundariyan Karunakaran without passing through here, and this layer
has veto power over every engine above it. Sizing is tied to realised volatility
and conviction — never to a flat rule — and capital preservation outranks
return-seeking wherever the two trade off.

The caps are read from the desk configuration (data/desk_config.json) so that
V5 and the live desk cannot drift into disagreeing about what is allowed. The
checks themselves are enforced in code; no LLM has a path into this file.

Every approved recommendation leaves here carrying an explicit invalidation
condition: the specific evidence that would prove the thesis wrong.
"""
from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Optional

from src.v5 import marketdata as md
from src.v5.contract import ModuleReport
from src.v5.ensemble import EnsembleResult

logger = logging.getLogger(__name__)

# Risk budget per position, expressed as the fraction of equity that would be
# lost if the stop were hit. Sizing is derived from this and volatility.
RISK_BUDGET_PCT = 0.75          # % of equity at risk per position
STOP_ATR_MULTIPLE = 2.0         # stop distance in ATRs
MIN_CONFIDENCE = 0.55           # P(direction correct) below which no capital is committed
FULL_SIZE_EDGE = 0.25           # edge at which the full risk budget is released
MAX_ADV_PARTICIPATION = 0.05    # a position may not exceed 5% of average daily volume
MAX_CORRELATION = 0.80          # correlation to an existing holding that triggers a cut
DEFAULT_EQUITY = 100_000.0


@dataclass
class RiskCheck:
    name: str
    passed: bool
    detail: str
    severity: str = "info"      # info | warn | veto

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskVerdict:
    ticker: str
    verdict: str                            # APPROVE | REDUCE | NO_TRADE | VETO
    approved: bool
    position_size_pct: float                # % of equity
    notional: float
    shares: Optional[float]
    entry_price: Optional[float]
    stop_price: Optional[float]
    target_price: Optional[float]
    risk_per_share: Optional[float]
    max_loss_pct: float                     # % of equity if the stop is hit
    reward_risk: Optional[float]
    invalidation: str
    checks: list = field(default_factory=list)
    stress: dict = field(default_factory=dict)
    equity: float = DEFAULT_EQUITY
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["checks"] = [c.to_dict() if isinstance(c, RiskCheck) else c for c in self.checks]
        return d


# ── inputs ──────────────────────────────────────────────────────────────────

def _config() -> dict:
    try:
        from src.desk.config import load_config
        return load_config()
    except Exception:
        return {"max_name_pct": 5.0, "heat_cap_pct": 10.0, "min_conviction": 65,
                "max_correlated_positions": 3}


def _account() -> dict:
    """Live paper account if the desk is connected; a stated notional otherwise."""
    try:
        from src.desk.auto_executor import account_snapshot
        acc = account_snapshot() or {}
        if acc.get("connected") and acc.get("equity"):
            return {"equity": float(acc["equity"]), "positions": acc.get("positions") or [],
                    "source": "live paper account"}
    except Exception as e:
        logger.debug(f"v5 risk: no account snapshot ({e})")
    return {"equity": DEFAULT_EQUITY, "positions": [],
            "source": f"assumed ${DEFAULT_EQUITY:,.0f} notional — no broker account connected"}


def _open_heat(positions: list, equity: float) -> float:
    """Stop-distance risk already open on the book, as a % of equity.

    Each open position is charged the risk it would lose to a 2×ATR adverse move
    — the same stop convention this gate applies to a new entry. Positions whose
    ATR cannot be computed are charged a flat 2% of their market value, which is
    deliberately conservative: an unmeasurable position is not a free one.
    """
    if not positions or equity <= 0:
        return 0.0
    total = 0.0
    for pos in positions[:20]:
        sym = pos.get("symbol") or pos.get("ticker")
        mv = abs(float(pos.get("market_value") or 0))
        if not sym or mv <= 0:
            continue
        atr = _atr(sym)
        c = md.closes(sym, period="3mo")
        px = float(c.iloc[-1]) if c is not None and len(c) else None
        frac = (STOP_ATR_MULTIPLE * atr / px) if (atr and px) else 0.02
        total += mv * frac / equity * 100
    return round(total, 3)


def _atr(ticker: str, n: int = 14) -> Optional[float]:
    df = md.history(ticker, period="6mo")
    if df is None or len(df) < n + 5 or "High" not in df.columns:
        return None
    hl = df["High"] - df["Low"]
    hc = (df["High"] - df["Close"].shift()).abs()
    lc = (df["Low"] - df["Close"].shift()).abs()
    tr = hl.combine(hc, max).combine(lc, max)
    return float(tr.rolling(n).mean().iloc[-1])


# ── the gate ────────────────────────────────────────────────────────────────

def assess(ticker: str, ens: EnsembleResult, reports: list[ModuleReport],
           meta=None) -> RiskVerdict:
    """Size and gate the idea. `meta` is the meta-reasoning result; when it is
    supplied its post-review confidence is what sizes the position, so a thesis
    that failed its own falsification review is sized on the reduced number
    rather than on the ensemble's first impression."""
    cfg = _config()
    confidence = float(getattr(meta, "confidence_after", None) or ens.confidence)
    edge = float(getattr(meta, "edge_after", None) if meta is not None else ens.edge)
    size_scale = min(1.0, edge / FULL_SIZE_EDGE) if FULL_SIZE_EDGE else 1.0
    acc = _account()
    equity = acc["equity"]
    checks: list[RiskCheck] = []
    notes = [f"Equity basis: {acc['source']}."]

    by_name = {r.module: r for r in reports}
    price = None
    c = md.closes(ticker, period="3mo")
    if c is not None and len(c):
        price = float(c.iloc[-1])

    r = md.returns(ticker, period="1y")
    vol = float(r.std() * math.sqrt(252)) if r is not None and len(r) > 30 else None
    atr = _atr(ticker)

    # ── 1. Conviction floor — capital preservation outranks opportunity ─────
    conviction_ok = confidence >= MIN_CONFIDENCE and ens.direction != "neutral"
    checks.append(RiskCheck(
        "conviction floor",
        conviction_ok,
        f"Confidence {confidence:.1%} that the direction is right, against a {MIN_CONFIDENCE:.0%} "
        f"floor; direction {ens.direction}."
        + ("" if conviction_ok else " Not enough edge to justify committing capital."),
        "veto" if not conviction_ok else "info"))

    # ── 2. Volatility-scaled sizing ─────────────────────────────────────────
    size_pct, stop_price, risk_per_share, target_price = 0.0, None, None, None
    if price and (atr or vol):
        stop_distance = (STOP_ATR_MULTIPLE * atr) if atr else (price * (vol or 0.3) / math.sqrt(252) * 4)
        risk_per_share = stop_distance
        stop_price = price - stop_distance if ens.direction == "bull" else price + stop_distance
        target_price = (price + 2 * stop_distance if ens.direction == "bull"
                        else price - 2 * stop_distance)
        # Risk budget scales with edge, not with a flat rule: an edge of 25 points
        # (confidence 62.5%) releases the full budget, half that releases half.
        budget = RISK_BUDGET_PCT * size_scale
        size_pct = (budget / 100.0) * equity / stop_distance * price / equity * 100.0
        checks.append(RiskCheck(
            "volatility sizing", True,
            f"Realised vol {vol:.0%} annualised, ATR {atr:.2f}. Stop {STOP_ATR_MULTIPLE:.0f}×ATR "
            f"= {stop_distance:.2f} ({stop_distance / price:.1%} away). Risking {budget:.2f}% of "
            f"equity implies {size_pct:.2f}% position size." if atr and vol else
            f"Sizing from volatility only ({vol:.0%} annualised) — ATR unavailable."))
    else:
        checks.append(RiskCheck("volatility sizing", False,
                                "No price or volatility data — position cannot be sized.", "veto"))

    # ── 3. Name cap ─────────────────────────────────────────────────────────
    name_cap = float(cfg.get("max_name_pct", 5.0))
    if size_pct > name_cap:
        checks.append(RiskCheck("name concentration cap", False,
                                f"Volatility sizing wanted {size_pct:.2f}%; desk cap is "
                                f"{name_cap:.1f}% of equity per name. Cut to the cap.", "warn"))
        size_pct = name_cap
    else:
        checks.append(RiskCheck("name concentration cap", True,
                                f"{size_pct:.2f}% is within the {name_cap:.1f}% per-name cap."))

    # ── 4. Liquidity ────────────────────────────────────────────────────────
    liq = by_name.get("liquidity")
    adv = None
    if liq and not liq.insufficient_data:
        for e in liq.evidence:
            if "dollar volume" in e["claim"] and isinstance(e.get("value"), (int, float)):
                adv = float(e["value"])
                break
    if adv:
        notional = size_pct / 100 * equity
        participation = notional / adv
        ok = participation <= MAX_ADV_PARTICIPATION
        if not ok:
            capped = MAX_ADV_PARTICIPATION * adv / equity * 100
            checks.append(RiskCheck("liquidity", False,
                                    f"${notional:,.0f} would be {participation:.1%} of ${adv / 1e6:.1f}M "
                                    f"average daily volume, above the {MAX_ADV_PARTICIPATION:.0%} limit. "
                                    f"Cut to {capped:.2f}% of equity.", "warn"))
            size_pct = min(size_pct, capped)
        else:
            shown = f"{participation:.2%}" if participation >= 0.0001 else "<0.01%"
            checks.append(RiskCheck("liquidity", True,
                                    f"${notional:,.0f} is {shown} of ${adv / 1e6:.1f}M average daily "
                                    f"volume — exitable in a single session."))
    else:
        checks.append(RiskCheck("liquidity", True,
                                "No volume data available; size not constrained by liquidity, which is "
                                "itself a risk for a thinly traded name.", "warn"))

    # ── 5. Correlation to what is already held ──────────────────────────────
    corr_note, worst = "no open positions to correlate against", None
    positions = acc.get("positions") or []
    if positions:
        base = md.returns(ticker, period="1y")
        rows = []
        for pos in positions[:12]:
            sym = pos.get("symbol") or pos.get("ticker")
            if not sym or sym == ticker or base is None:
                continue
            other = md.returns(sym, period="1y")
            if other is None:
                continue
            import pandas as pd
            df = pd.concat([base.rename("a"), other.rename("b")], axis=1, sort=True).dropna()
            if len(df) < 60:
                continue
            cc = float(df["a"].corr(df["b"]))
            if not math.isnan(cc):
                rows.append((sym, cc, float(pos.get("market_value") or 0)))
        if rows:
            worst = max(rows, key=lambda x: abs(x[1]))
            corr_note = (f"Highest correlation to an existing holding: {worst[0]} at {worst[1]:+.2f} "
                         f"(${worst[2]:,.0f} held)")
            if abs(worst[1]) > MAX_CORRELATION:
                size_pct *= 0.5
                checks.append(RiskCheck("correlation exposure", False,
                                        corr_note + f" — above the {MAX_CORRELATION:.2f} limit, so this "
                                                    f"is not a new bet. Size halved to {size_pct:.2f}%.",
                                        "warn"))
            else:
                checks.append(RiskCheck("correlation exposure", True, corr_note))
    if not positions or not worst:
        checks.append(RiskCheck("correlation exposure", True, corr_note))

    # ── 6. Portfolio heat ───────────────────────────────────────────────────
    heat_cap = float(cfg.get("heat_cap_pct", 10.0))
    open_risk = _open_heat(positions, equity)
    new_risk = RISK_BUDGET_PCT * size_scale
    heat_ok = (open_risk + new_risk) <= heat_cap
    checks.append(RiskCheck("portfolio heat", heat_ok,
                            f"Open book already carries {open_risk:.2f}% of stop-distance risk; this "
                            f"position adds {new_risk:.2f}% against a {heat_cap:.1f}% cap."
                            + ("" if heat_ok else " Cap breached — entry refused."),
                            "info" if heat_ok else "veto"))

    # ── 7. Drawdown and tail stress ─────────────────────────────────────────
    stress = _stress(ticker, ens, size_pct, price, vol)
    dd_ok = stress.get("position_loss_worst_month_pct", 0) <= 3.0
    checks.append(RiskCheck("tail stress", dd_ok,
                            f"Worst historical month for {ticker} ({stress.get('worst_month_pct', 0):.1f}%) "
                            f"would cost {stress.get('position_loss_worst_month_pct', 0):.2f}% of equity "
                            f"at this size."
                            + ("" if dd_ok else " Above the 3% single-position stress limit — reduce."),
                            "info" if dd_ok else "warn"))
    if not dd_ok and stress.get("worst_month_pct"):
        scale = 3.0 / max(0.01, stress["position_loss_worst_month_pct"])
        size_pct *= scale
        notes.append(f"Size scaled by {scale:.2f}× so the historical worst month costs no more than "
                     f"3% of equity.")

    # ── 8. Regime gate ──────────────────────────────────────────────────────
    regime = by_name.get("market_regime")
    if regime and not regime.insufficient_data and regime.view == "bear" and ens.direction == "bull":
        size_pct *= 0.75
        checks.append(RiskCheck("regime gate", False,
                                "Long into a regime the regime module reads as bearish — size cut 25%. "
                                "Fighting the tape is allowed; doing it at full size is not.", "warn"))
    else:
        checks.append(RiskCheck("regime gate", True,
                                f"Regime module reads {regime.view if regime else 'unavailable'}; "
                                f"no conflict with a {ens.direction} position."))

    # ── verdict ─────────────────────────────────────────────────────────────
    vetoes = [c for c in checks if c.severity == "veto" and not c.passed]
    warns = [c for c in checks if c.severity == "warn" and not c.passed]
    size_pct = max(0.0, round(size_pct, 3))

    # A neutral ensemble is not something risk "vetoed" — there was never a
    # proposal to refuse. Reserve VETO for a directional call this layer stopped.
    if ens.direction == "neutral":
        verdict, approved, size_pct = "NO_TRADE", False, 0.0
    elif vetoes:
        verdict, approved, size_pct = "VETO", False, 0.0
    elif size_pct < 0.10:
        verdict, approved, size_pct = "NO_TRADE", False, 0.0
    elif warns:
        verdict, approved = "REDUCE", True
    else:
        verdict, approved = "APPROVE", True

    notional = round(size_pct / 100 * equity, 2)
    shares = round(notional / price, 2) if price and price > 0 and notional else None
    max_loss = round((risk_per_share * shares / equity * 100), 3) if (risk_per_share and shares) else 0.0
    rr = 2.0 if (stop_price and target_price) else None

    return RiskVerdict(
        ticker=ticker, verdict=verdict, approved=approved,
        position_size_pct=size_pct, notional=notional, shares=shares,
        entry_price=round(price, 4) if price else None,
        stop_price=round(stop_price, 4) if stop_price else None,
        target_price=round(target_price, 4) if target_price else None,
        risk_per_share=round(risk_per_share, 4) if risk_per_share else None,
        max_loss_pct=max_loss, reward_risk=rr,
        invalidation=_invalidation(ticker, ens, reports, stop_price),
        checks=checks, stress=stress, equity=equity, notes=notes)


# ── stress testing ──────────────────────────────────────────────────────────

def _stress(ticker: str, ens: EnsembleResult, size_pct: float,
            price: Optional[float], vol: Optional[float]) -> dict:
    out: dict = {}
    c = md.closes(ticker, period="5y")
    if c is None or len(c) < 260:
        return {"note": "insufficient history for a historical stress test"}

    r = c.pct_change().dropna()
    monthly = (c.shift(-21) / c - 1).dropna()
    worst_month = float(monthly.min()) * 100
    worst_day = float(r.min()) * 100
    peak = c.cummax()
    max_dd = float(((c - peak) / peak).min()) * 100
    cur_dd = float((c.iloc[-1] / c.max() - 1)) * 100

    sign = 1 if ens.direction == "bull" else -1
    out.update({
        "worst_day_pct": round(worst_day, 2),
        "worst_month_pct": round(worst_month, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "current_drawdown_pct": round(cur_dd, 2),
        "position_loss_worst_day_pct": round(abs(worst_day) * size_pct / 100, 3),
        "position_loss_worst_month_pct": round(abs(worst_month) * size_pct / 100, 3),
    })

    # Scenario: a market shock transmitted through the measured beta.
    b = md.returns(md.BENCH, period="5y")
    if b is not None:
        import pandas as pd
        df = pd.concat([r.rename("a"), b.rename("b")], axis=1, sort=True).dropna()
        if len(df) > 200 and df["b"].var():
            beta = float(df["a"].cov(df["b"]) / df["b"].var())
            for shock in (-0.05, -0.10, -0.20):
                move = beta * shock * 100 * sign
                out[f"market_{int(abs(shock) * 100)}pct_drop"] = {
                    "beta": round(beta, 2),
                    "instrument_move_pct": round(beta * shock * 100, 2),
                    "position_pnl_pct_of_equity": round(move * size_pct / 100, 3),
                }
    if vol:
        out["vol_doubling"] = {
            "note": "If realised volatility doubles, the volatility-scaled size halves.",
            "current_vol_pct": round(vol * 100, 1),
            "size_at_double_vol_pct": round(size_pct / 2, 3),
        }
    return out


def _invalidation(ticker: str, ens: EnsembleResult, reports: list[ModuleReport],
                  stop_price: Optional[float]) -> str:
    """The specific evidence that would prove the thesis wrong — price level plus
    the falsifying observation from the strongest supporting module."""
    parts = []
    if stop_price:
        parts.append(f"Price closing {'below' if ens.direction == 'bull' else 'above'} "
                     f"{stop_price:.2f} (2×ATR against the position)")
    sig = md.signal(ticker)
    if sig.get("invalidation"):
        parts.append(f"signal-engine invalidation level {float(sig['invalidation']):.2f}")

    top = None
    for r in reports:
        if r.votes and r.view == ens.direction:
            if top is None or abs(r.net) > abs(top.net):
                top = r
    if top is not None:
        if top.module in ("trend_following", "momentum", "relative_strength"):
            parts.append(f"{top.module} flipping state (loss of the 200-day average or of relative "
                         f"strength versus {md.BENCH})")
        elif top.module in ("value", "growth", "quality", "earnings"):
            parts.append(f"a reported quarter that reverses the {top.module} case cited above")
        elif top.module in ("cointegration", "statistical_arbitrage"):
            parts.append("the spread relationship failing its stationarity test on the next run")
        else:
            parts.append(f"{top.module} moving to the opposite view on the next run")
    if not parts:
        return "No invalidation level could be computed — that alone is a reason not to take the trade."
    return "; ".join(parts) + "."
