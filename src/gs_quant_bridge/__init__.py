"""
src.gs_quant_bridge — Standalone GS-Quant inspired analytics.
Risk scenario engine and Black-Scholes options analytics. No GS API required.
"""

from .scenario_engine import ScenarioEngine, ScenarioResult
from .options_analytics import BlackScholes, Greeks, VolSurface, VolSurfaceBuilder, quick_options_summary
from .strategies import StrategyEngine, StrategyResult, Leg, AVAILABLE_STRATEGIES

__all__ = [
    "ScenarioEngine",
    "ScenarioResult",
    "BlackScholes",
    "Greeks",
    "VolSurface",
    "VolSurfaceBuilder",
    "quick_options_summary",
]
