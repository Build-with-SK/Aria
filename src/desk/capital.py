"""
src/desk/capital.py
===================
THE MONEY SHE IS ACTUALLY ALLOWED TO RISK.

The paper account holds about $10,000 and the risk officer falls back to
$100,000 when it cannot read one. Both are fiction, and the fiction has a
cost: at $100k a 5% position is $5,000, the drawdown halt trips at $2,000, and
every number on the screen describes a portfolio that does not exist. Watching
a system "make" $400 teaches nothing about a person who has £100.

So the desk sizes against a CAPITAL BASE he sets — £100 — rather than against
whatever the broker happens to report. Positions get small, the caps bite at
realistic levels, and a 2% drawdown is £2 instead of $2,000.

WHY THIS IS NOT A STEP TOWARD LIVE MONEY
----------------------------------------
It is the opposite. Sizing paper trades like real ones is what makes the paper
record worth reading: a strategy that only looks good because it never had to
fit inside £100 is not a strategy, it is a chart. Everything still goes
through PaperOnlyBroker, and this module cannot and does not touch that.

CURRENCY
--------
He thinks in pounds; Alpaca settles in dollars. The base is stored in HIS
currency and converted for sizing, with the rate and its age reported rather
than assumed. When the rate is unavailable the conversion is refused instead
of guessed — a base silently sized at the wrong rate is worse than one that
says it does not know.
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

#: The account currency Alpaca settles in.
ACCOUNT_CURRENCY = "USD"

#: Below this there is nothing to size: a single share of most things costs
#: more, and every position would round to zero. Refused loudly rather than
#: producing an account that silently never trades.
MIN_BASE = 5.0


def convert_to_account(amount: float, currency: str) -> tuple[float | None, dict]:
    """`amount` of `currency` in account currency, with the rate cited."""
    currency = (currency or ACCOUNT_CURRENCY).upper()
    if currency == ACCOUNT_CURRENCY:
        return float(amount), {"rate": 1.0, "pair": f"{currency}/{ACCOUNT_CURRENCY}",
                               "source": "same currency"}
    try:
        from src.data.currency import convert
        converted = convert(float(amount), currency, ACCOUNT_CURRENCY)
        if converted is None:
            return None, {"error": f"no {currency}/{ACCOUNT_CURRENCY} rate available"}
        rate = converted / float(amount) if amount else None
        return float(converted), {
            "rate": round(rate, 6) if rate else None,
            "pair": f"{currency}/{ACCOUNT_CURRENCY}",
            "source": "src.data.currency",
            "at": datetime.now().isoformat(timespec="seconds"),
        }
    except Exception as e:
        return None, {"error": f"conversion failed: {type(e).__name__}: {e}"}


def sizing_base(account: dict | None = None, cfg: dict | None = None) -> dict:
    """What equity figure the risk officer should size against, and why.

    Returns the number plus the reasoning, because a position size nobody can
    explain is a position size nobody can argue with.
    """
    from src.desk.config import load_config

    cfg = cfg or load_config()
    account = account or {}
    broker_equity = float(account.get("equity") or 0.0)

    base = cfg.get("capital_base")
    currency = str(cfg.get("capital_currency") or ACCOUNT_CURRENCY).upper()

    if not base:
        return {
            "equity": broker_equity,
            "currency": ACCOUNT_CURRENCY,
            "source": "broker",
            "note": ("No capital base set — sizing against whatever the broker "
                     "reports. Set capital_base in data/desk_config.json to "
                     "size against what he would actually risk."),
        }

    base = float(base)
    if base < MIN_BASE:
        return {
            "equity": broker_equity,
            "currency": ACCOUNT_CURRENCY,
            "source": "broker",
            "note": (f"capital_base of {base} {currency} is below the {MIN_BASE} "
                     f"floor — every position would round to zero shares. "
                     f"Ignored; sizing against the broker instead."),
        }

    converted, fx = convert_to_account(base, currency)
    if converted is None:
        return {
            "equity": broker_equity,
            "currency": ACCOUNT_CURRENCY,
            "source": "broker",
            "fx": fx,
            "note": (f"capital_base is {base} {currency} but the rate could "
                     f"not be read ({fx.get('error')}). Refusing to guess a "
                     f"rate; sizing against the broker until it is available."),
        }

    # Never size above what the account can actually cover. If he sets £100
    # against an account holding $10, the account wins — otherwise the caps
    # describe money that is not there.
    effective = min(converted, broker_equity) if broker_equity else converted
    capped = broker_equity and converted > broker_equity

    return {
        "equity": round(effective, 2),
        "currency": ACCOUNT_CURRENCY,
        "declared": {"amount": base, "currency": currency},
        "converted": round(converted, 2),
        "broker_equity": round(broker_equity, 2),
        "source": "capital_base",
        "fx": fx,
        "note": (f"Sizing against {base:.2f} {currency} "
                 f"(~{converted:,.2f} {ACCOUNT_CURRENCY}), not the "
                 f"{broker_equity:,.2f} the paper account reports. "
                 + ("Capped at the broker's equity, which is lower."
                    if capped else
                    "Positions, caps and drawdown limits are all relative to "
                    "this, so the paper record reads like the real thing.")),
    }


def describe(account: dict | None = None) -> dict:
    """The capital base and what it implies, for the UI and for her own
    answers about how much she can risk."""
    from src.desk.config import load_config

    cfg = load_config()
    base = sizing_base(account, cfg)
    equity = base["equity"]
    return {
        **base,
        "implies": {
            "max_per_name": round(equity * float(cfg.get("max_name_pct", 5)) / 100, 2),
            "max_sector": round(equity * float(cfg.get("max_sector_pct", 25)) / 100, 2),
            "heat_cap": round(equity * float(cfg.get("heat_cap_pct", 10)) / 100, 2),
            "drawdown_halt_at": round(
                equity * float(cfg.get("drawdown_halt_pct", 2)) / 100, 2),
            "daily_budget": min(float(cfg.get("daily_notional_budget", 0) or 0),
                                equity) if equity else 0,
        },
    }
