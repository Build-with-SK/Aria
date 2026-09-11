"""
src/desk/config.py
==================
Desk configuration — data/desk_config.json. auto_execute defaults ON for
the PAPER account only — the safety contract (env gate + broker paper flag)
still rules, and the UI toggle remains as a kill switch. Risk caps here are
*defaults*; the risk officer enforces them in code and an LLM can never
change them.
"""
from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
CONFIG_FILE = ROOT / "data" / "desk_config.json"
DESK_DIR = ROOT / "data" / "desk"

DEFAULTS = {
    # OFF by default. The paper-only env gate and the UI kill switch both still
    # apply, but a public checkout must not begin placing orders — even paper
    # ones — because someone ran the desk daemon before reading the safety
    # contract. Turn it on deliberately in data/desk_config.json or the UI.
    "auto_execute": False,
    # Risk tolerance — see src/risk/profiles.py. The named profile sets the
    # caps below; anything explicitly set in this file still wins, and the
    # platform ceiling in HARD_LIMITS wins over both.
    "risk_profile": "MODERATE",
    "interval_minutes": 30,         # hunt cycle cadence (market-hours aware)
    "management_tick_minutes": 5,   # exit engine / bracket healing tick, 24/7
    "focus_tickers": 4,             # how many names get a full debate per cycle
    "debate_rounds": 2,             # bull/bear rebuttal rounds
    "min_conviction": 65,           # judge bar in normal regimes (macro can raise it)
    # What he would actually put at risk, in HIS currency. The paper account
    # reports ~$10k and the risk officer falls back to $100k; both are fiction,
    # and sizing against fiction makes every number on the screen describe a
    # portfolio that does not exist. 0 means "use whatever the broker says".
    "capital_base": 100.0,
    "capital_currency": "GBP",
    "max_trades_per_day": 10,
    "daily_notional_budget": 20000.0,  # USD of new exposure per day
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
    # ── REFLEX fast lane (v4 — the 2-5s path) ──
    "reflex_enabled": True,          # separate kill switch (auto_execute still rules)
    "reflex_poll_seconds": 3,        # armed-ticker quote poll cadence
    "reflex_playbook_expiry_days": 2,   # armed playbooks expire after N trading days
    "reflex_min_prob": 0.70,         # confidence gate: signal prob must clear this
    "reflex_arm_conviction_gap": 10, # arm a playbook if conviction within N under the bar
    "reflex_signal_spike": 40.0,     # |composite| crossing this arms a signal playbook
    "reflex_veto_max_tokens": 30,    # Haiku veto answer budget
    "reflex_on_llm_fail": "skip",    # veto unreachable → "skip" (safe) | "proceed"
    # ── Fable teacher/reviewer (Fable grades closed trades, writes lessons) ──
    # ON by default now. It was opt-in because it cost API calls; on the
    # self-hosted brain it costs a local inference, and the thing it buys is
    # the only mechanism by which a finished trade changes a future one.
    # A desk that trades and never grades itself is not learning, it is
    # repeating.
    "teacher_enabled": True,
    "teacher_model": "claude-fable-5",   # only used where policy allows a vendor
    "teacher_daily_cap": 40,         # max Fable reviews/day (bill guard)
    "teacher_recall_lessons": 3,     # lessons injected into a future debate
    "llm_model": "qwen2.5:7b-instruct-q4_K_M",   # local model for debate prose (optional)
    "ntfy_topic": "",               # e.g. "aria-desk-<random>" → push via ntfy.sh
    "pushover_user": "",
    "pushover_token": "",
}


def _reject_non_finite(value):
    """json.loads accepts the bare literals NaN, Infinity and -Infinity. Every
    risk gate downstream is a `>` comparison, and NaN > x is False for every x —
    so a single NaN in this file turns the drawdown circuit-breaker, the heat
    check and every name cap off at once, while each check still runs, still
    reports False, and the UI still shows green. A cap that silently holds
    nothing is worse than no cap, because it is trusted."""
    raise ValueError(f"non-finite number in desk config: {value}")


def _clean(key, value):
    """Keep a config value only if it has the same type as its default and is a
    real number. Wrong types are as dangerous as NaN here: a string where a
    float belongs raises TypeError inside the comparison, and the callers that
    catch that exception are the ones this session had to fix for failing open.

    Returns (ok, value). bool is checked before int/float on purpose — bool is a
    subclass of int in Python, so `isinstance(True, int)` is True and a stray
    `true` would otherwise sail into a numeric cap as the number 1.
    """
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return (isinstance(value, bool), value)
    if isinstance(default, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return (False, value)
        if not math.isfinite(value):
            return (False, value)
        return (True, value)
    if isinstance(default, str):
        return (isinstance(value, str), value)
    return (type(value) is type(default), value)


def _sanitise(raw: dict, source: str) -> dict:
    """Drop anything unknown, mistyped or non-finite, and say which and why.
    Dropped keys fall back to their default rather than to nothing — a missing
    cap would be read as "no cap" by the same `>` comparisons."""
    clean = {}
    for k, v in (raw or {}).items():
        if k not in DEFAULTS:
            logger.warning(f"desk config: ignoring unknown key {k!r} ({source})")
            continue
        ok, v = _clean(k, v)
        if not ok:
            logger.error(f"desk config: REJECTED {k}={v!r} ({source}) — "
                         f"expected {type(DEFAULTS[k]).__name__}, finite; "
                         f"using default {DEFAULTS[k]!r}")
            continue
        clean[k] = v
    return clean


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"),
                             parse_constant=_reject_non_finite)
            if not isinstance(raw, dict):
                raise ValueError(f"expected an object, got {type(raw).__name__}")
            cfg.update(_sanitise(raw, "on disk"))
        except Exception as e:
            logger.warning(f"desk config unreadable, using defaults: {e}")
    return cfg


def save_config(updates: dict) -> dict:
    """Validates on the way in as well as on the way out. PATCH /api/desk/config
    reaches this directly, so a request body is untrusted input — the key-
    membership check that used to be the only filter let any value through for
    a known key."""
    cfg = load_config()
    cfg.update(_sanitise(updates, "incoming update"))
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    # allow_nan=False so a non-finite value can never be written even if one
    # reaches this line by a path the sanitiser does not cover.
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, allow_nan=False),
                           encoding="utf-8")
    return cfg


def paper_mode_confirmed() -> bool:
    """SAFETY CONTRACT gate 1: ALPACA_PAPER must be exactly "true".
    Anything else — missing, "True", "1", "false" — means auto-exec is
    DISABLED and trades fall back to the manual approval queue.
    There is no override."""
    return os.environ.get("ALPACA_PAPER", "") == "true"
