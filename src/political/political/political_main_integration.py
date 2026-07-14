"""
political_main_integration.py
==============================
Non-breaking integration bridge for the Political Intelligence Layer.

How to use in your existing main.py:

    # Add this import near the top:
    from src.political.political_main_integration import run_political_layer

    # Add this call inside your main pipeline, after your existing signals run:
    run_political_layer(external_signals=your_signal_dict)

    # Or with no external signals (will use neutral defaults):
    run_political_layer()

This module is safe to import even if:
  - The political CSV does not exist (creates empty watchlist)
  - No API keys are configured (uses placeholders)
  - The political data folder does not exist (creates it)
  - The existing signal_engine.py has not been modified

Trading Intelligence System — Phase 4
Research and educational purposes only.
"""

import json
import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _load_config() -> dict:
    """Load political_sources.yaml if available. Returns empty dict on failure."""
    try:
        import yaml
        config_path = BASE_DIR / "configs" / "political_sources.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except ImportError:
        logger.info("PyYAML not installed — using default config. "
                    "Run: pip install pyyaml")
    except Exception as e:
        logger.warning(f"Could not load political_sources.yaml: {e}")
    return {}


def _ensure_data_dirs() -> None:
    """Create data directories if they don't exist."""
    dirs = [
        BASE_DIR / "data" / "political" / "raw",
        BASE_DIR / "data" / "political" / "processed",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def _write_empty_outputs() -> None:
    """Write empty-but-valid JSON files so the system always has output."""
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    empty_watchlist = {
        "generated_at": ts,
        "record_count": 0,
        "data_policy": (
            "Research only. No political trade data loaded. "
            "Add data to data/political/manual_political_trades.csv to populate."
        ),
        "watchlist": [],
    }
    empty_signals = {
        "generated_at": ts,
        "count": 0,
        "signals": [],
    }
    empty_candidates = {
        "generated_at": ts,
        "count": 0,
        "important_disclaimer": (
            "Research output only. No automatic execution. "
            "Political signals are 10% of the final score."
        ),
        "trade_candidates": [],
    }

    files = [
        (BASE_DIR / "data" / "political" / "watchlist.json", empty_watchlist),
        (BASE_DIR / "data" / "political" / "political_signals.json", empty_signals),
        (BASE_DIR / "data" / "trade_candidates.json", empty_candidates),
    ]

    for path, payload in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            logger.info(f"Created empty output: {path.name}")


def _extract_signal_dict_from_existing_engine() -> Optional[Dict[str, dict]]:
    """
    Attempt to load signal scores from your existing signal engine output.
    Returns None if not available (safe fallback to neutral defaults).

    Extend this function to integrate with your actual signal_engine.py.
    """
    # Try common output file locations from existing system
    candidate_paths = [
        BASE_DIR / "data" / "signals.json",
        BASE_DIR / "data" / "latest_signals.json",
        BASE_DIR / "data" / "signal_output.json",
        BASE_DIR / "data" / "outputs" / "signals.json",
    ]

    for path in candidate_paths:
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)

                # Handle common formats
                if isinstance(data, dict):
                    # Format: {"NVDA": {"technical_score": 68, ...}}
                    if all(isinstance(v, dict) for v in data.values()):
                        logger.info(f"Loaded external signals from {path.name}")
                        return data

                    # Format: {"signals": [...]} or {"data": [...]}
                    for key in ("signals", "data", "results", "tickers"):
                        if key in data and isinstance(data[key], list):
                            result = {}
                            for item in data[key]:
                                ticker = item.get("ticker", item.get("symbol", ""))
                                if ticker:
                                    result[ticker] = item
                            if result:
                                logger.info(f"Loaded external signals from {path.name} (list format)")
                                return result

                elif isinstance(data, list):
                    result = {}
                    for item in data:
                        ticker = item.get("ticker", item.get("symbol", ""))
                        if ticker:
                            result[ticker] = item
                    if result:
                        logger.info(f"Loaded external signals from {path.name} (list format)")
                        return result

            except Exception as e:
                logger.debug(f"Could not parse {path.name}: {e}")

    return None


def run_political_layer(
    external_signals: Optional[Dict[str, dict]] = None,
    verbose: bool = True,
) -> List[dict]:
    """
    Run the Political Intelligence Layer.

    This is the main entry point for integration with your existing main.py.

    Args:
        external_signals: Optional {ticker: {technical_score, ...}} from your signal engine.
                          If None, attempts to auto-load from signals.json.
                          If still None, uses neutral defaults (50) for all scores.
        verbose: Print confirmation matrix to console (default True)

    Returns:
        List of trade candidate dicts (also written to data/trade_candidates.json)
        Returns empty list on any error — NEVER crashes main.py.
    """
    try:
        _ensure_data_dirs()
        _write_empty_outputs()

        # Try to load external signals if not provided
        if external_signals is None:
            external_signals = _extract_signal_dict_from_existing_engine()
            if external_signals:
                logger.info(f"Auto-loaded {len(external_signals)} tickers from existing signal engine")
            else:
                logger.info("No external signals found — using neutral defaults (50) for all confirmation scores")

        # Load config
        config = _load_config()

        # Run the pipeline
        from src.political import run_political_pipeline
        candidates = run_political_pipeline(
            external_signals=external_signals,
            config=config,
            save=True,
            verbose=verbose,
        )

        # Return as dicts for easy consumption by existing code
        return [c.to_dict() for c in candidates]

    except Exception as e:
        logger.error(f"Political Intelligence Layer failed: {e}")
        logger.debug(traceback.format_exc())
        logger.warning(
            "Political layer error — existing system will continue unaffected. "
            "Check logs for details."
        )
        return []


def get_political_watchlist() -> List[dict]:
    """Load and return the current political watchlist from watchlist.json."""
    path = BASE_DIR / "data" / "political" / "watchlist.json"
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("watchlist", [])
    except Exception as e:
        logger.error(f"Could not load watchlist.json: {e}")
        return []


def get_trade_candidates(status_filter: Optional[str] = None) -> List[dict]:
    """
    Load trade candidates from trade_candidates.json.

    Args:
        status_filter: Optional status to filter by.
                       Values: "Trade Candidate", "Research Candidate",
                               "Watchlist Only", "Avoid"
    """
    path = BASE_DIR / "data" / "trade_candidates.json"
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        candidates = data.get("trade_candidates", [])
        if status_filter:
            candidates = [c for c in candidates if c.get("trade_status") == status_filter]
        return candidates
    except Exception as e:
        logger.error(f"Could not load trade_candidates.json: {e}")
        return []
