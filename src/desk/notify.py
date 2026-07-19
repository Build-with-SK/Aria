"""
src/desk/notify.py
==================
One brief push per fill — ntfy and/or Pushover, whichever is configured.
The message IS the whole story: ticker, qty, price, stop, exit plan.
The full thesis/debate lives in the UI and logs, never in the push.

Exact formats (Module A4 — do not embellish):
  ▲ BUY 12 AAPL @ 312.05 | SL 303.71 | sell at 325.97 or on signal flip
  ▼ SOLD 12 AAPL @ 318.40 | P&L +$76.20 (+2.0%) | reason: target hit
"""
from __future__ import annotations

import logging
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


def fmt_entry(side: str, qty: float, ticker: str, price: float,
              stop: float | None, target: float | None) -> str:
    arrow = "▲ BUY" if side in ("buy", "long") else "▼ SELL"
    msg = f"{arrow} {qty:g} {ticker} @ {price:.2f}"
    msg += f" | SL {stop:.2f}" if stop else " | SL none"
    if target:
        exit_verb = "sell at" if side in ("buy", "long") else "cover at"
        msg += f" | {exit_verb} {target:.2f} or on signal flip"
    else:
        msg += " | exit on signal flip"
    return msg


def fmt_exit(entry_side: str, qty: float, ticker: str, price: float,
             pnl: float, pnl_pct: float, reason: str) -> str:
    arrow = "▼ SOLD" if entry_side in ("buy", "long") else "▲ COVERED"
    return (f"{arrow} {qty:g} {ticker} @ {price:.2f} "
            f"| P&L {pnl:+.2f} ({pnl_pct:+.1f}%) | reason: {reason}")


def push(text: str, config: dict | None = None):
    """Send one brief push. Silent no-op when nothing is configured."""
    if config is None:
        from src.desk.config import load_config
        config = load_config()
    topic = (config.get("ntfy_topic") or "").strip()
    if topic:
        try:
            req = urllib.request.Request(
                f"https://ntfy.sh/{urllib.parse.quote(topic)}",
                data=text.encode("utf-8"),
                headers={"Title": "ARIA desk".encode("ascii").decode(),
                         "Tags": "chart_with_upwards_trend"})
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            logger.warning(f"ntfy push failed: {e}")
    user, token = config.get("pushover_user", ""), config.get("pushover_token", "")
    if user and token:
        try:
            data = urllib.parse.urlencode({
                "token": token, "user": user,
                "title": "ARIA desk", "message": text}).encode()
            urllib.request.urlopen(
                urllib.request.Request("https://api.pushover.net/1/messages.json",
                                       data=data, method="POST"), timeout=10)
        except Exception as e:
            logger.warning(f"pushover push failed: {e}")
