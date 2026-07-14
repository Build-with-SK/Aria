"""
ARIA Tool: run_scenario
GS-Quant-style macro risk scenario analysis for a ticker.
"""

import asyncio
from tools.ticker import get_ticker_data


SCENARIOS = {
    "rate_shock": {
        "name": "Rate shock (+150bps in 90 days)",
        "description": "Sudden hawkish repricing — Fed signals rates higher for longer",
        "equity_beta_impact": -0.18,   # % impact per unit of beta
        "growth_sector_multiplier": 1.8,
        "defensive_multiplier": 0.4,
        "duration_sensitive": True,
        "notes": "Growth names with high duration take the hardest hit. Financials benefit from spread widening.",
    },
    "equity_crash": {
        "name": "Equity crash (-25% broad market)",
        "description": "Systemic equity selloff — comparable to 2022 or March 2020",
        "equity_beta_impact": -25.0,
        "growth_sector_multiplier": 1.4,
        "defensive_multiplier": 0.5,
        "duration_sensitive": False,
        "notes": "High-beta and leverage amplify losses. Cash and short vol positions outperform.",
    },
    "vol_spike": {
        "name": "Volatility spike (VIX to 40+)",
        "description": "Sudden risk-off event driving vol regime change",
        "equity_beta_impact": -12.0,
        "growth_sector_multiplier": 1.2,
        "defensive_multiplier": 0.6,
        "duration_sensitive": False,
        "notes": "Options pricing explodes. Long gamma positions benefit. Momentum factor reverses.",
    },
    "credit_widening": {
        "name": "Credit spread widening (+200bps HY)",
        "description": "Tightening credit conditions — leveraged balance sheets under pressure",
        "equity_beta_impact": -8.0,
        "growth_sector_multiplier": 1.1,
        "defensive_multiplier": 0.7,
        "duration_sensitive": False,
        "notes": "High-debt companies suffer most. Investment-grade moats provide shelter.",
    },
    "soft_landing": {
        "name": "Soft landing confirmed",
        "description": "Inflation falls, growth holds — the Goldilocks scenario",
        "equity_beta_impact": +12.0,
        "growth_sector_multiplier": 1.3,
        "defensive_multiplier": 0.7,
        "duration_sensitive": False,
        "notes": "Growth and cyclicals outperform. Defensives and cash lag. Credit spreads tighten.",
    },
    "stagflation": {
        "name": "Stagflation (high inflation + recession)",
        "description": "1970s redux — no good policy options, real assets outperform",
        "equity_beta_impact": -15.0,
        "growth_sector_multiplier": 1.6,
        "defensive_multiplier": 0.5,
        "duration_sensitive": True,
        "notes": "Commodities, real estate, TIPS outperform. Duration assets crushed. Value > Growth.",
    },
    "dollar_surge": {
        "name": "Dollar surge (DXY +10%)",
        "description": "USD safe-haven demand — emerging markets and exporters hurt",
        "equity_beta_impact": -5.0,
        "growth_sector_multiplier": 0.8,
        "defensive_multiplier": 0.9,
        "duration_sensitive": False,
        "notes": "Multinationals with overseas revenue hurt. Domestic-focused names relatively insulated.",
    },
}


SECTOR_SENSITIVITY = {
    "Technology": {"rate_sensitive": True, "growth": True, "cyclical": True},
    "Consumer Cyclical": {"rate_sensitive": False, "growth": True, "cyclical": True},
    "Financial Services": {"rate_sensitive": True, "growth": False, "cyclical": True},
    "Healthcare": {"rate_sensitive": False, "growth": False, "cyclical": False},
    "Consumer Defensive": {"rate_sensitive": False, "growth": False, "cyclical": False},
    "Utilities": {"rate_sensitive": True, "growth": False, "cyclical": False},
    "Energy": {"rate_sensitive": False, "growth": False, "cyclical": True},
    "Communication Services": {"rate_sensitive": True, "growth": True, "cyclical": True},
    "Industrials": {"rate_sensitive": False, "growth": False, "cyclical": True},
    "Basic Materials": {"rate_sensitive": False, "growth": False, "cyclical": True},
    "Real Estate": {"rate_sensitive": True, "growth": False, "cyclical": False},
}


async def run_scenario(ticker: str, scenario_name: str) -> dict:
    """Estimate scenario impact on a ticker based on beta, sector, and scenario parameters."""
    data = await get_ticker_data(ticker, period="1y")
    if "error" in data:
        return {"error": data["error"], "ticker": ticker}

    fund = data.get("fundamentals", {})
    beta = fund.get("beta") or 1.0
    sector = fund.get("sector", "Unknown")
    current_price = data["price"]["current"]

    sector_profile = SECTOR_SENSITIVITY.get(sector, {"rate_sensitive": False, "growth": False, "cyclical": True})

    scenarios_to_run = (
        list(SCENARIOS.items()) if scenario_name == "all"
        else [(scenario_name, SCENARIOS.get(scenario_name))]
    )

    results = {}
    for s_name, s_params in scenarios_to_run:
        if s_params is None:
            results[s_name] = {"error": f"Unknown scenario: {s_name}"}
            continue

        base_impact = s_params["equity_beta_impact"] * beta

        # Sector multiplier
        if sector_profile.get("growth") and s_params.get("duration_sensitive"):
            multiplier = s_params["growth_sector_multiplier"]
        elif not sector_profile.get("cyclical"):
            multiplier = s_params["defensive_multiplier"]
        else:
            multiplier = 1.0

        # Rate sensitivity overlay
        if s_name == "rate_shock" and sector_profile.get("rate_sensitive"):
            multiplier *= 1.3

        adjusted_impact = base_impact * multiplier
        estimated_price = round(current_price * (1 + adjusted_impact / 100), 2)

        severity = (
            "severe" if abs(adjusted_impact) > 20
            else "significant" if abs(adjusted_impact) > 10
            else "moderate" if abs(adjusted_impact) > 5
            else "mild"
        )

        results[s_name] = {
            "scenario": s_params["name"],
            "description": s_params["description"],
            "estimated_impact_pct": round(adjusted_impact, 2),
            "estimated_price": estimated_price,
            "current_price": current_price,
            "severity": severity,
            "methodology": {
                "beta_used": beta,
                "base_impact": round(base_impact, 2),
                "sector_multiplier": round(multiplier, 2),
                "sector": sector,
            },
            "notes": s_params["notes"],
        }

    return {
        "ticker": ticker,
        "scenarios": results,
        "disclaimer": (
            "These are illustrative estimates based on beta and sector sensitivity. "
            "Actual outcomes depend on company-specific factors, correlation dynamics, "
            "and the precise path of the scenario. Use for risk awareness, not precision forecasting."
        ),
    }
