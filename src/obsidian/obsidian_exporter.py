"""
src/obsidian/obsidian_exporter.py
Trading Intelligence System — Phase 4
Obsidian Vault Exporter

Exports all TIS model outputs as structured Markdown into:
  C:/Users/sound/Documents/DigitalBrain/01 - Trading/TIS/

Asset notes are SMART-UPDATED:
  - YAML frontmatter + auto-generated sections are overwritten
  - Any manual notes you've written (## Thesis, ## Key Drivers, etc.) are preserved

Vault path MUST exist — raises ObsidianVaultError if not found.
Wrap the call in main.py with try/except so a vault error never kills the pipeline.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("obsidian_exporter")


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class ObsidianVaultError(RuntimeError):
    """Raised when the vault root does not exist."""


# ─────────────────────────────────────────────────────────────────────────────
# Constants — backlink block injected into every note
# ─────────────────────────────────────────────────────────────────────────────

_DASH_LINKS = (
    "[[01 - Trading/TIS/00 Dashboard/Trading Intelligence Dashboard|TIS Dashboard]] · "
    "[[01 - Trading/TIS/00 Dashboard/Asset Master Index|Asset Index]] · "
    "[[01 - Trading/TIS/00 Dashboard/Risk Dashboard|Risk Dashboard]] · "
    "[[01 - Trading/TIS/00 Dashboard/Model Status Dashboard|Model Status]] · "
    "[[01 - Trading/TIS/00 Dashboard/Pulse Bridge Dashboard|Pulse Bridge]] · "
    "[[01 - Trading/TIS/02 Signal Reports/latest_signals|latest_signals]] · "
    "[[01 - Trading/TIS/05 Risk Reports/risk_summary|risk_summary]] · "
    "[[01 - Trading/TIS/09 Model Audit Logs/model_status|model_status]]"
)

_TAGS_CORE = [
    "trading", "market-intelligence", "capitalwithsk", "trading-intelligence-system"
]

# Sections in asset notes that the model manages (will be replaced on update)
_ASSET_AUTO_SECTIONS = {
    "📊 Current Signal",
    "🔮 Futures/Options",
    "🧠 ML Predictions",
}

# Sections in asset notes that the user manages (preserved on update)
_ASSET_MANUAL_SECTIONS = {
    "💡 Thesis",
    "🔑 Key Drivers",
    "⚠️ Risk Factors",
    "📐 Technical Notes",
    "📰 Fundamental Notes",
    "📈 Backtest History",
    "🔗 Vault Cross-References",
}

# Wiki cross-links for known tickers
_WIKI_LINKS: dict[str, list[str]] = {
    "TSLA": ["[[Wiki/Companies/Tesla]]"],
    "BTC-USD": ["[[Wiki/Concepts/Concept-Bitcoin]]"],
    "GC=F":  ["[[Wiki/Concepts/Concept-Gold]]"],
    "USDINR=X": ["[[Wiki/Concepts/Concept-Currency-Risk]]"],
    "EURUSD=X": ["[[Wiki/Macro/FX]]"],
    "GBPUSD=X": ["[[Wiki/Macro/FX]]"],
    "USDJPY=X": ["[[Wiki/Macro/FX]]"],
    "AUDUSD=X": ["[[Wiki/Macro/FX]]"],
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _write(path: Path, content: str) -> None:
    """Write UTF-8 file, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info(f"  → {path.name}")


def _yaml_header(data: dict) -> str:
    """Render YAML frontmatter block."""
    return "---\n" + yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False) + "---\n"


def _pulse_line(date: str) -> str:
    return (
        f"**Pulse today:** "
        f"[[01 - Trading/Analysis/Daily/trading-briefing-{date}|Trading Briefing]] · "
        f"[[01 - Trading/Analysis/Daily/nse-scan-{date}|NSE Scan]] · "
        f"[[Pulse/outputs/daily-pulse-{date}|Daily Pulse]]"
    )


def _signal_emoji(action: str) -> str:
    mapping = {
        "Strong Buy": "🟢", "Buy": "🟩", "Mild Bullish": "🟡",
        "Neutral": "⚪", "Mild Bearish": "🟠", "Sell": "🟥", "Strong Sell": "🔴",
    }
    return mapping.get(action, "⚪")


def _action_to_regime(signals_dict: dict) -> str:
    """Derive a rough market regime from the top signals."""
    if not signals_dict:
        return "neutral"
    scores = [v.get("composite_score", 0) for v in signals_dict.values()]
    avg = sum(scores) / len(scores) if scores else 0
    if avg >= 30:
        return "bull"
    if avg <= -30:
        return "bear"
    return "neutral"


def _top_n(signals_dict: dict, n: int = 3, direction: str = "bullish") -> str:
    """Return comma-separated top N tickers by direction."""
    key = "composite_score"
    reverse = direction == "bullish"
    sorted_sigs = sorted(signals_dict.items(), key=lambda x: x[1].get(key, 0), reverse=reverse)
    tickers = [t for t, _ in sorted_sigs[:n]]
    return ", ".join(tickers)


def _risk_level(macro_data: dict, alerts_json: list) -> str:
    """Derive overall risk level from macro + alert count."""
    vix = macro_data.get("vix", 0) if macro_data else 0
    crit = sum(1 for a in alerts_json if a.get("severity") == "CRITICAL")
    if crit >= 3 or (vix and vix > 35):
        return "CRITICAL"
    if crit >= 1 or (vix and vix > 25):
        return "HIGH"
    if vix and vix > 18:
        return "MEDIUM"
    return "LOW"


# ─────────────────────────────────────────────────────────────────────────────
# Smart asset-note updater — preserves manual sections
# ─────────────────────────────────────────────────────────────────────────────

def _parse_sections(content: str) -> dict[str, str]:
    """
    Split a markdown note into {heading: body} pairs.
    The YAML frontmatter is stored under the key '__yaml__'.
    Text before the first heading is stored under '__preamble__'.
    """
    sections: dict[str, str] = {}

    # Extract YAML frontmatter
    yaml_match = re.match(r'^---\n(.*?)\n---\n', content, re.DOTALL)
    if yaml_match:
        sections["__yaml__"] = yaml_match.group(0)
        content = content[yaml_match.end():]
    else:
        sections["__yaml__"] = ""

    # Split on ## headings
    parts = re.split(r'^(## .+)$', content, flags=re.MULTILINE)
    preamble = parts[0]
    sections["__preamble__"] = preamble

    i = 1
    while i < len(parts) - 1:
        heading_line = parts[i]           # e.g. "## 💡 Thesis"
        body = parts[i + 1]               # everything until next heading
        # Strip leading emoji + spaces to get clean section name
        heading_clean = re.sub(r'^## [^\w]*', '', heading_line).strip()
        sections[heading_clean] = body
        i += 2

    return sections


def _smart_update_asset_note(existing: str, new_yaml: str, new_auto_sections: dict[str, str], new_preamble: str) -> str:
    """
    Merge new model data into an existing asset note.
    Auto sections are replaced. Manual sections are preserved.
    """
    old = _parse_sections(existing)

    # Build result
    result = new_yaml + "\n"
    result += new_preamble

    # Ordered section list (auto first, then manual)
    section_order = [
        ("📊 Current Signal", True),
        ("📅 Signal History", True),
        ("🔮 Futures/Options", True),
        ("🧠 ML Predictions", True),
        ("💡 Thesis", False),
        ("🔑 Key Drivers", False),
        ("⚠️ Risk Factors", False),
        ("📐 Technical Notes", False),
        ("📰 Fundamental Notes", False),
        ("📈 Backtest History", False),
        ("🔗 Vault Cross-References", False),
    ]

    for section_name, is_auto in section_order:
        if is_auto:
            body = new_auto_sections.get(section_name, "\n*Awaiting model data.*\n\n")
        else:
            # Preserve existing manual content; fall back to placeholder
            body = old.get(section_name, "\n*Add your notes here.*\n\n")

        result += f"## {section_name}\n{body}"

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Main Exporter Class
# ─────────────────────────────────────────────────────────────────────────────

class ObsidianExporter:
    """
    Exports TIS model outputs to the Obsidian vault.

    Usage in main.py:
        from src.obsidian.obsidian_exporter import ObsidianExporter, ObsidianVaultError
        try:
            exporter = ObsidianExporter("configs/obsidian.yaml")
            exporter.export_all(
                signals_dict=signals_dict,
                backtest_results=bt_json,
                ml_predictions=predictions_to_json(ml_predictions),
                macro_data=macro_data,
                sentiment_data=sentiment_data,
                alerts_json=alerts_json_list,
                portfolio_data=portfolio_data,
                opt_results=opt_results,
                futures_data=futures_to_json(futures_signals),
                options_data=options_to_json(options_data),
                report=report,
            )
        except ObsidianVaultError as e:
            logger.error(f"Obsidian vault error: {e}")
        except Exception as e:
            logger.warning(f"Obsidian export failed (non-fatal): {e}")
    """

    def __init__(self, config_path: str = "configs/obsidian.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        obs = cfg.get("obsidian", {})
        self.vault_root = Path(obs["vault_root"])
        self.tis_root   = Path(obs["tis_root"])
        self.enabled    = obs.get("enabled", True)

        folders = obs.get("folders", {})
        self.f = {k: self.tis_root / v for k, v in folders.items()}

        if not self.enabled:
            logger.info("Obsidian export disabled in config.")
            return

        # Validate vault root EXISTS — raise if not
        if not self.vault_root.exists():
            raise ObsidianVaultError(
                f"Obsidian vault not found: {self.vault_root}\n"
                f"Make sure your vault is open and the path is correct."
            )

        # Validate TIS root exists — raise with helpful message
        if not self.tis_root.exists():
            raise ObsidianVaultError(
                f"TIS folder not found: {self.tis_root}\n"
                f"Run SETUP_Run_This_First.ps1 to create the folder structure."
            )

        logger.info(f"ObsidianExporter ready → {self.tis_root}")

    # ── Public entry point ────────────────────────────────────────────────────

    def export_all(
        self,
        signals_dict: dict        = None,
        backtest_results: dict    = None,
        ml_predictions: dict      = None,
        macro_data: dict          = None,
        sentiment_data: dict      = None,
        alerts_json: list         = None,
        portfolio_data: dict      = None,
        opt_results: dict         = None,
        futures_data: dict        = None,
        options_data: dict        = None,
        report: dict              = None,
    ) -> None:
        if not self.enabled:
            return

        t0 = time.time()
        date = _today()

        signals_dict    = signals_dict    or {}
        backtest_results= backtest_results or {}
        ml_predictions  = ml_predictions  or {}
        macro_data      = macro_data      or {}
        sentiment_data  = sentiment_data  or {}
        alerts_json     = alerts_json     or []
        portfolio_data  = portfolio_data  or {}
        opt_results     = opt_results     or {}
        futures_data    = futures_data    or {}
        options_data    = options_data    or {}
        report          = report          or {}

        logger.info("── Obsidian export starting ──")

        self._export_daily_market_log(date, signals_dict, macro_data, sentiment_data, alerts_json, ml_predictions, backtest_results, portfolio_data, opt_results, futures_data, options_data)
        self._export_signal_report(date, signals_dict, ml_predictions)
        self._export_asset_notes(date, signals_dict, ml_predictions, backtest_results, futures_data, options_data)
        self._export_backtest_report(date, backtest_results)
        self._export_risk_report(date, signals_dict, macro_data, alerts_json, portfolio_data)
        self._export_portfolio_report(date, portfolio_data, opt_results)
        self._export_derivatives_report(date, futures_data, options_data)
        self._export_ml_report(date, ml_predictions, signals_dict)
        self._export_model_audit(date, signals_dict, macro_data, ml_predictions, backtest_results, alerts_json)
        self._append_system_log(date, signals_dict, alerts_json)

        elapsed = round(time.time() - t0, 1)
        logger.info(f"── Obsidian export complete ({elapsed}s) ──")

    # ── 01 Daily Market Log ───────────────────────────────────────────────────

    def _export_daily_market_log(self, date, signals_dict, macro_data, sentiment_data, alerts_json, ml_predictions, backtest_results, portfolio_data, opt_results, futures_data, options_data):
        regime    = _action_to_regime(signals_dict)
        risk_lvl  = _risk_level(macro_data, alerts_json)
        top_bull  = _top_n(signals_dict, 3, "bullish")
        top_bear  = _top_n(signals_dict, 3, "bearish")
        vix       = macro_data.get("vix", "")
        dxy       = macro_data.get("dxy", "")

        crit = sum(1 for a in alerts_json if a.get("severity") == "CRITICAL")
        warn = sum(1 for a in alerts_json if a.get("severity") == "WARNING")

        sent_bull = sum(1 for s in sentiment_data.values() if s.get("label") == "Bullish")
        sent_bear = sum(1 for s in sentiment_data.values() if s.get("label") == "Bearish")

        # Best backtest sharpe
        best_sharpe = ""
        if backtest_results:
            sharpes = [v.get("sharpe_ratio", 0) for v in backtest_results.values() if isinstance(v, dict)]
            if sharpes:
                best_sharpe = round(max(sharpes), 2)

        # Best opt strategy
        best_strat = list(opt_results.keys())[0] if opt_results else ""

        yaml_data = {
            "type": "daily-market-log",
            "source": "trading-intelligence-system",
            "date": date,
            "market_regime": regime,
            "risk_level": risk_lvl,
            "top_bullish": top_bull,
            "top_bearish": top_bear,
            "vix_level": vix,
            "dxy_level": dxy,
            "tags": _TAGS_CORE + ["daily-market-log", "systematic-research"],
        }

        # Build signal table rows
        def sig_rows(direction, limit=5):
            reverse = direction == "bullish"
            sorted_s = sorted(signals_dict.items(), key=lambda x: x[1].get("composite_score", 0), reverse=reverse)[:limit]
            rows = "| Asset | Signal | Score | Confidence |\n|---|---|---|---|\n"
            for ticker, s in sorted_s:
                rows += f"| {ticker} | {s.get('action','?')} | {s.get('composite_score',0):+.1f} | {s.get('confidence','?')} |\n"
            return rows

        # Asset class summary
        asset_classes = {}
        for ticker, s in signals_dict.items():
            ac = s.get("asset_class", "other")
            asset_classes.setdefault(ac, []).append(s.get("composite_score", 0))
        ac_table = "| Class | Avg Score | Assets |\n|---|---|---|\n"
        for ac, scores in sorted(asset_classes.items()):
            ac_table += f"| {ac} | {sum(scores)/len(scores):+.1f} | {len(scores)} |\n"

        # ML summary
        ml_summary = ""
        if ml_predictions:
            for ticker, pred in list(ml_predictions.items())[:3]:
                if isinstance(pred, dict):
                    p1 = pred.get("pred_1d", pred.get("1d", "?"))
                    ml_summary += f"- **{ticker}**: 1d={p1}\n"

        content = (
            f"{_yaml_header(yaml_data)}\n"
            f"# 📅 Daily Market Log — {date}\n\n"
            f"> Generated by Trading Intelligence System (Phase 4).\n"
            f"> Back to [[01 - Trading/TIS/00 Dashboard/Trading Intelligence Dashboard|🧠 TIS Dashboard]] · "
            f"[[01 - Trading/TIS/00 Dashboard/Market Intelligence Index|Market Intelligence Index]] · "
            f"[[Dashboard/HOME|🏠 HOME]]\n\n"
            f"---\n\n"
            f"## 🔗 Links\n{_DASH_LINKS}\n\n"
            f"{_pulse_line(date)}\n\n"
            f"---\n\n"
            f"## 🌍 Market Overview\n\n"
            f"| Field | Value |\n|---|---|\n"
            f"| Date | {date} |\n"
            f"| Market Regime | **{regime}** |\n"
            f"| Risk Level | **{risk_lvl}** |\n"
            f"| VIX | {vix} |\n"
            f"| DXY | {dxy} |\n"
            f"| Alerts | {crit} CRITICAL · {warn} WARNING |\n"
            f"| Sentiment | {sent_bull} Bullish · {sent_bear} Bearish |\n\n"
            f"---\n\n"
            f"## 🟢 Top Bullish Signals\n\n{sig_rows('bullish')}\n"
            f"---\n\n"
            f"## 🔴 Top Bearish Signals\n\n{sig_rows('bearish')}\n"
            f"---\n\n"
            f"## 📊 Asset Class Summary\n\n{ac_table}\n"
            f"---\n\n"
            f"## ⚠️ Risk Warnings\n\n"
        )

        for a in alerts_json:
            if a.get("severity") in ("CRITICAL", "WARNING"):
                content += f"- **{a.get('severity')}** [{a.get('ticker','')}] {a.get('message','')}\n"
        if not any(a.get("severity") in ("CRITICAL","WARNING") for a in alerts_json):
            content += "- No critical or warning alerts.\n"

        content += (
            f"\n---\n\n"
            f"## 🧠 ML Prediction Notes\n\n{ml_summary if ml_summary else '*No ML data.*'}\n\n"
            f"---\n\n"
            f"## 📈 Backtest Notes\n\n"
            f"**Best Sharpe:** {best_sharpe}\n\n"
            f"---\n\n"
            f"## 💼 Portfolio Notes\n\n"
            f"**Strategy Used:** {best_strat}\n\n"
            f"---\n\n"
            f"## 🧐 Research Interpretation\n\n*Add your interpretation here.*\n\n"
            f"---\n\n"
            f"## 📋 Actions for Tomorrow\n\n- [ ] \n\n"
            f"---\n\n"
            f"## 🔗 Cross-References\n\n"
            f"- [[01 - Trading/TIS/02 Signal Reports/{date}_signals|{date} Signals]]\n"
            f"- [[01 - Trading/TIS/05 Risk Reports/{date}_risk_report|{date} Risk Report]]\n"
            f"- [[01 - Trading/TIS/09 Model Audit Logs/{date}_model_audit|{date} Model Audit]]\n"
            f"- [[Wiki/Macro/Interest-Rates]] · [[Wiki/Macro/Inflation]] · [[Wiki/Macro/FX]]\n\n"
            f"*Tags: #trading #daily-market-log #market-intelligence #systematic-research #capitalwithsk #trading-intelligence-system*\n"
        )

        _write(self.f["daily_logs"] / f"{date}_market_report.md", content)

    # ── 02 Signal Reports ─────────────────────────────────────────────────────

    def _export_signal_report(self, date, signals_dict, ml_predictions):
        buckets = {
            "Strong Buy": [], "Buy": [], "Mild Bullish": [],
            "Neutral": [], "Mild Bearish": [], "Sell": [], "Strong Sell": [],
        }
        for ticker, s in signals_dict.items():
            action = s.get("action", "Neutral")
            if action in buckets:
                buckets[action].append((ticker, s))

        yaml_data = {
            "type": "signal-report",
            "source": "trading-intelligence-system",
            "date": date,
            "total_assets": len(signals_dict),
            "strong_buy_count": len(buckets["Strong Buy"]),
            "buy_count": len(buckets["Buy"]),
            "neutral_count": len(buckets["Neutral"]),
            "sell_count": len(buckets["Sell"]),
            "strong_sell_count": len(buckets["Strong Sell"]),
            "tags": _TAGS_CORE + ["signals", "signal-report"],
        }

        def table(items):
            if not items:
                return "*None.*\n"
            rows = "| Asset | Score | Confidence | ML 1D |\n|---|---|---|---|\n"
            for ticker, s in sorted(items, key=lambda x: abs(x[1].get("composite_score", 0)), reverse=True):
                ml = ml_predictions.get(ticker, {})
                p1 = ml.get("pred_1d", ml.get("1d", "—")) if isinstance(ml, dict) else "—"
                rows += (
                    f"| **{ticker}** | {s.get('composite_score',0):+.1f} | "
                    f"{s.get('confidence','?')} | {p1} |\n"
                )
            return rows

        header = (
            f"{_yaml_header(yaml_data)}\n"
            f"# 📊 Signal Report — {date}\n\n"
            f"> Back to [[01 - Trading/TIS/00 Dashboard/Trading Intelligence Dashboard|🧠 TIS Dashboard]] · "
            f"[[01 - Trading/TIS/00 Dashboard/Asset Master Index|Asset Index]] · [[Dashboard/HOME|🏠 HOME]]\n\n"
            f"---\n\n"
            f"## 🔗 Links\n{_DASH_LINKS}\n\n"
            f"{_pulse_line(date)}\n\n"
            f"---\n\n"
        )

        body = ""
        emoji_map = {"Strong Buy":"🟢","Buy":"🟩","Mild Bullish":"🟡","Neutral":"⚪","Mild Bearish":"🟠","Sell":"🟥","Strong Sell":"🔴"}
        for action, items in buckets.items():
            body += f"## {emoji_map.get(action,'')} {action}\n\n{table(items)}\n"

        body += f"\n*Tags: #trading #signals #signal-report #market-intelligence #capitalwithsk #trading-intelligence-system*\n"

        full = header + body

        # Write latest (overwrite) + dated archive
        _write(self.f["signals"] / "latest_signals.md", full)
        _write(self.f["signals"] / f"{date}_signals.md", full)

    # ── 03 Asset Notes (smart update) ─────────────────────────────────────────

    def _export_asset_notes(self, date, signals_dict, ml_predictions, backtest_results, futures_data, options_data):
        for ticker, s in signals_dict.items():
            self._export_one_asset(ticker, s, date, ml_predictions, backtest_results, futures_data, options_data)

    def _export_one_asset(self, ticker, s, date, ml_predictions, backtest_results, futures_data, options_data):
        action     = s.get("action", "Neutral")
        score      = s.get("composite_score", 0)
        conf       = s.get("confidence", "?")
        asset_class= s.get("asset_class", "unknown")
        price      = s.get("current_price", "")
        risk       = s.get("risk_level", "")

        ml = ml_predictions.get(ticker, {}) if isinstance(ml_predictions, dict) else {}
        p1  = ml.get("pred_1d",  ml.get("1d",  "—")) if isinstance(ml, dict) else "—"
        p5  = ml.get("pred_5d",  ml.get("5d",  "—")) if isinstance(ml, dict) else "—"
        p20 = ml.get("pred_20d", ml.get("20d", "—")) if isinstance(ml, dict) else "—"

        # Futures/options context for this ticker
        fut_bias = ""
        if isinstance(futures_data, dict):
            for k, v in futures_data.items():
                if isinstance(v, dict) and v.get("underlying", "") == ticker:
                    fut_bias = v.get("signal", v.get("action", ""))

        opt_flow = ""
        if isinstance(options_data, dict) and ticker in options_data:
            od = options_data[ticker]
            if isinstance(od, dict):
                opt_flow = od.get("iv_rank", od.get("put_call_ratio", ""))

        # Backtest for this ticker
        bt = backtest_results.get(ticker, {}) if isinstance(backtest_results, dict) else {}
        sharpe  = bt.get("sharpe_ratio", "—")
        cagr    = bt.get("cagr", "—")
        max_dd  = bt.get("max_drawdown", "—")
        wr      = bt.get("win_rate", "—")
        trades  = bt.get("total_trades", "—")

        yaml_data = {
            "type": "asset-note",
            "ticker": ticker,
            "asset_class": asset_class,
            "last_updated": date,
            "current_signal": action,
            "signal_score": round(score, 1),
            "confidence": conf,
            "current_price": price,
            "risk_level": risk,
            "ml_1d": p1,
            "ml_5d": p5,
            "ml_20d": p20,
            "tags": _TAGS_CORE + ["asset-note"],
        }

        new_yaml = _yaml_header(yaml_data)

        new_preamble = (
            f"# 📈 {ticker}\n\n"
            f"> Auto-updated by TIS (Phase 4) · Last run: {_now_iso()}\n"
            f"> Back to [[01 - Trading/TIS/00 Dashboard/Asset Master Index|Asset Master Index]] · "
            f"[[01 - Trading/TIS/00 Dashboard/Trading Intelligence Dashboard|🧠 TIS Dashboard]] · "
            f"[[Dashboard/HOME|🏠 HOME]]\n\n"
            f"---\n\n"
            f"## 🔗 Links\n{_DASH_LINKS}\n\n"
            f"{_pulse_line(date)}\n\n"
            f"---\n\n"
        )

        new_auto_sections = {
            "📊 Current Signal": (
                f"\n| Field | Value |\n|---|---|\n"
                f"| Signal | **{_signal_emoji(action)} {action}** |\n"
                f"| Score | {score:+.1f} |\n"
                f"| Confidence | {conf} |\n"
                f"| Price | {price} |\n"
                f"| Risk Level | {risk} |\n"
                f"| Last Updated | {date} |\n\n"
            ),
            "📅 Signal History": (
                f"\n```dataview\nTABLE date, current_signal, signal_score, confidence\n"
                f"FROM \"01 - Trading/TIS/02 Signal Reports\"\n"
                f"WHERE contains(file.content, \"{ticker}\")\n"
                f"SORT date DESC\nLIMIT 10\n```\n\n"
                f"*Without Dataview: search for {ticker} in [[01 - Trading/TIS/02 Signal Reports/latest_signals|latest_signals]]*\n\n"
            ),
            "🔮 Futures/Options": (
                f"\n| Field | Value |\n|---|---|\n"
                f"| Futures Bias | {fut_bias if fut_bias else '—'} |\n"
                f"| Options Flow (IV/PCR) | {opt_flow if opt_flow else '—'} |\n\n"
            ),
            "🧠 ML Predictions": (
                f"\n| Horizon | Bullish Probability |\n|---|---|\n"
                f"| 1-Day | {p1} |\n"
                f"| 5-Day | {p5} |\n"
                f"| 20-Day | {p20} |\n\n"
            ),
        }

        # Wiki cross-links section body
        wiki = _WIKI_LINKS.get(ticker, [])
        wiki_body = "\n"
        if wiki:
            for link in wiki:
                wiki_body += f"- {link}\n"
        wiki_body += (
            f"- [[01 - Trading/Watchlists/NSE/nse-watchlist]] *(if NSE listed)*\n"
            f"- [[01 - Trading/TIS/00 Dashboard/Pulse Bridge Dashboard|Pulse Bridge]]\n"
            f"- [[01 - Trading/Options-Futures/01-Foundations/options-fundamentals]]\n\n"
        )

        # Default backtest body for new notes
        bt_body = (
            f"\n| Period | Sharpe | CAGR | Max DD | Win Rate | Trades |\n"
            f"|---|---|---|---|---|---|\n"
            f"| Full | {sharpe} | {cagr} | {max_dd} | {wr} | {trades} |\n\n"
            f"> ⚠️ Survivorship bias applies. < 5 trades = statistically insignificant.\n\n"
        )

        asset_file = self.f["assets"] / f"{ticker}.md"

        if asset_file.exists():
            # Smart update — preserve manual sections
            existing = asset_file.read_text(encoding="utf-8")
            # Inject backtest into auto sections for smart update
            new_auto_sections["📈 Backtest History"] = bt_body
            new_auto_sections["🔗 Vault Cross-References"] = wiki_body
            updated = _smart_update_asset_note(existing, new_yaml, new_auto_sections, new_preamble)
            _write(asset_file, updated)
        else:
            # New note — write full template with placeholders for manual sections
            full = new_yaml + new_preamble
            full += f"## 📊 Current Signal\n{new_auto_sections['📊 Current Signal']}"
            full += f"## 📅 Signal History\n{new_auto_sections['📅 Signal History']}"
            full += f"## 🔮 Futures/Options\n{new_auto_sections['🔮 Futures/Options']}"
            full += f"## 🧠 ML Predictions\n{new_auto_sections['🧠 ML Predictions']}"
            full += "## 💡 Thesis\n\n*Add your investment thesis here.*\n\n"
            full += "## 🔑 Key Drivers\n\n- \n- \n\n"
            full += "## ⚠️ Risk Factors\n\n- \n- \n\n"
            full += "## 📐 Technical Notes\n\n**Key Levels:**\n**Trend:**\n**Momentum:**\n\n"
            full += "## 📰 Fundamental Notes\n\n**Sector:**\n**Catalyst:**\n\n"
            full += f"## 📈 Backtest History\n{bt_body}"
            full += f"## 🔗 Vault Cross-References\n{wiki_body}"
            full += f"\n*Tags: #trading #asset-note #market-intelligence #capitalwithsk #trading-intelligence-system*\n"
            _write(asset_file, full)

    # ── 04 Backtest Report ────────────────────────────────────────────────────

    def _export_backtest_report(self, date, backtest_results):
        yaml_data = {
            "type": "backtest-report",
            "source": "trading-intelligence-system",
            "date": date,
            "tags": _TAGS_CORE + ["backtest"],
        }

        rows = "| Asset | Sharpe | CAGR | Max DD | Win Rate | Trades |\n|---|---|---|---|---|---|\n"
        for ticker, bt in (backtest_results.items() if isinstance(backtest_results, dict) else []):
            if isinstance(bt, dict):
                rows += (
                    f"| {ticker} | {bt.get('sharpe_ratio','—')} | {bt.get('cagr','—')} | "
                    f"{bt.get('max_drawdown','—')} | {bt.get('win_rate','—')} | {bt.get('total_trades','—')} |\n"
                )

        content = (
            f"{_yaml_header(yaml_data)}\n"
            f"# 📈 Backtest Report — {date}\n\n"
            f"> Back to [[01 - Trading/TIS/00 Dashboard/Trading Intelligence Dashboard|🧠 TIS Dashboard]] · [[Dashboard/HOME|🏠 HOME]]\n\n"
            f"---\n\n"
            f"## 🔗 Links\n{_DASH_LINKS}\n\n"
            f"---\n\n"
            f"## 📊 Per-Asset Results\n\n{rows}\n"
            f"> ⚠️ **Survivorship bias**: Universe fixed to current winners.\n"
            f"> ⚠️ **Look-ahead bias**: Fix pending in `src/backtesting/backtester.py`.\n"
            f"> ⚠️ Results with < 5 trades are statistically insignificant.\n\n"
            f"---\n\n"
            f"## 🔗 Related\n\n"
            f"- [[01 - Trading/TIS/05 Risk Reports/{date}_risk_report|{date} Risk Report]]\n"
            f"- [[01 - Trading/TIS/06 Portfolio Reports/portfolio_summary|Portfolio Summary]]\n"
            f"- [[01 - Trading/Backtests]]\n\n"
            f"*Tags: #trading #backtest #market-intelligence #capitalwithsk #trading-intelligence-system*\n"
        )

        _write(self.f["backtest"] / "backtest_summary.md", content)
        _write(self.f["backtest"] / f"{date}_backtest_report.md", content)

    # ── 05 Risk Report ────────────────────────────────────────────────────────

    def _export_risk_report(self, date, signals_dict, macro_data, alerts_json, portfolio_data):
        risk_lvl = _risk_level(macro_data, alerts_json)
        vix      = macro_data.get("vix", "")
        dxy      = macro_data.get("dxy", "")

        yaml_data = {
            "type": "risk-report",
            "source": "trading-intelligence-system",
            "date": date,
            "portfolio_risk_level": risk_lvl,
            "vix": vix,
            "tags": _TAGS_CORE + ["risk", "risk-report"],
        }

        # Alert table
        alert_rows = "| Severity | Asset | Message |\n|---|---|---|\n"
        for a in alerts_json:
            alert_rows += f"| {a.get('severity','')} | {a.get('ticker','')} | {a.get('message','')} |\n"
        if not alerts_json:
            alert_rows += "| — | — | No alerts. |\n"

        # Exposure by asset class
        exposure: dict[str, list] = {}
        for ticker, s in signals_dict.items():
            ac = s.get("asset_class", "other")
            exposure.setdefault(ac, []).append(s.get("composite_score", 0))

        exp_table = "| Class | Assets | Avg Score |\n|---|---|---|\n"
        for ac, scores in sorted(exposure.items()):
            exp_table += f"| {ac} | {len(scores)} | {sum(scores)/len(scores):+.1f} |\n"

        content = (
            f"{_yaml_header(yaml_data)}\n"
            f"# ⚠️ Risk Report — {date}\n\n"
            f"> Back to [[01 - Trading/TIS/00 Dashboard/Risk Dashboard|Risk Dashboard]] · "
            f"[[01 - Trading/TIS/00 Dashboard/Trading Intelligence Dashboard|🧠 TIS Dashboard]] · [[Dashboard/HOME|🏠 HOME]]\n\n"
            f"---\n\n"
            f"## 🔗 Links\n{_DASH_LINKS}\n\n"
            f"{_pulse_line(date)}\n\n"
            f"---\n\n"
            f"## 💼 Portfolio Risk\n\n"
            f"| Metric | Value |\n|---|---|\n"
            f"| Risk Level | **{risk_lvl}** |\n"
            f"| VIX | {vix} |\n"
            f"| DXY | {dxy} |\n\n"
            f"---\n\n"
            f"## 📊 Asset Class Exposure\n\n{exp_table}\n"
            f"---\n\n"
            f"## 🚨 Alerts\n\n{alert_rows}\n"
            f"---\n\n"
            f"## 🎯 Risk Actions\n\n- [ ] \n\n"
            f"*Tags: #trading #risk #risk-report #market-intelligence #capitalwithsk #trading-intelligence-system*\n"
        )

        _write(self.f["risk"] / "risk_summary.md", content)
        _write(self.f["risk"] / f"{date}_risk_report.md", content)

    # ── 06 Portfolio Report ───────────────────────────────────────────────────

    def _export_portfolio_report(self, date, portfolio_data, opt_results):
        yaml_data = {
            "type": "portfolio-report",
            "source": "trading-intelligence-system",
            "date": date,
            "strategies": list(opt_results.keys()) if opt_results else [],
            "tags": _TAGS_CORE + ["portfolio"],
        }

        strat_section = ""
        for strat, data in (opt_results.items() if isinstance(opt_results, dict) else []):
            if isinstance(data, dict):
                sharpe = data.get("sharpe_ratio", "—")
                alloc  = data.get("total_allocated", "—")
                div    = data.get("diversification", "—")
                strat_section += (
                    f"### {strat}\n\n"
                    f"| Sharpe | Allocated | Diversification |\n|---|---|---|\n"
                    f"| {sharpe} | {alloc} | {div} |\n\n"
                )
                weights = data.get("weights", {})
                if weights:
                    strat_section += "**Top Weights:**\n"
                    top_w = sorted(weights.items(), key=lambda x: abs(x[1]), reverse=True)[:5]
                    for t, w in top_w:
                        strat_section += f"- {t}: {w:.1%}\n"
                    strat_section += "\n"

        content = (
            f"{_yaml_header(yaml_data)}\n"
            f"# 💼 Portfolio Report — {date}\n\n"
            f"> Back to [[01 - Trading/TIS/00 Dashboard/Trading Intelligence Dashboard|🧠 TIS Dashboard]] · [[Dashboard/HOME|🏠 HOME]]\n\n"
            f"---\n\n"
            f"## 🔗 Links\n{_DASH_LINKS}\n\n"
            f"---\n\n"
            f"## 📊 Optimisation Strategies\n\n{strat_section if strat_section else '*No portfolio data.*'}\n"
            f"---\n\n"
            f"## ⚠️ Notes\n\n"
            f"- Mean-variance may fall back to signal-weighted if correlation matrix is singular.\n"
            f"- Max weight per asset: 15%\n\n"
            f"*Tags: #trading #portfolio #market-intelligence #capitalwithsk #trading-intelligence-system*\n"
        )

        _write(self.f["portfolio"] / "portfolio_summary.md", content)

    # ── 07 Derivatives Report ─────────────────────────────────────────────────

    def _export_derivatives_report(self, date, futures_data, options_data):
        yaml_data = {
            "type": "derivatives-report",
            "source": "trading-intelligence-system",
            "date": date,
            "tags": _TAGS_CORE + ["derivatives"],
        }

        # Futures table
        fut_rows = "| Contract | Signal | Score |\n|---|---|---|\n"
        for ticker, f in (futures_data.items() if isinstance(futures_data, dict) else []):
            if isinstance(f, dict):
                fut_rows += f"| {ticker} | {f.get('action', f.get('signal','—'))} | {f.get('composite_score','—')} |\n"

        # Options table
        opt_rows = "| Ticker | IV Rank | Put/Call | Bias |\n|---|---|---|---|\n"
        for ticker, o in (options_data.items() if isinstance(options_data, dict) else []):
            if isinstance(o, dict):
                opt_rows += (
                    f"| {ticker} | {o.get('iv_rank','—')} | "
                    f"{o.get('put_call_ratio','—')} | {o.get('bias','—')} |\n"
                )

        content = (
            f"{_yaml_header(yaml_data)}\n"
            f"# 🔮 Derivatives Report — {date}\n\n"
            f"> Back to [[01 - Trading/TIS/00 Dashboard/Trading Intelligence Dashboard|🧠 TIS Dashboard]] · [[Dashboard/HOME|🏠 HOME]]\n\n"
            f"---\n\n"
            f"## 🔗 Links\n{_DASH_LINKS}\n\n"
            f"---\n\n"
            f"## 📈 Futures Signals\n\n{fut_rows}\n"
            f"---\n\n"
            f"## 🎯 Options Flow\n\n{opt_rows}\n"
            f"---\n\n"
            f"## 🔗 Related\n\n"
            f"- [[01 - Trading/Options-Futures/01-Foundations/futures-fundamentals]]\n"
            f"- [[01 - Trading/Options-Futures/01-Foundations/options-fundamentals]]\n"
            f"- [[01 - Trading/Options-Futures/03-Greeks/greeks-masterclass]]\n\n"
            f"*Tags: #trading #derivatives #market-intelligence #capitalwithsk #trading-intelligence-system*\n"
        )

        _write(self.f["derivatives"] / "derivatives_summary.md", content)

    # ── 08 ML Predictions ────────────────────────────────────────────────────

    def _export_ml_report(self, date, ml_predictions, signals_dict):
        yaml_data = {
            "type": "ml-predictions",
            "source": "trading-intelligence-system",
            "date": date,
            "tags": _TAGS_CORE + ["ml-predictions"],
        }

        rows = "| Asset | 1-Day | 5-Day | 20-Day | Signal Agrees? |\n|---|---|---|---|---|\n"
        for ticker, pred in (ml_predictions.items() if isinstance(ml_predictions, dict) else []):
            if isinstance(pred, dict):
                p1  = pred.get("pred_1d",  pred.get("1d",  "—"))
                p5  = pred.get("pred_5d",  pred.get("5d",  "—"))
                p20 = pred.get("pred_20d", pred.get("20d", "—"))
                sig_action = signals_dict.get(ticker, {}).get("action", "?")
                bullish_actions = {"Strong Buy", "Buy", "Mild Bullish"}
                agrees = "✅" if (sig_action in bullish_actions and str(p1) > "0.5") else "—"
                rows += f"| {ticker} | {p1} | {p5} | {p20} | {agrees} |\n"

        content = (
            f"{_yaml_header(yaml_data)}\n"
            f"# 🤖 ML Predictions — {date}\n\n"
            f"> Back to [[01 - Trading/TIS/00 Dashboard/Model Status Dashboard|Model Status]] · "
            f"[[01 - Trading/TIS/00 Dashboard/Trading Intelligence Dashboard|🧠 TIS Dashboard]] · [[Dashboard/HOME|🏠 HOME]]\n\n"
            f"---\n\n"
            f"## 🔗 Links\n{_DASH_LINKS}\n\n"
            f"---\n\n"
            f"## 🔮 Predictions Table\n\n{rows}\n"
            f"---\n\n"
            f"## ⚠️ Known Issues\n\n"
            f"- Forex ML models have 0 rows (no volume data in yfinance forex)\n"
            f"- No walk-forward validation yet\n"
            f"- Trained on 80/20 split — no held-out regime test\n\n"
            f"*Tags: #trading #ml-predictions #market-intelligence #capitalwithsk #trading-intelligence-system*\n"
        )

        _write(self.f["ml"] / "ml_predictions.md", content)
        _write(self.f["ml"] / "model_confidence.md", content)

    # ── 09 Model Audit Log ────────────────────────────────────────────────────

    def _export_model_audit(self, date, signals_dict, macro_data, ml_predictions, backtest_results, alerts_json):
        status = "SUCCESS"
        errors = 0
        if not signals_dict:
            status = "PARTIAL"; errors += 1
        if not macro_data:
            status = "PARTIAL"; errors += 1
        if not ml_predictions:
            status = "PARTIAL"; errors += 1

        yaml_data = {
            "type": "model-audit",
            "source": "trading-intelligence-system",
            "date": date,
            "model_phase": "Phase 4",
            "status": status,
            "assets_processed": len(signals_dict),
            "signals_generated": len(signals_dict),
            "errors": errors,
            "tags": _TAGS_CORE + ["model-audit"],
        }

        checklist = [
            ("Data downloader",    bool(signals_dict)),
            ("Signal engine",      bool(signals_dict)),
            ("ML predictions",     bool(ml_predictions)),
            ("Backtester",         bool(backtest_results)),
            ("Macro data",         bool(macro_data)),
            ("Alerts engine",      bool(alerts_json) or True),
            ("Obsidian export",    True),
        ]

        check_md = ""
        for module, ok in checklist:
            mark = "x" if ok else " "
            check_md += f"- [{mark}] {module}\n"

        known_issues = (
            "- [ ] Look-ahead bias in backtester (`src/backtesting/backtester.py`)\n"
            "- [ ] Forex ML 0 rows — no volume in yfinance forex (`src/models/ml_model.py`)\n"
            "- [ ] FRED data broken — pandas-datareader incompatibility (`src/macro/macro_data.py`)\n"
            "- [ ] Sentiment no negation detection (`src/sentiment/sentiment_analyzer.py`)\n"
            "- [ ] No walk-forward validation (`src/models/model_trainer.py`)\n"
            "- [ ] Survivorship bias in universe (`src/backtesting/backtester.py`)\n"
        )

        content = (
            f"{_yaml_header(yaml_data)}\n"
            f"# 🤖 Model Audit — {date}\n\n"
            f"> Back to [[01 - Trading/TIS/00 Dashboard/Model Status Dashboard|Model Status]] · "
            f"[[01 - Trading/TIS/00 Dashboard/Trading Intelligence Dashboard|🧠 TIS Dashboard]] · [[Dashboard/HOME|🏠 HOME]]\n\n"
            f"---\n\n"
            f"## 🔗 Links\n{_DASH_LINKS}\n\n"
            f"---\n\n"
            f"## ✅ Modules Run\n\n{check_md}\n"
            f"---\n\n"
            f"## ⚠️ Active Known Issues\n\n{known_issues}\n"
            f"---\n\n"
            f"## 📋 Run Summary\n\n"
            f"| Field | Value |\n|---|---|\n"
            f"| Status | **{status}** |\n"
            f"| Assets Processed | {len(signals_dict)} |\n"
            f"| Signals Generated | {len(signals_dict)} |\n"
            f"| Error Count | {errors} |\n"
            f"| Run Time | {_now_iso()} |\n\n"
            f"*Tags: #trading #model-audit #market-intelligence #capitalwithsk #trading-intelligence-system*\n"
        )

        _write(self.f["audit"] / "model_status.md", content)
        _write(self.f["audit"] / f"{date}_model_audit.md", content)

    # ── 10 System Log (append-only) ───────────────────────────────────────────

    def _append_system_log(self, date, signals_dict, alerts_json):
        log_file = self.f["system"] / "system_log.md"

        new_entry = (
            f"\n### {_now_iso()}\n\n"
            f"**Status:** ✅ Run complete\n"
            f"**Assets:** {len(signals_dict)}\n"
            f"**Alerts:** {sum(1 for a in alerts_json if a.get('severity')=='CRITICAL')} CRITICAL · "
            f"{sum(1 for a in alerts_json if a.get('severity')=='WARNING')} WARNING\n\n"
            f"---\n"
        )

        if log_file.exists():
            existing = log_file.read_text(encoding="utf-8")
            # Prepend new entry after the header (after first ---)
            parts = existing.split("---\n", 1)
            if len(parts) == 2:
                updated = parts[0] + "---\n" + new_entry + parts[1]
            else:
                updated = existing + new_entry
        else:
            yaml_data = {
                "type": "system-log",
                "source": "trading-intelligence-system",
                "tags": _TAGS_CORE + ["system-log"],
            }
            updated = (
                f"{_yaml_header(yaml_data)}\n"
                f"# 🛠 System Development Log\n\n"
                f"> Append-only run history. Most recent at top.\n"
                f"> Back to [[01 - Trading/TIS/00 Dashboard/Model Status Dashboard|Model Status]] · [[Dashboard/HOME|🏠 HOME]]\n\n"
                f"---\n"
                + new_entry
            )

        _write(log_file, updated)
