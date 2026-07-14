"""
notifications.py
================
Phase 4 — Alert notification system.

Sends alerts via:
  1. Email (SMTP — works with Gmail, Outlook, any SMTP server)
  2. Telegram Bot (free, instant, no rate limits for personal use)

Setup instructions are printed the first time you run.
All credentials are stored in configs/notifications.yaml (gitignored).
Never hardcode credentials in code.

Usage:
  from src.notifications.notifications import send_alerts
  send_alerts(alerts, config)
"""

from __future__ import annotations

import logging
import smtplib
import json
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Optional
import urllib.request
import urllib.parse

logger = logging.getLogger(__name__)

NOTIF_CONFIG_PATH = Path("configs/notifications.yaml")


# ===========================================================================
# Config loader
# ===========================================================================

def load_notification_config() -> dict:
    """
    Load notification config from configs/notifications.yaml.
    Returns empty dict if file doesn't exist (notifications disabled).
    """
    if not NOTIF_CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(NOTIF_CONFIG_PATH.read_text()) or {}
    except Exception as e:
        logger.warning(f"Could not load notification config: {e}")
        return {}


def create_default_notification_config() -> None:
    """
    Create a template notifications.yaml if it doesn't exist.
    User fills in their credentials.
    """
    if NOTIF_CONFIG_PATH.exists():
        return

    template = """# Trading Intelligence System — Notification Config
# Fill in your credentials below. This file is gitignored.

email:
  enabled: false
  smtp_host: "smtp.gmail.com"
  smtp_port: 587
  sender_email: "your_email@gmail.com"
  sender_password: "your_app_password"   # Gmail: use App Password, not main password
  recipient_email: "your_email@gmail.com"
  send_on_severity: ["CRITICAL", "WARNING"]   # Which severities to email

telegram:
  enabled: false
  bot_token: "your_bot_token"     # Get from @BotFather on Telegram
  chat_id: "your_chat_id"         # Get from @userinfobot on Telegram
  send_on_severity: ["CRITICAL", "WARNING", "INFO"]

# Minimum alerts to send (avoid spam on quiet days)
min_alerts_to_notify: 1
"""
    NOTIF_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    NOTIF_CONFIG_PATH.write_text(template)
    logger.info(f"Created notification config template at {NOTIF_CONFIG_PATH}")
    logger.info("Edit configs/notifications.yaml to enable email/Telegram alerts")


# ===========================================================================
# Email
# ===========================================================================

def _format_email_html(alerts: list, report_summary: dict) -> str:
    """Format alerts as an HTML email."""
    today = str(date.today())
    regime = report_summary.get("market_regime", "Unknown")
    bull   = report_summary.get("regime_stats", {}).get("bull", 0)
    bear   = report_summary.get("regime_stats", {}).get("bear", 0)

    sev_colors = {"CRITICAL": "#dc2626", "WARNING": "#d97706", "INFO": "#2563eb"}

    rows = ""
    for a in alerts:
        color = sev_colors.get(a.get("severity", "INFO"), "#6b7280")
        rows += f"""
        <tr>
          <td style="padding:8px;border-bottom:1px solid #e5e7eb">
            <span style="color:{color};font-weight:bold">{a.get('severity')}</span>
          </td>
          <td style="padding:8px;border-bottom:1px solid #e5e7eb">{a.get('ticker') or 'Market'}</td>
          <td style="padding:8px;border-bottom:1px solid #e5e7eb">{a.get('title')}</td>
          <td style="padding:8px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#6b7280">{a.get('message','')[:100]}</td>
        </tr>"""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:800px;margin:0 auto">
      <div style="background:#0d1117;color:#e6edf3;padding:20px;border-radius:8px 8px 0 0">
        <h2 style="margin:0">📊 Trading Intelligence System</h2>
        <p style="margin:5px 0;color:#8b949e">{today} · Daily Alert Report</p>
      </div>
      <div style="background:#f9fafb;padding:20px">
        <div style="background:white;border-radius:8px;padding:16px;margin-bottom:16px;border:1px solid #e5e7eb">
          <h3 style="margin:0 0 8px">Market Regime: {regime}</h3>
          <span style="color:#3fb950">▲ {bull} Bullish</span> &nbsp;
          <span style="color:#f85149">▼ {bear} Bearish</span>
        </div>
        <div style="background:white;border-radius:8px;padding:16px;border:1px solid #e5e7eb">
          <h3 style="margin:0 0 12px">Alerts ({len(alerts)})</h3>
          <table style="width:100%;border-collapse:collapse">
            <thead>
              <tr style="background:#f3f4f6">
                <th style="padding:8px;text-align:left">Severity</th>
                <th style="padding:8px;text-align:left">Asset</th>
                <th style="padding:8px;text-align:left">Alert</th>
                <th style="padding:8px;text-align:left">Detail</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      </div>
      <div style="background:#0d1117;color:#8b949e;padding:12px 20px;border-radius:0 0 8px 8px;font-size:12px">
        ⚠️ Research tool only. Not financial advice. Signals are probabilistic.
      </div>
    </body></html>"""
    return html


def send_email(alerts: list, report_summary: dict, cfg: dict) -> bool:
    """
    Send alert email via SMTP.
    Returns True if sent successfully.
    """
    email_cfg = cfg.get("email", {})
    if not email_cfg.get("enabled", False):
        return False

    severities = email_cfg.get("send_on_severity", ["CRITICAL", "WARNING"])
    filtered   = [a for a in alerts if a.get("severity") in severities]

    if not filtered:
        logger.info("No alerts match email severity filter — skipping email")
        return False

    try:
        msg = MIMEMultipart("alternative")
        today = str(date.today())
        n_crit = sum(1 for a in filtered if a.get("severity") == "CRITICAL")
        msg["Subject"] = f"[TIS] {n_crit} CRITICAL alerts — {today}" if n_crit else f"[TIS] {len(filtered)} alerts — {today}"
        msg["From"]    = email_cfg["sender_email"]
        msg["To"]      = email_cfg["recipient_email"]

        html_body = _format_email_html(filtered, report_summary)
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(email_cfg["smtp_host"], email_cfg["smtp_port"]) as server:
            server.starttls()
            server.login(email_cfg["sender_email"], email_cfg["sender_password"])
            server.send_message(msg)

        logger.info(f"Email sent: {len(filtered)} alerts to {email_cfg['recipient_email']}")
        return True

    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False


# ===========================================================================
# Telegram
# ===========================================================================

def _format_telegram_message(alerts: list, report_summary: dict) -> str:
    """Format alerts as a Telegram message (Markdown)."""
    today  = str(date.today())
    regime = report_summary.get("market_regime", "Unknown")
    n_crit = sum(1 for a in alerts if a.get("severity") == "CRITICAL")
    n_warn = sum(1 for a in alerts if a.get("severity") == "WARNING")

    header = f"📊 *Trading Intelligence System*\n_{today}_\n\n"
    header += f"*Regime:* {regime}\n"
    header += f"🔴 {n_crit} Critical  ⚠️ {n_warn} Warning\n\n"

    lines = []
    for a in alerts[:10]:   # Cap at 10 to avoid Telegram message length limit
        sev_emoji = {"CRITICAL": "🔴", "WARNING": "⚠️", "INFO": "🔵"}.get(a.get("severity"), "⚪")
        ticker = f"[{a['ticker']}] " if a.get("ticker") else ""
        lines.append(f"{sev_emoji} *{ticker}{a.get('title', '')}*\n_{a.get('message', '')[:80]}_")

    body = "\n\n".join(lines)
    footer = "\n\n⚠️ _Research tool only. Not financial advice._"

    return header + body + footer


def send_telegram(alerts: list, report_summary: dict, cfg: dict) -> bool:
    """
    Send alert via Telegram Bot API.
    Returns True if sent successfully.
    """
    tg_cfg = cfg.get("telegram", {})
    if not tg_cfg.get("enabled", False):
        return False

    severities = tg_cfg.get("send_on_severity", ["CRITICAL", "WARNING"])
    filtered   = [a for a in alerts if a.get("severity") in severities]

    if not filtered:
        return False

    try:
        token   = tg_cfg["bot_token"]
        chat_id = tg_cfg["chat_id"]
        text    = _format_telegram_message(filtered, report_summary)

        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id":    chat_id,
            "text":       text,
            "parse_mode": "Markdown",
        }).encode()

        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())

        if result.get("ok"):
            logger.info(f"Telegram message sent: {len(filtered)} alerts")
            return True
        else:
            logger.warning(f"Telegram API error: {result}")
            return False

    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


# ===========================================================================
# Master send function
# ===========================================================================

def send_notifications(
    alerts:         list,
    report_summary: dict,
    config:         Optional[dict] = None,
) -> dict:
    """
    Send all enabled notifications.

    Parameters
    ----------
    alerts         : List of alert dicts (from alerts_engine)
    report_summary : Daily report dict (for regime/summary context)
    config         : Notification config dict (from notifications.yaml)

    Returns
    -------
    Dict: { "email": bool, "telegram": bool }
    """
    if config is None:
        config = load_notification_config()

    if not config:
        logger.info("No notification config found. Skipping notifications.")
        logger.info(f"To enable: edit {NOTIF_CONFIG_PATH}")
        return {"email": False, "telegram": False}

    min_alerts = config.get("min_alerts_to_notify", 1)
    if len(alerts) < min_alerts:
        logger.info(f"Only {len(alerts)} alerts (min: {min_alerts}) — skipping notifications")
        return {"email": False, "telegram": False}

    email_sent    = send_email(alerts, report_summary, config)
    telegram_sent = send_telegram(alerts, report_summary, config)

    return {"email": email_sent, "telegram": telegram_sent}
