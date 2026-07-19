"""
src/desk/config.py
==================
Desk configuration — data/desk_config.json. auto_execute defaults OFF;
the user arms it explicitly in the UI. Risk caps here are *defaults*;
the risk officer enforces them in code and an LLM can never change them.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
CONFIG_FILE = ROOT / "data" / "desk_config.json"
DESK_DIR = ROOT / "data" / "desk"

DEFAULTS = {
    "auto_execute": False,          # user arms explicitly; paper-only regardless
    "interval_minutes": 30,         # desk cycle cadence
    "focus_tickers": 4,             # how many names get a full debate per cycle
    "debate_rounds": 2,             # bull/bear rebuttal rounds
    "min_conviction": 65,           # judge bar in normal regimes (macro can raise it)
    "max_trades_per_day": 5,
    "daily_notional_budget": 5000.0,   # USD of new exposure per day
    "max_name_pct": 5.0,            # % of equity per name
    "max_sector_pct": 25.0,         # % of equity per sector
    "heat_cap_pct": 10.0,           # max simultaneous open risk (sum stop-distances)
    "max_correlated_positions": 3,  # same sector + same side, incl. open positions
    "drawdown_halt_pct": 2.0,       # halt new entries if paper account down >X% today
    # ── exit engine (PositionManager — rules enforced in code) ──
    "max_hold_days": 10,            # trading days before the time stop fires
    "time_stop_min_r": 0.5,         # time stop only if progress below this many R
    "scale_out_at_target": False,   # True: 50% off at target, trail the rest
    "max_gross_exposure_pct": 100.0,  # flatten worst-first above this — no margin
    "llm_model": "qwen2.5-coder:7b",   # local model for debate prose (optional)
    "ntfy_topic": "",               # e.g. "aria-desk-<random>" → push via ntfy.sh
    "pushover_user": "",
    "pushover_token": "",
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception as e:
            logger.warning(f"desk config unreadable, using defaults: {e}")
    return cfg


def save_config(updates: dict) -> dict:
    cfg = load_config()
    for k, v in updates.items():
        if k in DEFAULTS:
            cfg[k] = v
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def paper_mode_confirmed() -> bool:
    """SAFETY CONTRACT gate 1: ALPACA_PAPER must be exactly "true".
    Anything else — missing, "True", "1", "false" — means auto-exec is
    DISABLED and trades fall back to the manual approval queue.
    There is no override."""
    return os.environ.get("ALPACA_PAPER", "") == "true"
