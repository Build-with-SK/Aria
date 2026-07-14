"""
obsidian_political_export.py
=============================
Generates Obsidian-compatible Markdown reports for the Political Intelligence Layer.

Output location (configurable):
    DigitalBrain/Trading Intelligence System/12 Political Intelligence/
        political_watchlist.md
        political_signals.md
        trade_candidates.md

Run standalone:
    python -m src.political.obsidian_political_export

Or import from your existing Obsidian export pipeline.

Trading Intelligence System — Phase 4
Research and educational purposes only.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ── Default Obsidian vault paths (adjust to match your actual vault) ──────────
DEFAULT_OBSIDIAN_ROOT = BASE_DIR / "DigitalBrain" / "Trading Intelligence System" / "12 Political Intelligence"

# ── Source data paths ─────────────────────────────────────────────────────────
WATCHLIST_JSON = BASE_DIR / "data" / "political" / "watchlist.json"
SIGNALS_JSON = BASE_DIR / "data" / "political" / "political_signals.json"
TRADE_CANDIDATES_JSON = BASE_DIR / "data" / "trade_candidates.json"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Cannot load {path}: {e}")
        return {}


def _now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")


def _score_bar(score: float, width: int = 20) -> str:
    """Visual ASCII progress bar for scores 0–100."""
    filled = int(max(0, min(score, 100)) / 100 * width)
    return "[" + "█" * filled + "░" * (width - filled) + f"] {score:.0f}/100"


def _political_bar(raw: float, width: int = 20) -> str:
    """Visual bar for political_activity_score –100 to +100."""
    norm = (raw + 100) / 2
    filled = int(norm / 100 * width)
    direction = "▲ BUY" if raw > 10 else ("▼ SELL" if raw < -10 else "◆ NEUTRAL")
    return "[" + "█" * filled + "░" * (width - filled) + f"] {raw:+.1f}  {direction}"


# ─────────────────────────────────────────────
#  Political Watchlist Report
# ─────────────────────────────────────────────

def generate_watchlist_md(output_path: Optional[Path] = None) -> Path:
    out = output_path or (DEFAULT_OBSIDIAN_ROOT / "political_watchlist.md")
    out.parent.mkdir(parents=True, exist_ok=True)

    data = _load_json(WATCHLIST_JSON)
    watchlist = data.get("watchlist", [])
    generated_at = data.get("generated_at", _now_str())

    lines = [
        "---",
        "tags:",
        "  - political-intelligence",
        "  - politician-trades",
        "  - public-disclosures",
        "  - watchlist",
        "  - trading-intelligence-system",
        "  - research-only",
        f"updated: {generated_at}",
        "---",
        "",
        "# 🏛️ Political Watchlist",
        "",
        f"> **Generated:** {generated_at}  ",
        f"> **Tickers tracked:** {len(watchlist)}",
        "",
        "> [!WARNING] Research Signal Only",
        "> Political disclosures are public but delayed (STOCK Act: 45-day filing window).",
        "> Political activity is a **watchlist trigger only**, not a trade recommendation.",
        "> All entries require technical + macro + risk confirmation before any trade consideration.",
        "",
        "**Links:** [[Trading Intelligence Dashboard]] | [[Market Intelligence Index]] | "
        "[[Asset Master Index]] | [[Pulse Dashboard]] | [[09AM Market Scan]] | "
        "[[latest_signals]] | [[risk_summary]] | [[model_status]]",
        "",
        "---",
        "",
        "## Confirmation Matrix",
        "",
        "| Ticker | Company | Score | Activity | 30d Net | Sources | Confidence | Warning |",
        "|--------|---------|-------|----------|---------|---------|------------|---------|",
    ]

    for e in watchlist[:50]:
        ticker = e.get("ticker", "")
        company = e.get("company_name", "")[:30]
        score = e.get("political_activity_score", 0)
        activity = e.get("activity_type", "neutral").title()
        net_30d = e.get("net_activity_30d", 0)
        sources = e.get("source_count", 0)
        conf = e.get("confidence", "Low")
        warn = "⚠️" if e.get("warning") else "✅"
        lines.append(
            f"| **{ticker}** | {company} | {score:+.1f} | {activity} | "
            f"{net_30d:+d} | {sources} | {conf} | {warn} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Detailed Entries",
        "",
    ]

    for e in watchlist[:30]:
        ticker = e.get("ticker", "")
        company = e.get("company_name", "Unknown")
        score = e.get("political_activity_score", 0)
        activity = e.get("activity_type", "neutral")
        politicians = e.get("politicians_involved", [])
        sources = e.get("top_sources", [])
        latest_txn = e.get("latest_transaction_date", "")
        delay = e.get("disclosure_delay_days", 0)
        warning = e.get("warning", "")
        reason = e.get("reason", "")
        buy_30d = e.get("buy_count_30d", 0)
        sell_30d = e.get("sell_count_30d", 0)

        lines += [
            f"### {ticker} — {company}",
            "",
            f"**Political Activity Score:** `{score:+.1f}`  ",
            f"{_political_bar(score)}",
            "",
            f"- **Activity type:** {activity.title()}",
            f"- **Latest transaction:** {latest_txn or 'Unknown'}",
            f"- **Avg disclosure delay:** {delay} days",
            f"- **Buys (30d):** {buy_30d}  **Sells (30d):** {sell_30d}",
            f"- **Sources:** {', '.join(sources) if sources else 'N/A'}",
            f"- **Politicians involved:** {', '.join(politicians[:5]) if politicians else 'N/A'}",
            "",
        ]
        if reason:
            lines.append(f"> {reason}")
            lines.append("")
        if warning:
            lines += [
                "> [!CAUTION]",
                f"> {warning}",
                "",
            ]
        lines.append("---")
        lines.append("")

    lines += [
        "",
        "## Data Sources Reference",
        "",
        "| Source | URL | Type | Reliability |",
        "|--------|-----|------|-------------|",
        "| Capitol Trades | https://www.capitoltrades.com | API/Manual | 90% |",
        "| Quiver Quantitative | https://www.quiverquant.com | API/Manual | 88% |",
        "| Unusual Whales | https://unusualwhales.com/politics | API/Manual | 85% |",
        "| House Disclosures | https://disclosures-clerk.house.gov | Official | 100% |",
        "| Senate Disclosures | https://efts.senate.gov | Official | 100% |",
        "| OGE Disclosures | https://www.oge.gov | Official | 100% |",
        "| ProPublica | https://projects.propublica.org/represent/finances | Research | 85% |",
        "| CREW | https://www.citizensforethics.org | Research | 78% |",
        "",
        "_All data sourced from publicly available legal disclosures only._",
    ]

    out.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Obsidian watchlist report → {out}")
    return out


# ─────────────────────────────────────────────
#  Trade Candidates Report
# ─────────────────────────────────────────────

def generate_trade_candidates_md(output_path: Optional[Path] = None) -> Path:
    out = output_path or (DEFAULT_OBSIDIAN_ROOT / "trade_candidates.md")
    out.parent.mkdir(parents=True, exist_ok=True)

    data = _load_json(TRADE_CANDIDATES_JSON)
    candidates = data.get("trade_candidates", [])
    generated_at = data.get("generated_at", _now_str())

    # Group by status
    trade_c = [c for c in candidates if c.get("trade_status") == "Trade Candidate"]
    research_c = [c for c in candidates if c.get("trade_status") == "Research Candidate"]
    watchlist_c = [c for c in candidates if c.get("trade_status") == "Watchlist Only"]
    avoid_c = [c for c in candidates if c.get("trade_status") == "Avoid"]

    lines = [
        "---",
        "tags:",
        "  - trade-candidates",
        "  - political-intelligence",
        "  - market-intelligence",
        "  - trading-intelligence-system",
        "  - watchlist",
        "  - research-only",
        f"updated: {generated_at}",
        "---",
        "",
        "# 📊 Trade Candidates — Political Intelligence Layer",
        "",
        f"> **Generated:** {generated_at}  ",
        f"> **Total candidates:** {len(candidates)}",
        "",
        "> [!IMPORTANT] Research Output Only",
        "> These candidates are generated for research and educational purposes.",
        "> **No automatic execution. No broker connection.**",
        "> Political signals account for **10% of the final score**.",
        "> Trade Candidates require: Technical ✅ + Macro ✅ + Risk ✅ + options/futures confirmation.",
        "",
        f"> **Do NOT say:** 'Politician bought this, so buy.'",
        f"> **Do say:** 'Public political disclosure activity detected. Added to watchlist. Trade candidate only if market confirmation agrees.'",
        "",
        "**Links:** [[Trading Intelligence Dashboard]] | [[Market Intelligence Index]] | "
        "[[Asset Master Index]] | [[Pulse Dashboard]] | [[09AM Market Scan]] | "
        "[[latest_signals]] | [[risk_summary]] | [[model_status]]",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"| Status | Count |",
        f"|--------|-------|",
        f"| 🟢 Trade Candidate | {len(trade_c)} |",
        f"| 🟡 Research Candidate | {len(research_c)} |",
        f"| 🔵 Watchlist Only | {len(watchlist_c)} |",
        f"| 🔴 Avoid | {len(avoid_c)} |",
        "",
        "---",
        "",
    ]

    def _section(title: str, emoji: str, items: list) -> List[str]:
        section = [f"## {emoji} {title}", "", ]
        if not items:
            section += [f"_No {title.lower()} at this time._", ""]
            return section

        section += [
            f"| Ticker | Score | Political | Technical | Options | Futures | Macro | Risk | Action | Confidence |",
            f"|--------|-------|-----------|-----------|---------|---------|-------|------|--------|------------|",
        ]
        for c in items:
            pol = c.get("political_activity_score", 0)
            pol_norm = round((pol + 100) / 2, 0)
            section.append(
                f"| **{c.get('ticker')}** "
                f"| {c.get('final_trade_candidate_score', 0):.1f} "
                f"| {pol_norm:.0f} "
                f"| {c.get('technical_score', 50):.0f} "
                f"| {c.get('options_score', 50):.0f} "
                f"| {c.get('futures_score', 50):.0f} "
                f"| {c.get('macro_score', 50):.0f} "
                f"| {c.get('risk_score', 50):.0f} "
                f"| {c.get('action', 'neutral').title()} "
                f"| {c.get('confidence', 'Low')} |"
            )
        section.append("")
        for c in items[:10]:
            ticker = c.get("ticker", "")
            score = c.get("final_trade_candidate_score", 0)
            reason = c.get("reason", [])
            risk_warning = c.get("risk_warning", "")
            conf_summary = c.get("confirmation_summary", "")

            section += [
                f"### {ticker}",
                "",
                f"**Final Score:** `{score:.1f}/100`  ",
                f"{_score_bar(score)}",
                "",
                f"**Confirmation:** `{conf_summary}`",
                "",
            ]
            if reason:
                section.append("**Analysis:**")
                for r in reason:
                    section.append(f"- {r}")
                section.append("")
            if risk_warning:
                section += [
                    "> [!WARNING]",
                    f"> {risk_warning[:300]}",
                    "",
                ]
            section.append("---")
            section.append("")
        return section

    lines += _section("Trade Candidates", "🟢", trade_c)
    lines += _section("Research Candidates", "🟡", research_c)
    lines += _section("Watchlist Only", "🔵", watchlist_c[:15])
    lines += _section("Avoid", "🔴", avoid_c[:10])

    lines += [
        "",
        "## Score Formula",
        "",
        "```",
        "final_trade_candidate_score =",
        "    0.35 × technical_score          ← Primary driver",
        "  + 0.15 × options_score",
        "  + 0.15 × futures_score",
        "  + 0.15 × macro_score",
        "  + 0.10 × political_activity_score ← Max 10% weight",
        "  + 0.10 × risk_adjusted_score",
        "```",
        "",
        "_Political activity accounts for a maximum of 10–15% of the final score._",
        "_The model is driven by market confirmation and risk control._",
    ]

    out.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Obsidian trade candidates report → {out}")
    return out


# ─────────────────────────────────────────────
#  Political Signals Report
# ─────────────────────────────────────────────

def generate_signals_md(output_path: Optional[Path] = None) -> Path:
    out = output_path or (DEFAULT_OBSIDIAN_ROOT / "political_signals.md")
    out.parent.mkdir(parents=True, exist_ok=True)

    data = _load_json(SIGNALS_JSON)
    signals = data.get("signals", [])
    generated_at = data.get("generated_at", _now_str())

    lines = [
        "---",
        "tags:",
        "  - political-intelligence",
        "  - politician-trades",
        "  - public-disclosures",
        "  - market-intelligence",
        "  - trading-intelligence-system",
        "  - research-only",
        f"updated: {generated_at}",
        "---",
        "",
        "# 🔭 Political Intelligence Signals",
        "",
        f"> **Generated:** {generated_at}  ",
        f"> **Active signals:** {len(signals)}",
        "",
        "> [!NOTE]",
        "> All signals derived from publicly available legal disclosures only.",
        "> Political signals are **research-only**. Do not trade solely on political activity.",
        "",
        "**Links:** [[Trading Intelligence Dashboard]] | [[Market Intelligence Index]] | "
        "[[Pulse Dashboard]] | [[political_watchlist]] | [[trade_candidates]] | [[risk_summary]]",
        "",
        "---",
        "",
        "| Ticker | Score | Activity | Buy 30d | Sell 30d | Net | Sources | Confidence |",
        "|--------|-------|----------|---------|----------|-----|---------|------------|",
    ]

    for s in signals[:50]:
        ticker = s.get("ticker", "")
        score = s.get("political_activity_score", 0)
        activity = s.get("activity_type", "neutral").title()
        buy_30d = s.get("buy_count_30d", 0)
        sell_30d = s.get("sell_count_30d", 0)
        net = s.get("net_activity_30d", 0)
        sources = s.get("source_count", 0)
        conf = s.get("confidence", "Low")
        lines.append(
            f"| **{ticker}** | {score:+.1f} | {activity} | "
            f"{buy_30d} | {sell_30d} | {net:+d} | {sources} | {conf} |"
        )

    lines += [
        "",
        "---",
        "",
        f"_Political disclosures are subject to STOCK Act 45-day filing delays._  ",
        f"_All data from publicly available, legal sources only._",
    ]

    out.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Obsidian signals report → {out}")
    return out


# ─────────────────────────────────────────────
#  Master export runner
# ─────────────────────────────────────────────

def run_obsidian_export(obsidian_root: Optional[Path] = None) -> List[Path]:
    """
    Generate all Obsidian reports.
    Returns list of created/updated file paths.
    """
    root = obsidian_root or DEFAULT_OBSIDIAN_ROOT
    root.mkdir(parents=True, exist_ok=True)

    files = []
    try:
        files.append(generate_watchlist_md(root / "political_watchlist.md"))
        files.append(generate_signals_md(root / "political_signals.md"))
        files.append(generate_trade_candidates_md(root / "trade_candidates.md"))
        logger.info(f"Obsidian export complete: {len(files)} files written to {root}")
    except Exception as e:
        logger.error(f"Obsidian export failed: {e}")

    return files


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
    created = run_obsidian_export()
    print("\nObsidian reports written:")
    for f in created:
        print(f"  ✓ {f}")
