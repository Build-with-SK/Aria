"""
src/data/fx_monitor.py
======================
GBP/INR remittance monitor for a UK↔India money-mover.

Frames the rate around what actually matters to the user:
  • Pound strengthening vs rupee (GBPINR up)  → better to send  UK → India
  • Rupee strengthening vs pound (GBPINR down) → better to send  India → UK

Raises an alert when the rate moves meaningfully from a rolling baseline, or
crosses a user-set target. State + alerts persist to data/fx_state.json and
data/alerts.json (the same feed the dashboard shows).
"""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
STATE_FILE = ROOT / "data" / "fx_state.json"
ALERTS_FILE = ROOT / "data" / "alerts.json"

PAIR = "GBPINR=X"
MOVE_ALERT_PCT = 1.0     # alert if rate moves >=1% vs the rolling baseline
BASELINE_ALPHA = 0.02    # slow EMA baseline (~ multi-day)


def _fetch_rate() -> float | None:
    """Latest GBP/INR via Yahoo quote (no key needed)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{PAIR}?range=5d&interval=1d"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        res = data["chart"]["result"][0]
        closes = [c for c in res["indicators"]["quote"][0]["close"] if c]
        return float(closes[-1]) if closes else None
    except Exception as e:
        logger.warning(f"FX rate fetch failed: {e}")
        return None


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"baseline": None, "last": None, "target_high": None, "target_low": None,
            "history": []}


def _save_state(s: dict):
    STATE_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")


def _push_alert(severity: str, title: str, message: str):
    """Append to the dashboard alerts feed (data/alerts.json)."""
    alerts = []
    if ALERTS_FILE.exists():
        try:
            alerts = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
            if not isinstance(alerts, list):
                alerts = []
        except Exception:
            alerts = []
    alerts.insert(0, {"severity": severity, "category": "FX_REMITTANCE",
                      "title": title, "message": message,
                      "timestamp": datetime.now().isoformat()})
    ALERTS_FILE.write_text(json.dumps(alerts[:100], indent=2), encoding="utf-8")


def set_targets(high: float | None = None, low: float | None = None) -> dict:
    """Set alert-me-at levels: high = alert when GBPINR ≥ high (great to send UK→India),
    low = alert when GBPINR ≤ low (great to send India→UK)."""
    s = _load_state()
    if high is not None:
        s["target_high"] = high
    if low is not None:
        s["target_low"] = low
    _save_state(s)
    return s


def check(force_alert: bool = False) -> dict:
    """Fetch the rate, update the baseline, raise remittance alerts. Returns a status dict."""
    rate = _fetch_rate()
    s = _load_state()
    if rate is None:
        return {**s, "error": "rate unavailable", "rate": s.get("last")}

    prev = s.get("last")
    base = s.get("baseline") or rate
    base = base * (1 - BASELINE_ALPHA) + rate * BASELINE_ALPHA   # EMA
    move_pct = round((rate / base - 1) * 100, 2)

    direction = ("pound_strong" if move_pct >= MOVE_ALERT_PCT else
                 "rupee_strong" if move_pct <= -MOVE_ALERT_PCT else "neutral")

    alerts_raised = []
    # movement alerts
    if direction == "pound_strong":
        msg = (f"£1 = ₹{rate:.2f} — the pound is {move_pct:+.1f}% above its recent "
               f"average. Good window to send money UK → India (you'll get more rupees).")
        _push_alert("INFO", "Pound strong vs rupee — good to send UK→India", msg)
        alerts_raised.append(msg)
    elif direction == "rupee_strong":
        msg = (f"£1 = ₹{rate:.2f} — the rupee is {abs(move_pct):.1f}% stronger than its "
               f"recent average. Good window to send money India → UK (your rupees buy more pounds).")
        _push_alert("INFO", "Rupee strong vs pound — good to send India→UK", msg)
        alerts_raised.append(msg)

    # target-level alerts
    if s.get("target_high") and rate >= s["target_high"]:
        m = f"£1 = ₹{rate:.2f} hit your target of ₹{s['target_high']:.2f} — send UK→India now."
        _push_alert("WARNING", "GBP/INR hit your UK→India target", m); alerts_raised.append(m)
    if s.get("target_low") and rate <= s["target_low"]:
        m = f"£1 = ₹{rate:.2f} dropped to your target of ₹{s['target_low']:.2f} — send India→UK now."
        _push_alert("WARNING", "GBP/INR hit your India→UK target", m); alerts_raised.append(m)

    hist = (s.get("history") or [])[-59:] + [{"at": datetime.now().isoformat(), "rate": round(rate, 3)}]
    s.update({"baseline": round(base, 4), "last": round(rate, 3), "history": hist,
              "checked_at": datetime.now().isoformat()})
    _save_state(s)

    return {
        "pair": "GBP/INR", "rate": round(rate, 3),
        "inverse": round(1 / rate, 5), "unit": "£1 = ₹" + f"{rate:.2f}",
        "baseline": round(base, 3), "move_pct": move_pct, "direction": direction,
        "prev": prev, "delta": round(rate - prev, 3) if prev else None,
        "send_advice": ("Favourable to send UK → India" if direction == "pound_strong"
                        else "Favourable to send India → UK" if direction == "rupee_strong"
                        else "Rate near its recent average — no strong edge either way"),
        "target_high": s.get("target_high"), "target_low": s.get("target_low"),
        "history": s["history"], "alerts_raised": alerts_raised,
        "checked_at": s["checked_at"],
    }


def status() -> dict:
    s = _load_state()
    return {"pair": "GBP/INR", "rate": s.get("last"), "baseline": s.get("baseline"),
            "target_high": s.get("target_high"), "target_low": s.get("target_low"),
            "history": s.get("history", []), "checked_at": s.get("checked_at")}
