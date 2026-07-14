"""
ARIA Obsidian Bridge
Bidirectional read/write interface to the DigitalBrain vault.
Creates and maintains the ARIA/ folder structure inside the vault.
Also reads user's personal notes for trading-relevant context.
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from memory.working_memory import WorkingMemory


ARIA_FOLDERS = [
    "ARIA/sessions",
    "ARIA/decisions",
    "ARIA/signals",
    "ARIA/memory",
    "ARIA/watchlist",
    "ARIA/macro",
    "ARIA/debate_logs",
    "ARIA/chart_reads",
    "ARIA/journal",
    "ARIA/journal/postmortems",
]

LONG_TERM_FILE = "ARIA/memory/long-term-memory.md"
RECENT_FILE    = "ARIA/memory/recent-memory.md"
WATCHLIST_FILE = "ARIA/watchlist/watchlist.md"
LESSONS_FILE   = "ARIA/memory/lessons-learned.md"

# Common ticker pattern — uppercase 1-5 letters optionally preceded by $
TICKER_PATTERN = re.compile(r'\b\$?([A-Z]{1,5})\b')


class ObsidianBridge:
    def __init__(self, vault_path: str):
        self.vault = Path(vault_path)

    def ensure_aria_structure(self):
        """Create ARIA folder structure in vault if it doesn't exist."""
        for folder in ARIA_FOLDERS:
            (self.vault / folder).mkdir(parents=True, exist_ok=True)

        # Create memory files if missing
        lt = self.vault / LONG_TERM_FILE
        if not lt.exists():
            lt.write_text(self._default_long_term_template())

        rt = self.vault / RECENT_FILE
        if not rt.exists():
            rt.write_text(self._default_recent_template())

        wl = self.vault / WATCHLIST_FILE
        if not wl.exists():
            wl.write_text("# ARIA Watchlist\n\n- SPY\n- QQQ\n")

    # ── READ ──────────────────────────────────────────────────────────────────

    def read_long_term_memory(self) -> str:
        path = self.vault / LONG_TERM_FILE
        if path.exists():
            return path.read_text(encoding="utf-8-sig", errors="replace")
        return ""

    def read_recent_memory(self) -> str:
        path = self.vault / RECENT_FILE
        if path.exists():
            return path.read_text(encoding="utf-8-sig", errors="replace")
        return ""

    def read_watchlist(self) -> list:
        path = self.vault / WATCHLIST_FILE
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8-sig", errors="replace").split("\n")
        tickers = []
        for line in lines:
            line = line.strip()
            if line.startswith("- "):
                tickers.append(line[2:].strip())
        return tickers

    def read_decision_file(self, ticker: str) -> Optional[str]:
        path = self.vault / f"ARIA/decisions/{ticker}.md"
        if path.exists():
            return path.read_text(encoding="utf-8-sig", errors="replace")
        return None

    # ── WRITE ─────────────────────────────────────────────────────────────────

    def write_session_note(self, session_id: str, content: str):
        """Append notable analysis snippet to session file."""
        path = self.vault / f"ARIA/sessions/{session_id}.md"
        timestamp = datetime.now().strftime("%H:%M:%S")

        if not path.exists():
            path.write_text(
                f"# ARIA Session {session_id}\n"
                f"Created: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            )

        with path.open("a", encoding="utf-8-sig", errors="replace") as f:
            f.write(f"\n## {timestamp}\n\n{content}\n\n---\n")

    def write_session_summary(self, session_id: str, conversation: list, working_memory: WorkingMemory):
        """Write full session summary on exit."""
        path = self.vault / f"ARIA/sessions/{session_id}.md"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Build summary
        lines = [
            f"# ARIA Session {session_id}",
            f"Date: {ts}",
            f"Turns: {len([m for m in conversation if m['role'] == 'user'])}",
            "",
        ]

        if working_memory.tickers_in_focus:
            lines += ["## Tickers analysed", ""]
            for t in working_memory.tickers_in_focus:
                lines.append(f"- {t}")
            lines.append("")

        if working_memory.past_decisions:
            session_decisions = [d for d in working_memory.past_decisions if d.get("source") == "session"]
            if session_decisions:
                lines += ["## Decisions logged", ""]
                for d in session_decisions:
                    lines.append(f"- **{d.get('ticker')}** | {d.get('direction')} | {d.get('rationale', '')[:100]}")
                lines.append("")

        if working_memory.open_questions:
            lines += ["## Open questions", ""]
            for q in working_memory.open_questions[-5:]:
                lines.append(f"- {q}")
            lines.append("")

        if working_memory.macro_regime:
            lines += [f"## Macro regime noted", "", f"- {working_memory.macro_regime}", ""]

        path.write_text("\n".join(lines), encoding="utf-8-sig", errors="replace")

        # Update recent memory
        self._update_recent_memory(working_memory)

    def write_signal(self, ticker: str, signal_data: dict):
        """Write signal score to ARIA/signals/."""
        path = self.vault / f"ARIA/signals/{ticker}.md"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")

        content = (
            f"# {ticker} Signal\n"
            f"Updated: {ts}\n\n"
            f"**Score:** {signal_data.get('score', 'N/A')}\n"
            f"**Direction:** {signal_data.get('direction', 'N/A')}\n"
            f"**Confidence:** {signal_data.get('confidence', 'N/A')}\n\n"
            f"## Details\n\n"
            f"```json\n{json.dumps(signal_data, indent=2, default=str)}\n```\n"
        )
        path.write_text(content, encoding="utf-8-sig", errors="replace")

    def write_debate_log(self, ticker: str, debate_result: dict):
        """Write cognitive debate output."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.vault / f"ARIA/debate_logs/{ticker}_{ts}.md"

        lines = [f"# {ticker} Cognitive Debate", f"Date: {ts}", ""]
        for agent, view in debate_result.items():
            lines += [f"## {agent}", "", str(view), ""]

        path.write_text("\n".join(lines), encoding="utf-8-sig", errors="replace")

    def write_chart_read(self, chart_name: str, analysis: str):
        """Write chart analysis to vault."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.vault / f"ARIA/chart_reads/{ts}_{chart_name}.md"

        content = (
            f"# Chart Analysis: {chart_name}\n"
            f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"{analysis}\n"
        )
        path.write_text(content, encoding="utf-8-sig", errors="replace")

    def write_macro_snapshot(self, macro_data: dict):
        """Write macro snapshot."""
        ts = datetime.now().strftime("%Y%m%d")
        path = self.vault / f"ARIA/macro/{ts}_snapshot.md"

        lines = [f"# Macro Snapshot {ts}", ""]
        for k, v in macro_data.items():
            lines.append(f"- **{k}:** {v}")

        path.write_text("\n".join(lines), encoding="utf-8-sig", errors="replace")

    def promote_to_long_term(self, working_memory: WorkingMemory):
        """Promote learnings from session to long-term memory file."""
        path = self.vault / LONG_TERM_FILE
        existing = path.read_text(encoding="utf-8-sig", errors="replace") if path.exists() else ""

        additions = []
        ts = datetime.now().strftime("%Y-%m-%d")

        for decision in working_memory.past_decisions:
            if decision.get("source") == "session":
                note = (f"- [{ts}] **{decision.get('ticker')}** {decision.get('direction')} — "
                        f"{decision.get('rationale', '')[:100]} (conf: {decision.get('confidence', '?')})")
                if note not in existing:
                    additions.append(note)

        for pattern in working_memory.learned_patterns:
            if pattern not in existing:
                additions.append(f"- [{ts}] {pattern}")

        if additions:
            section = "\n## Promoted from session " + ts + "\n\n" + "\n".join(additions) + "\n"
            with path.open("a", encoding="utf-8-sig", errors="replace") as f:
                f.write(section)

    def _update_recent_memory(self, working_memory: WorkingMemory):
        """Overwrite recent-memory.md with current session state."""
        path = self.vault / RECENT_FILE
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")

        lines = [
            f"# ARIA Recent Memory",
            f"Updated: {ts}",
            "",
        ]

        if working_memory.tickers_in_focus:
            lines += ["## Tickers", ""]
            for t in working_memory.tickers_in_focus:
                lines.append(f"- {t}")
            lines.append("")

        if working_memory.open_questions:
            lines += ["## Open questions", ""]
            for q in working_memory.open_questions:
                lines.append(f"- {q}")
            lines.append("")

        if working_memory.session_notes:
            lines += ["## Notes", ""]
            for n in working_memory.session_notes[-10:]:
                lines.append(f"- {n}")
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8-sig", errors="replace")

    # ── TEMPLATES ─────────────────────────────────────────────────────────────

    def _default_long_term_template(self) -> str:
        return (
            "# ARIA Long-Term Memory\n"
            "This file is maintained by ARIA. Do not delete sections — add your own notes below.\n\n"
            "## Past decisions\n\n"
            "## Patterns\n\n"
            "## Macro\n\n"
            "- Regime: unknown\n\n"
            "## Risk events\n\n"
        )

    def _default_recent_template(self) -> str:
        return (
            "# ARIA Recent Memory\n"
            "Updated: never\n\n"
            "## Tickers\n\n"
            "## Open questions\n\n"
            "## Notes\n\n"
        )

    # ── READ USER'S PERSONAL VAULT ────────────────────────────────────────────

    def read_user_journal(self, days_back: int = 7) -> str:
        """
        Read the user's personal daily journal notes from the vault.
        Looks for files named YYYY-MM-DD.md in common journal folder patterns.
        Returns concatenated content of recent journal entries.
        """
        journal_folders = ["Journal", "Daily", "Daily Notes", "Diary", "Notes/Daily", ""]
        found_entries = []

        for folder in journal_folders:
            base = self.vault / folder if folder else self.vault
            if not base.exists():
                continue
            for i in range(days_back):
                date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
                for pattern in [f"{date}.md", f"{date} Daily.md", f"Daily {date}.md"]:
                    p = base / pattern
                    if p.exists():
                        content = p.read_text(encoding="utf-8-sig", errors="replace")
                        found_entries.append(f"\n### Journal: {date}\n{content[:1500]}")
                        break

        if not found_entries:
            return ""
        return "\n".join(found_entries)

    def read_ticker_notes(self, ticker: str) -> str:
        """
        Search the vault for any user-written notes that mention a specific ticker.
        Scans all .md files outside the ARIA/ folder for ticker mentions.
        Returns relevant excerpts.
        """
        ticker_upper = ticker.upper()
        ticker_dollar = f"${ticker_upper}"
        excerpts = []

        for md_file in self.vault.rglob("*.md"):
            # Skip ARIA-generated files
            if "ARIA" in str(md_file):
                continue
            try:
                content = md_file.read_text(encoding="utf-8-sig", errors="replace")
                if ticker_upper in content or ticker_dollar in content:
                    # Extract the relevant paragraph(s)
                    lines = content.split("\n")
                    relevant = []
                    for i, line in enumerate(lines):
                        if ticker_upper in line or ticker_dollar in line:
                            start = max(0, i - 2)
                            end = min(len(lines), i + 4)
                            relevant.extend(lines[start:end])
                            relevant.append("...")
                    if relevant:
                        excerpts.append(
                            f"**From {md_file.name}:**\n" + "\n".join(relevant[:20])
                        )
            except Exception:
                continue

        if not excerpts:
            return f"[No personal notes found mentioning {ticker_upper}]"
        return "\n\n".join(excerpts[:5])  # Max 5 source files

    def read_lessons_learned(self) -> str:
        """Read ARIA's accumulated lessons file for system prompt injection."""
        path = self.vault / LESSONS_FILE
        if path.exists():
            return path.read_text(encoding="utf-8-sig", errors="replace")
        return "[No lessons learned yet — ARIA is building experience]"

    def scan_vault_for_trading_context(self, max_files: int = 20) -> str:
        """
        Scan the user's vault for trading-related notes (not in ARIA/).
        Returns a summary of what the user has been thinking about.
        Useful for ARIA to understand the user's current focus areas.
        """
        trading_keywords = {
            "buy", "sell", "trade", "position", "stock", "crypto", "market",
            "bull", "bear", "long", "short", "earnings", "fed", "rates",
            "inflation", "recession", "rally", "crash", "support", "resistance",
        }
        results = []
        scanned = 0

        # Sort by modification time — most recent first
        md_files = sorted(
            [f for f in self.vault.rglob("*.md") if "ARIA" not in str(f)],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )

        for md_file in md_files[:50]:
            if scanned >= max_files:
                break
            try:
                content = md_file.read_text(encoding="utf-8-sig", errors="replace").lower()
                keyword_hits = sum(1 for kw in trading_keywords if kw in content)
                if keyword_hits >= 2:
                    # Extract tickers mentioned
                    raw = md_file.read_text(encoding="utf-8-sig", errors="replace")
                    tickers = list(set(TICKER_PATTERN.findall(raw)))
                    # Filter out common non-ticker all-caps words
                    noise = {"I", "A", "THE", "AND", "OR", "FOR", "TO", "IN", "IS", "IT",
                             "BE", "AT", "BY", "AN", "IF", "NO", "SO", "DO", "ON", "OF",
                             "AS", "MY", "WE", "HE", "SHE", "BUT", "NOT", "FROM", "WITH"}
                    tickers = [t for t in tickers if t not in noise and len(t) >= 2]

                    modified = datetime.fromtimestamp(md_file.stat().st_mtime).strftime("%Y-%m-%d")
                    results.append(
                        f"- **{md_file.name}** (modified {modified}) — "
                        f"{keyword_hits} trading keywords, tickers: {', '.join(tickers[:8]) or 'none detected'}"
                    )
                    scanned += 1
            except Exception:
                continue

        if not results:
            return "[No trading-related notes found in vault outside ARIA/]"

        return "=== USER'S VAULT — TRADING-RELATED NOTES ===\n" + "\n".join(results)

    # ── WRITE LEARNING JOURNALS ───────────────────────────────────────────────

    def write_postmortem(self, outcome: dict):
        """Write a trade postmortem to ARIA/journal/postmortems/."""
        ticker = outcome.get("ticker", "UNKNOWN")
        issued_at = outcome.get("issued_at", "")[:10]
        final_verdict = outcome.get("final_verdict", "UNRESOLVED")
        direction = outcome.get("direction", "?").upper()
        confidence = outcome.get("confidence", "?")
        regime = outcome.get("regime", "unknown")
        reasoning = outcome.get("reasoning", "No reasoning recorded.")
        outcomes_by_horizon = outcome.get("outcomes", {})

        folder = self.vault / "ARIA/journal/postmortems"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{issued_at}_{ticker}_{final_verdict}.md"

        emoji = {"HIT": "✅", "MISS": "❌", "MIXED": "⚠️", "UNRESOLVED": "❓"}.get(final_verdict, "")
        lines = [
            f"# {emoji} {ticker} — {direction} — {final_verdict}",
            f"Issued: {issued_at} | Resolved: {datetime.now().strftime('%Y-%m-%d')}",
            f"Tags: #aria-postmortem #{ticker.lower()} #{final_verdict.lower()}",
            "",
            f"- **Confidence:** {confidence} | **Regime:** {regime} | **Score:** {outcome.get('score', 'N/A')}",
            f"- **Entry Price:** ${outcome.get('entry_price', 'N/A')}",
            "",
            "## My Reasoning",
            f"> {reasoning}",
            "",
            "## What Happened",
        ]

        for horizon, result in sorted(outcomes_by_horizon.items()):
            v_emoji = {"HIT": "✅", "MISS": "❌", "FLAT": "➖"}.get(result.get("verdict", ""), "")
            lines.append(
                f"- **{horizon}:** {v_emoji} {result.get('pct_move', '?')}% "
                f"(exit ${result.get('exit_price', '?')}) — {result.get('verdict', '?')}"
            )

        if final_verdict == "MISS":
            lines += [
                "", "## Lesson",
                "> *(What would have saved this trade? Fill in manually.)*",
            ]
        elif final_verdict == "HIT":
            lines += [
                "", "## Pattern to Remember",
                "> *(What made this work? Fill in manually.)*",
            ]

        lines.append(f"\n*Auto-generated by ARIA on {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path)

    def append_lesson(self, lesson: str, category: str = "critical"):
        """Append a distilled lesson to ARIA/memory/lessons-learned.md."""
        path = self.vault / LESSONS_FILE
        today = datetime.now().strftime("%Y-%m-%d")
        entry = f"- [{today}] {lesson}\n"

        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "# ARIA Lessons Learned\n\n"
                "## Critical Lessons\n\n"
                "## Regime Lessons\n\n"
                "## Ticker Lessons\n\n"
                "## What Works\n\n",
                encoding="utf-8"
            )

        section_map = {
            "regime": "## Regime Lessons",
            "ticker": "## Ticker Lessons",
            "positive": "## What Works",
        }
        target = section_map.get(category.lower(), "## Critical Lessons")

        content = path.read_text(encoding="utf-8")
        if target in content:
            content = content.replace(target, target + "\n" + entry, 1)
            path.write_text(content, encoding="utf-8")
        else:
            with path.open("a", encoding="utf-8") as f:
                f.write(entry)
