"""
src/v5/registry.py
==================
The multi-strategy research engine's index: every module is independently
callable by name, and no module can take the platform down with it.

Isolation contract — if a module raises, times out, or returns something that
is not a valid ModuleReport, the registry converts that into an abstention
(`insufficient`). A broken engine must never silently become a confident one,
and it must never block the other engines from reporting.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Optional

from src.v5.contract import ModuleReport, insufficient, validate

logger = logging.getLogger(__name__)

FAMILIES = [
    "fundamental",      # Value, Growth, Quality, Factor, Earnings
    "price",            # Momentum, Trend, Mean Reversion, Technical, RS, Breadth, Rotation
    "quant",            # StatArb, Cointegration, PCA, Clustering, HMM, Kalman, Bayes, TS
    "macro",            # Regime, Global Macro, Yield Curve, Credit, FX, Carry, Commodity,
                        # Seasonality, Cross-Asset, Liquidity
    "volatility",       # Vol trading, Options surface
    "machine",          # ML, DL, RL (research-only), NLP, Event, News, Alt-data
    "behavioural",      # Crowding, sentiment extremes, bias detection
]


@dataclass
class ModuleSpec:
    name: str
    family: str
    description: str
    fn: Callable[[str], ModuleReport]
    horizon_days: int = 21
    research_only: bool = False     # never contributes to a live recommendation

    def to_dict(self) -> dict:
        return {"name": self.name, "family": self.family,
                "description": self.description,
                "horizon_days": self.horizon_days,
                "research_only": self.research_only}


REGISTRY: dict[str, ModuleSpec] = {}


def module(name: str, family: str, description: str,
           horizon_days: int = 21, research_only: bool = False):
    """Register a research module. The wrapped function takes a ticker and
    returns a ModuleReport."""
    if family not in FAMILIES:
        raise ValueError(f"unknown family '{family}' for module '{name}'")

    def deco(fn: Callable[[str], ModuleReport]):
        if name in REGISTRY:
            logger.warning(f"v5: module '{name}' re-registered (reload?)")
        REGISTRY[name] = ModuleSpec(name=name, family=family, description=description,
                                    fn=fn, horizon_days=horizon_days,
                                    research_only=research_only)
        return fn
    return deco


_LOADED = False


def load_modules() -> int:
    """Import the module package so the decorators fire. Idempotent."""
    global _LOADED
    if not _LOADED:
        import src.v5.modules  # noqa: F401  (side-effect: registration)
        _LOADED = True
    return len(REGISTRY)


def list_modules(family: Optional[str] = None) -> list[dict]:
    load_modules()
    specs = sorted(REGISTRY.values(), key=lambda s: (s.family, s.name))
    return [s.to_dict() for s in specs if family is None or s.family == family]


def run(name: str, ticker: str, *, strict: bool = False) -> ModuleReport:
    """Run one module in isolation. Never raises."""
    load_modules()
    spec = REGISTRY.get(name)
    if spec is None:
        return insufficient(name, "unknown", ticker, f"no module named '{name}' is registered")

    t0 = time.time()
    try:
        report = spec.fn(ticker)
    except Exception as e:                      # isolation boundary
        logger.warning(f"v5 module '{name}' raised on {ticker}: {e}")
        return insufficient(name, spec.family, ticker,
                            f"module raised {type(e).__name__}", error=str(e)[:300])

    if not isinstance(report, ModuleReport):
        return insufficient(name, spec.family, ticker,
                            f"module returned {type(report).__name__}, not a ModuleReport")

    report.module, report.family, report.ticker = name, spec.family, ticker
    if not report.horizon_days:
        report.horizon_days = spec.horizon_days

    problems = validate(report)
    if problems:
        logger.warning(f"v5 contract violation: {problems}")
        if strict:
            raise AssertionError(problems)
        return insufficient(name, spec.family, ticker,
                            "output violated the module contract: " + "; ".join(problems[:3]))

    report.__dict__["_elapsed_ms"] = int((time.time() - t0) * 1000)
    return report


def run_all(ticker: str, *, families: Optional[list[str]] = None,
            names: Optional[list[str]] = None,
            include_research_only: bool = False,
            max_workers: int = 8) -> list[ModuleReport]:
    """Run the research engine across modules, in parallel (they are network- and
    disk-bound). Order of the returned list is stable: family, then name."""
    load_modules()
    specs = [s for s in REGISTRY.values()
             if (families is None or s.family in families)
             and (names is None or s.name in names)
             and (include_research_only or not s.research_only)]
    specs.sort(key=lambda s: (s.family, s.name))

    out: dict[str, ModuleReport] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(run, s.name, ticker): s.name for s in specs}
        for fut in as_completed(futures):
            nm = futures[fut]
            try:
                out[nm] = fut.result()
            except Exception as e:              # belt and braces; run() already catches
                spec = REGISTRY[nm]
                out[nm] = insufficient(nm, spec.family, ticker,
                                       "executor failure", error=str(e)[:300])
    return [out[s.name] for s in specs if s.name in out]
