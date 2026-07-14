# src/strategies/master_portfolio.py
"""
Master Portfolio / All-Strategy Synthesiser
Aggregates signals from all 15 strategy modules.
Ranked opportunity list, risk budget allocation, liquidity filter,
deduplication with conviction weighting, full daily briefing Obsidian output.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class RankedIdea:
    rank: int
    ticker: str
    name: str
    direction: str              # "long" | "short"
    asset_class: str
    source_strategies: List[str]
    combined_conviction: float  # weighted average across strategies
    signal_count: int           # how many strategies agree
    estimated_return_pct: float
    risk_budget_strategy: str
    liquidity_flag: bool        # True = liquid enough
    rationale: str


@dataclass
class RiskBudget:
    strategy: str
    allocated_pct: float        # % of total portfolio VaR budget
    utilised_pct: float         # how much is currently used
    utilisation_ratio: float    # utilised / allocated


@dataclass
class MasterPortfolio:
    top_longs: List[RankedIdea]
    top_shorts: List[RankedIdea]
    active_hedges: List[dict]
    risk_budgets: List[RiskBudget]
    portfolio_var: float
    portfolio_cvar: float
    expected_return_pct: float
    estimated_sharpe: float
    combined_score: float           # overall directional conviction
    strategy_count: int
    as_of: str
    notes: List[str]

    def _to_json(self) -> dict:
        return {
            "top_longs": [vars(i) for i in self.top_longs],
            "top_shorts": [vars(i) for i in self.top_shorts],
            "active_hedges": self.active_hedges,
            "risk_budgets": [vars(b) for b in self.risk_budgets],
            "portfolio_var": round(self.portfolio_var, 2),
            "portfolio_cvar": round(self.portfolio_cvar, 2),
            "expected_return_pct": round(self.expected_return_pct, 4),
            "estimated_sharpe": round(self.estimated_sharpe, 3),
            "combined_score": round(self.combined_score, 2),
            "strategy_count": self.strategy_count,
            "as_of": self.as_of,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Risk budget defaults
# ---------------------------------------------------------------------------
_DEFAULT_RISK_BUDGETS: Dict[str, float] = {
    "equity_long_short": 0.25,
    "global_macro":      0.20,
    "quant_systematic":  0.15,
    "options":           0.10,
    "futures":           0.10,
    "forex":             0.10,
    "credit_long_short": 0.05,
    "crypto":            0.05,
    # Others share residual
    "relative_value":    0.05,
    "market_neutral":    0.05,
    "event_driven":      0.03,
    "distressed":        0.03,
    "convertible_arb":   0.02,
    "derivatives_risk":  0.02,
}

_STRATEGY_ASSET_CLASS: Dict[str, str] = {
    "equity_long_short": "Equities",
    "relative_value":    "Equities/Arb",
    "market_neutral":    "Equities",
    "global_macro":      "Multi-Asset",
    "event_driven":      "Equities",
    "distressed":        "Equities/Credit",
    "convertible_arb":   "Fixed Income/Equities",
    "quant_systematic":  "Equities",
    "credit_long_short": "Credit",
    "options":           "Derivatives",
    "futures":           "Futures",
    "crypto":            "Crypto",
    "forex":             "FX",
    "derivatives_risk":  "Derivatives",
    "multi_strategy":    "Multi-Asset",
}


# ---------------------------------------------------------------------------
# Signal extraction helpers
# ---------------------------------------------------------------------------

def _extract_longs_shorts(
    strategy_name: str,
    result: Any,
) -> tuple:
    """
    Extract (longs, shorts) as list of dicts {ticker, conviction, rationale}
    from any strategy result object.
    """
    longs, shorts = [], []

    try:
        if result is None:
            return longs, shorts

        # equity_long_short → LongShortPortfolio
        if hasattr(result, "longs") and hasattr(result, "shorts"):
            for p in (result.longs or []):
                longs.append({"ticker": p.ticker, "conviction": p.signal_score, "rationale": strategy_name})
            for p in (result.shorts or []):
                shorts.append({"ticker": p.ticker, "conviction": abs(p.signal_score), "rationale": strategy_name})

        # relative_value → List[PairsResult]
        elif isinstance(result, list) and result and hasattr(result[0], "signal"):
            for r in result:
                if getattr(r, "signal", "") == "long_spread":
                    longs.append({"ticker": r.ticker_a, "conviction": abs(r.spread_zscore) * 20, "rationale": f"Pairs long {r.ticker_a}/{r.ticker_b}"})
                elif getattr(r, "signal", "") == "short_spread":
                    shorts.append({"ticker": r.ticker_a, "conviction": abs(r.spread_zscore) * 20, "rationale": f"Pairs short {r.ticker_a}/{r.ticker_b}"})

        # market_neutral → MarketNeutralPortfolio
        elif hasattr(result, "positions") and isinstance(result.positions, list):
            for p in result.positions:
                item = {"ticker": p.ticker, "conviction": 50, "rationale": strategy_name}
                if p.direction == "long":
                    longs.append(item)
                else:
                    shorts.append(item)

        # global_macro → MacroPositioning
        elif hasattr(result, "asset_tilts"):
            for tilt in (result.asset_tilts or []):
                for proxy in tilt.ticker_proxies:
                    item = {"ticker": proxy, "conviction": tilt.conviction, "rationale": tilt.rationale}
                    if tilt.direction == "long":
                        longs.append(item)
                    elif tilt.direction == "short":
                        shorts.append(item)

        # event_driven → EventCalendar
        elif hasattr(result, "events"):
            for e in (result.events or []):
                item = {"ticker": e.ticker, "conviction": e.conviction, "rationale": e.rationale}
                if e.recommended_action == "long":
                    longs.append(item)
                elif e.recommended_action == "short":
                    shorts.append(item)

        # distressed → DistressedScreen
        elif hasattr(result, "short_candidates") and hasattr(result, "long_candidates"):
            for t in result.long_candidates:
                longs.append({"ticker": t, "conviction": 45, "rationale": "Distressed opportunistic long"})
            for t in result.short_candidates:
                shorts.append({"ticker": t, "conviction": 60, "rationale": "Distressed short"})

        # quant_systematic → FactorPortfolio
        elif hasattr(result, "long_names") and hasattr(result, "short_names"):
            for t in (result.long_names or []):
                longs.append({"ticker": t, "conviction": 55, "rationale": "Factor model long"})
            for t in (result.short_names or []):
                shorts.append({"ticker": t, "conviction": 55, "rationale": "Factor model short"})

        # credit_long_short → CreditPositioning
        elif hasattr(result, "hy_position"):
            if result.hy_position == "long":
                for t in ["HYG", "JNK"]:
                    longs.append({"ticker": t, "conviction": abs(result.overall_credit_score), "rationale": "Credit risk-on"})
            elif result.hy_position == "short":
                for t in ["HYG", "JNK"]:
                    shorts.append({"ticker": t, "conviction": abs(result.overall_credit_score), "rationale": "Credit risk-off"})

        # options_engine → List[OptionsSignal]
        elif isinstance(result, list) and result and hasattr(result[0], "strategy"):
            for sig in result:
                if sig.strategy in ("bull_spread", "csp"):
                    longs.append({"ticker": sig.ticker, "conviction": sig.conviction, "rationale": f"Options: {sig.strategy}"})
                elif sig.strategy in ("bear_spread",):
                    shorts.append({"ticker": sig.ticker, "conviction": sig.conviction, "rationale": f"Options: {sig.strategy}"})

        # futures / forex / crypto → List with direction
        elif isinstance(result, list) and result and hasattr(result[0], "direction"):
            for sig in result:
                item = {"ticker": sig.ticker, "conviction": getattr(sig, "conviction", 50), "rationale": strategy_name}
                if sig.direction in ("long", "strong_long"):
                    longs.append(item)
                elif sig.direction in ("short", "strong_short"):
                    shorts.append(item)

    except Exception as e:
        logger.debug(f"Signal extraction failed for {strategy_name}: {e}")

    return longs, shorts


def _deduplicate_and_rank(
    ideas: List[dict],   # [{"ticker", "conviction", "rationale", "strategy"}]
    direction: str,
    top_n: int,
) -> List[RankedIdea]:
    """
    Merge same-ticker signals across strategies, weight by conviction.
    Return top_n ranked ideas.
    """
    agg: Dict[str, dict] = {}
    for idea in ideas:
        t = idea["ticker"]
        if t not in agg:
            agg[t] = {
                "ticker": t,
                "strategies": [],
                "conviction_sum": 0.0,
                "count": 0,
                "rationales": [],
            }
        agg[t]["strategies"].append(idea.get("strategy", "unknown"))
        agg[t]["conviction_sum"] += float(idea.get("conviction", 50))
        agg[t]["count"] += 1
        agg[t]["rationales"].append(str(idea.get("rationale", "")))

    ranked = []
    for i, (ticker, data) in enumerate(
        sorted(agg.items(), key=lambda x: x[1]["conviction_sum"], reverse=True)[:top_n]
    ):
        avg_conviction = data["conviction_sum"] / max(data["count"], 1)
        est_return = (avg_conviction / 100.0) * (0.15 if direction == "long" else 0.12)

        ranked.append(RankedIdea(
            rank=i + 1,
            ticker=ticker,
            name=ticker,
            direction=direction,
            asset_class=_infer_asset_class(ticker),
            source_strategies=list(set(data["strategies"])),
            combined_conviction=round(avg_conviction, 2),
            signal_count=data["count"],
            estimated_return_pct=round(est_return * 100, 2),
            risk_budget_strategy=data["strategies"][0] if data["strategies"] else "unknown",
            liquidity_flag=True,    # simplified — full impl would check ADV
            rationale=" | ".join(set(data["rationales"]))[:200],
        ))

    return ranked


def _infer_asset_class(ticker: str) -> str:
    if ticker.endswith("=X"):
        return "FX"
    elif ticker.endswith("-USD"):
        return "Crypto"
    elif ticker.endswith("=F"):
        return "Futures"
    elif ticker in ("HYG", "JNK", "LQD", "VCIT", "BKLN", "SJNK", "SHYG", "AGG", "TLT", "IEF"):
        return "Fixed Income/Credit"
    elif ticker in ("GLD", "SLV", "USO", "GC=F", "SI=F", "CL=F"):
        return "Commodities"
    elif ticker in ("SPY", "QQQ", "IWM", "DIA"):
        return "Equity ETF"
    else:
        return "Equities"


def _compute_risk_budgets(
    strategy_scores: Dict[str, float],
    var_total: float,
) -> List[RiskBudget]:
    """Compute risk budget utilisation per strategy."""
    budgets = []
    for strategy, alloc_pct in _DEFAULT_RISK_BUDGETS.items():
        score = strategy_scores.get(strategy, 0.0)
        # Utilisation: if |score| > 50, fully utilised; else proportional
        utilised = alloc_pct * (abs(score) / 100.0)
        budgets.append(RiskBudget(
            strategy=strategy,
            allocated_pct=round(alloc_pct * 100, 1),
            utilised_pct=round(utilised * 100, 1),
            utilisation_ratio=round(utilised / max(alloc_pct, 0.001), 3),
        ))
    return sorted(budgets, key=lambda b: b.utilised_pct, reverse=True)


def _build_hedges(
    options_signals: Optional[List],
    futures_signals: Optional[List],
) -> List[dict]:
    """Identify protective hedges from options/futures signals."""
    hedges = []
    if options_signals:
        for sig in options_signals:
            if getattr(sig, "strategy", "") in ("protective_put", "iron_condor"):
                hedges.append({
                    "type": sig.strategy,
                    "ticker": sig.ticker,
                    "strikes": getattr(sig, "recommended_strikes", []),
                    "expiry": getattr(sig, "recommended_expiry", ""),
                    "max_loss": getattr(sig, "max_loss", 0),
                    "rationale": getattr(sig, "rationale", ""),
                })
    if futures_signals:
        for sig in futures_signals:
            if getattr(sig, "direction", "") == "short" and getattr(sig, "asset_class", "") == "Equity Index":
                hedges.append({
                    "type": "futures_hedge",
                    "ticker": sig.ticker,
                    "direction": "short",
                    "contracts": getattr(sig, "size_in_contracts", 1),
                    "rationale": f"Equity index hedge: {sig.rationale}",
                })
    return hedges[:5]  # cap at 5 active hedges


def run_master_portfolio(
    config: dict,
    all_strategy_results: Dict[str, Any],
    multi_strategy_result: Optional[Any] = None,
    risk_report: Optional[Any] = None,
) -> MasterPortfolio:
    """
    Crown jewel aggregator.

    all_strategy_results: {strategy_name: result_object}
    multi_strategy_result: MultiStrategyAllocation
    risk_report: DerivativesRiskReport
    """
    notes: List[str] = []
    capital = float(config.get("backtest", {}).get("initial_capital", 100_000))

    # Collect all long/short ideas across strategies
    all_longs: List[dict] = []
    all_shorts: List[dict] = []
    strategy_scores: Dict[str, float] = {}

    for strategy_name, result in all_strategy_results.items():
        longs, shorts = _extract_longs_shorts(strategy_name, result)
        for item in longs:
            item["strategy"] = strategy_name
        for item in shorts:
            item["strategy"] = strategy_name
        all_longs.extend(longs)
        all_shorts.extend(shorts)

        # Extract strategy score
        score = 0.0
        if hasattr(result, "strategy_score"):
            score = float(result.strategy_score)
        elif hasattr(result, "combined_score"):
            score = float(result.combined_score)
        elif isinstance(result, list) and result:
            # List of signals — average conviction
            convictions = [getattr(r, "conviction", 0) for r in result]
            directions = [getattr(r, "direction", "neutral") for r in result]
            if convictions:
                avg_c = float(np.mean(convictions))
                long_bias = sum(1 for d in directions if d in ("long", "strong_long")) - \
                            sum(1 for d in directions if d in ("short", "strong_short"))
                score = float(np.clip(avg_c * np.sign(long_bias + 1e-9), -100, 100))
        strategy_scores[strategy_name] = round(score, 2)

    notes.append(f"Aggregated signals from {len(all_strategy_results)} strategies.")
    notes.append(f"Raw ideas: {len(all_longs)} longs, {len(all_shorts)} shorts before deduplication.")

    # Deduplicate and rank top 10 each
    top_longs = _deduplicate_and_rank(all_longs, "long", top_n=10)
    top_shorts = _deduplicate_and_rank(all_shorts, "short", top_n=10)

    notes.append(f"After deduplication: {len(top_longs)} longs, {len(top_shorts)} shorts.")

    # Liquidity flag (simplified: all tickers in featured_data assumed liquid)
    # Full implementation: check ADV vs intended size

    # Active hedges
    options_sigs = all_strategy_results.get("options")
    futures_sigs = all_strategy_results.get("futures")
    hedges = _build_hedges(options_sigs, futures_sigs)
    notes.append(f"Active hedges: {len(hedges)}")

    # Risk budgets
    risk_budgets = _compute_risk_budgets(strategy_scores, capital * 0.01)

    # VaR from risk engine
    var_total = 0.0
    cvar_total = 0.0
    if risk_report is not None:
        try:
            var_total = float(risk_report.var_1d_99)
            cvar_total = float(risk_report.cvar_1d_99)
        except Exception:
            pass

    # Portfolio-level estimates
    if multi_strategy_result is not None:
        est_sharpe = float(getattr(multi_strategy_result, "estimated_portfolio_sharpe", 0.0))
        port_vol = float(getattr(multi_strategy_result, "estimated_portfolio_vol", 0.10))
        combined_score = float(getattr(multi_strategy_result, "combined_score", 0.0))
    else:
        avg_score = float(np.mean(list(strategy_scores.values()))) if strategy_scores else 0.0
        combined_score = float(np.clip(avg_score, -100, 100))
        est_sharpe = combined_score / 100.0 * 1.5
        port_vol = 0.10

    expected_return = port_vol * est_sharpe

    # Top ideas summary for notes
    if top_longs:
        top_3_long = [f"{i.ticker}({i.combined_conviction:.0f})" for i in top_longs[:3]]
        notes.append(f"Top longs: {', '.join(top_3_long)}")
    if top_shorts:
        top_3_short = [f"{i.ticker}({i.combined_conviction:.0f})" for i in top_shorts[:3]]
        notes.append(f"Top shorts: {', '.join(top_3_short)}")

    fully_utilised = [b.strategy for b in risk_budgets if b.utilisation_ratio > 0.80]
    if fully_utilised:
        notes.append(f"Risk budgets >80% utilised: {fully_utilised}")

    return MasterPortfolio(
        top_longs=top_longs,
        top_shorts=top_shorts,
        active_hedges=hedges,
        risk_budgets=risk_budgets,
        portfolio_var=round(var_total, 2),
        portfolio_cvar=round(cvar_total, 2),
        expected_return_pct=round(expected_return * 100, 4),
        estimated_sharpe=round(est_sharpe, 3),
        combined_score=round(combined_score, 2),
        strategy_count=len(all_strategy_results),
        as_of=datetime.now().strftime("%Y-%m-%d %H:%M"),
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Obsidian daily briefing formatter
# ---------------------------------------------------------------------------

def format_obsidian_briefing(master: MasterPortfolio, config: dict) -> str:
    """Generate the full Obsidian daily briefing note in Markdown."""
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%H:%M")

    score_icon = "🟢" if master.combined_score > 20 else ("🔴" if master.combined_score < -20 else "🟡")
    sharpe_icon = "✅" if master.estimated_sharpe > 0.5 else ("⚠️" if master.estimated_sharpe > 0 else "❌")

    lines = [
        f"# 📊 Master Portfolio Briefing — {today}",
        f"",
        f"> **Generated:** {now} | **Strategies:** {master.strategy_count} | **Score:** {score_icon} {master.combined_score:.1f}/100",
        f"",
        f"#trading #market-intelligence #capitalwithsk #daily-scan #pulse #market-scanner",
        f"",
        f"---",
        f"",
        f"## 🎯 Portfolio Overview",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Combined Signal Score | {score_icon} **{master.combined_score:.1f}** / 100 |",
        f"| Estimated Sharpe | {sharpe_icon} {master.estimated_sharpe:.2f} |",
        f"| Expected Return | {master.expected_return_pct:.2f}% |",
        f"| 1d 99% VaR | ${master.portfolio_var:,.0f} |",
        f"| CVaR (ES) | ${master.portfolio_cvar:,.0f} |",
        f"| Strategies Active | {master.strategy_count} |",
        f"",
        f"---",
        f"",
        f"## 🟢 Top 10 Long Ideas",
        f"",
        f"| Rank | Ticker | Asset Class | Conviction | Signal Count | Est. Return | Strategies |",
        f"|------|--------|-------------|------------|--------------|-------------|------------|",
    ]

    for idea in master.top_longs:
        conv_bar = "█" * int(idea.combined_conviction / 20) + "░" * (5 - int(idea.combined_conviction / 20))
        lines.append(
            f"| {idea.rank} | **{idea.ticker}** | {idea.asset_class} | "
            f"{conv_bar} {idea.combined_conviction:.0f} | {idea.signal_count} | "
            f"+{idea.estimated_return_pct:.1f}% | {', '.join(idea.source_strategies[:2])} |"
        )

    lines += [
        f"",
        f"---",
        f"",
        f"## 🔴 Top 10 Short Ideas",
        f"",
        f"| Rank | Ticker | Asset Class | Conviction | Signal Count | Est. Return | Strategies |",
        f"|------|--------|-------------|------------|--------------|-------------|------------|",
    ]

    for idea in master.top_shorts:
        conv_bar = "█" * int(idea.combined_conviction / 20) + "░" * (5 - int(idea.combined_conviction / 20))
        lines.append(
            f"| {idea.rank} | **{idea.ticker}** | {idea.asset_class} | "
            f"{conv_bar} {idea.combined_conviction:.0f} | {idea.signal_count} | "
            f"-{idea.estimated_return_pct:.1f}% | {', '.join(idea.source_strategies[:2])} |"
        )

    if master.active_hedges:
        lines += [
            f"",
            f"---",
            f"",
            f"## 🛡️ Active Hedges",
            f"",
        ]
        for h in master.active_hedges:
            lines.append(f"- **{h.get('ticker', 'N/A')}** ({h.get('type', 'unknown')}): {h.get('rationale', '')[:100]}")

    lines += [
        f"",
        f"---",
        f"",
        f"## 📊 Risk Budget Utilisation",
        f"",
        f"| Strategy | Allocated | Utilised | Ratio |",
        f"|----------|-----------|----------|-------|",
    ]

    for b in master.risk_budgets:
        util_icon = "🔴" if b.utilisation_ratio > 0.9 else ("🟡" if b.utilisation_ratio > 0.6 else "🟢")
        lines.append(
            f"| {b.strategy} | {b.allocated_pct:.0f}% | {b.utilised_pct:.0f}% | {util_icon} {b.utilisation_ratio:.2f} |"
        )

    lines += [
        f"",
        f"---",
        f"",
        f"## 📝 System Notes",
        f"",
    ]
    for note in master.notes:
        lines.append(f"- {note}")

    lines += [
        f"",
        f"---",
        f"",
        f"*Auto-generated by Trading Intelligence System v5.0 | {now}*",
    ]

    return "\n".join(lines)
