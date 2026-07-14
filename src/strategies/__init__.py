# src/strategies/__init__.py
# Trading Intelligence System — Strategy Modules
# Phase 5: Multi-Strategy, Multi-Asset Platform

from .equity_long_short import run_equity_long_short, LongShortPortfolio
from .relative_value import run_relative_value, PairsResult
from .market_neutral import run_market_neutral, MarketNeutralPortfolio
from .global_macro import run_global_macro, MacroPositioning
from .event_driven import run_event_driven, EventCalendar
from .distressed import run_distressed, DistressedScreen
from .convertible_arb import run_convertible_arb, ConvertiblePosition
from .quant_systematic import run_quant_systematic, FactorPortfolio
from .credit_long_short import run_credit_long_short, CreditPositioning
from .options_engine import run_options_engine, OptionsSignal
from .futures_engine import run_futures_engine, FuturesSignal
from .crypto_engine import run_crypto_engine, CryptoSignal
from .forex_engine import run_forex_engine, ForexSignal
from .derivatives_risk import run_derivatives_risk, DerivativesRiskReport
from .multi_strategy import run_multi_strategy, MultiStrategyAllocation
from .master_portfolio import run_master_portfolio, MasterPortfolio

__all__ = [
    "run_equity_long_short", "LongShortPortfolio",
    "run_relative_value", "PairsResult",
    "run_market_neutral", "MarketNeutralPortfolio",
    "run_global_macro", "MacroPositioning",
    "run_event_driven", "EventCalendar",
    "run_distressed", "DistressedScreen",
    "run_convertible_arb", "ConvertiblePosition",
    "run_quant_systematic", "FactorPortfolio",
    "run_credit_long_short", "CreditPositioning",
    "run_options_engine", "OptionsSignal",
    "run_futures_engine", "FuturesSignal",
    "run_crypto_engine", "CryptoSignal",
    "run_forex_engine", "ForexSignal",
    "run_derivatives_risk", "DerivativesRiskReport",
    "run_multi_strategy", "MultiStrategyAllocation",
    "run_master_portfolio", "MasterPortfolio",
]
