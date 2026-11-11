"""
src/trading/scorecard.py
========================
How a strategy is judged. Never by how much it made.

    trades · win rate · profit factor · Sharpe · Sortino · max drawdown
    average return · volatility · turnover · fees · slippage · capacity
    tail loss · out-of-sample performance

RAW RETURN IS NOT A RANKING
---------------------------
`rank()` refuses to sort on total profit. A strategy that made 40% by putting
everything into one position twice is not better than one that made 8% steadily,
and at this account size a single lucky fill can dominate a year. Ranking uses
risk- and cost-adjusted performance, and a strategy without enough
out-of-sample trades is not ranked at all — it is listed as insufficient
evidence, which is a different statement from "bad".

HONEST ANNUALISATION
--------------------
Sharpe is computed per trade and annualised only when there is enough elapsed
time to estimate a trade rate — otherwise `sharpe_annualised` is None and says
why. Multiplying a fortnight of paper trading by sqrt(252) produces an
impressive number and no information.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime
from typing import Iterable, Optional

#: Below this, a scorecard is descriptive only — never a basis for promotion.
MIN_TRADES_FOR_RANKING = 20
#: Elapsed days needed before a per-trade Sharpe is annualised at all.
MIN_DAYS_FOR_ANNUALISATION = 20


def _dt(v) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v))
    except Exception:
        return None


def _returns_bps(rows: list[dict]) -> list[float]:
    out = []
    for r in rows:
        notional = float(r.get("notional") or 0)
        if notional <= 0:
            continue
        out.append(float(r.get("realised_pnl", 0)) / notional * 1e4)
    return out


def score(rows: Iterable[dict], *, strategy_id: str = "",
          equity_base: Optional[float] = None) -> dict:
    """Build a scorecard from closed-trade rows (outcomes.outcomes())."""
    rows = [r for r in rows if r.get("exit_at")]
    n = len(rows)
    card: dict = {"strategy_id": strategy_id or "all", "trades": n}
    if n == 0:
        card.update(note="no closed trades", rankable=False)
        return card

    rets = _returns_bps(rows)
    pnls = [float(r.get("realised_pnl", 0)) for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win, gross_loss = sum(wins), abs(sum(losses))

    card["win_rate"] = round(len(wins) / n, 4)
    card["profit_factor"] = (round(gross_win / gross_loss, 3) if gross_loss > 0
                             else (None if gross_win == 0 else math.inf))
    card["avg_return_bps"] = round(statistics.fmean(rets), 2) if rets else None
    card["volatility_bps"] = (round(statistics.pstdev(rets), 2)
                              if len(rets) > 1 else None)
    card["net_pnl"] = round(sum(pnls), 4)
    card["fees_total"] = round(sum(float(r.get("fees", 0)) for r in rows), 4)
    card["fees_bps_mean"] = round(statistics.fmean(
        [float(r.get("fees", 0)) / float(r["notional"]) * 1e4
         for r in rows if float(r.get("notional") or 0) > 0] or [0]), 2)
    card["slippage_bps_mean"] = round(statistics.fmean(
        [float(r.get("slippage_bps", 0)) for r in rows]), 2)
    card["turnover_notional"] = round(sum(float(r.get("notional") or 0) for r in rows), 2)
    if equity_base:
        card["turnover_x_equity"] = round(card["turnover_notional"] / equity_base, 2)
    card["avg_holding_period_s"] = round(statistics.fmean(
        [float(r.get("holding_period_s", 0)) for r in rows]), 1)

    # risk-adjusted
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    mean = statistics.fmean(rets) if rets else 0.0
    card["sharpe_per_trade"] = round(mean / sd, 3) if sd > 0 else None
    downside = [r for r in rets if r < 0]
    dsd = statistics.pstdev(downside) if len(downside) > 1 else 0.0
    card["sortino_per_trade"] = round(mean / dsd, 3) if dsd > 0 else None

    starts = sorted(x for x in (_dt(r.get("entry_at")) for r in rows) if x)
    ends = sorted(x for x in (_dt(r.get("exit_at")) for r in rows) if x)
    days = ((ends[-1] - starts[0]).total_seconds() / 86400) if starts and ends else 0.0
    card["elapsed_days"] = round(days, 2)
    if days >= MIN_DAYS_FOR_ANNUALISATION and card["sharpe_per_trade"] is not None:
        per_year = n / (days / 365.25)
        card["trades_per_year_observed"] = round(per_year, 1)
        card["sharpe_annualised"] = round(card["sharpe_per_trade"] * math.sqrt(per_year), 3)
    else:
        card["sharpe_annualised"] = None
        card["annualisation_note"] = (
            f"{days:.1f} days of history — too little to estimate a trade rate; "
            f"annualising it would invent precision")

    # drawdown on the cumulative net P&L path
    peak, dd, cum = 0.0, 0.0, 0.0
    base = equity_base or max(1.0, abs(sum(float(r.get("notional") or 0) for r in rows)) / n)
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    card["max_drawdown"] = round(dd, 4)
    card["max_drawdown_pct"] = round(dd / base * 100, 3) if base else None

    # tail: the worst 5% of trades, which is what actually ends accounts
    k = max(1, int(round(0.05 * n)))
    worst = sorted(rets)[:k]
    card["tail_loss_bps_mean"] = round(statistics.fmean(worst), 2)
    card["worst_trade_bps"] = round(min(rets), 2) if rets else None

    # capacity: the largest size actually traded, and whether edge held there
    sizes = sorted(float(r.get("notional") or 0) for r in rows)
    card["largest_notional"] = round(sizes[-1], 2)
    big = [r for r in rows if float(r.get("notional") or 0) >= sizes[-max(1, n // 4)]]
    card["edge_bps_in_largest_quartile"] = round(statistics.fmean(_returns_bps(big)), 2) \
        if big and _returns_bps(big) else None
    card["capacity_note"] = ("capacity beyond the largest size traded is unmeasured; "
                             "it cannot be inferred from smaller fills")

    oos = [r for r in rows if r.get("out_of_sample")]
    card["oos_trades"] = len(oos)
    card["oos_avg_return_bps"] = (round(statistics.fmean(_returns_bps(oos)), 2)
                                  if _returns_bps(oos) else None)

    card["rankable"] = len(oos) >= MIN_TRADES_FOR_RANKING
    if not card["rankable"]:
        card["rankable_note"] = (f"{len(oos)} out-of-sample trades; "
                                 f"{MIN_TRADES_FOR_RANKING} needed before this "
                                 f"scorecard can be compared with another")
    return card


def quality(card: dict) -> Optional[float]:
    """A single comparable number, or None when there is not enough evidence.

    Risk-adjusted, cost-aware, tail-penalised — deliberately not return.
    """
    if not card.get("rankable"):
        return None
    sharpe = card.get("sharpe_per_trade")
    if sharpe is None:
        return None
    tail = card.get("tail_loss_bps_mean") or 0.0
    avg = card.get("avg_return_bps") or 0.0
    cost_share = 0.0
    if avg > 0:
        cost_share = min(1.0, (card.get("fees_bps_mean", 0)
                               + abs(card.get("slippage_bps_mean", 0))) / (avg + 1e-9))
    tail_penalty = min(1.0, abs(tail) / (abs(avg) * 10 + 1e-9)) if avg else 1.0
    pf = card.get("profit_factor")
    pf_term = 0.0 if pf in (None, math.inf) else min(pf, 3.0) / 3.0
    return round(sharpe * (1 - 0.5 * cost_share) * (1 - 0.3 * tail_penalty)
                 + 0.2 * pf_term, 4)


def rank(cards: list[dict]) -> dict:
    """Ordered by quality, never by profit. Strategies without enough
    out-of-sample evidence are listed separately, not at the bottom."""
    scored, insufficient = [], []
    for c in cards:
        q = quality(c)
        (scored if q is not None else insufficient).append(
            {**({"quality": q} if q is not None else
                {"why": c.get("rankable_note", "insufficient evidence")}),
             "strategy_id": c.get("strategy_id"), "trades": c.get("trades"),
             "sharpe_per_trade": c.get("sharpe_per_trade"),
             "net_pnl": c.get("net_pnl")})
    scored.sort(key=lambda r: r["quality"], reverse=True)
    return {"ranked": scored, "insufficient_evidence": insufficient,
            "basis": ("risk-adjusted, cost-aware, tail-penalised; total profit is "
                      "reported but never ranked on")}
