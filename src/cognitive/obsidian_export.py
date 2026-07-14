"""
obsidian_export.py — ARIA Obsidian Bridge
Exports ARIA's cognitive debates, scenario runs, and session summaries as
markdown notes into the DigitalBrain Obsidian vault, matching the existing
TIS export convention (01 - Trading\\... folder structure, YAML frontmatter).
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List


class ObsidianExporter:
    """
    Writes ARIA outputs as markdown notes into the Obsidian vault.
    Default vault location is assumed to be a sibling of the TIS project
    folder (...\\Documents\\DigitalBrain\\), matching the established
    DigitalBrain vault convention. Override with vault_path if different.
    """

    def __init__(self, base_path: str = None, vault_path: str = None):
        if base_path is None:
            base_path = Path(__file__).parent.parent.parent
        self.base_path = Path(base_path)

        if vault_path:
            self.vault_path = Path(vault_path)
        else:
            # Default assumption: DigitalBrain sits alongside trading-intelligence-system
            self.vault_path = self.base_path.parent / "DigitalBrain"

        self.aria_root = self.vault_path / "01 - Trading" / "ARIA"
        self.debates_dir = self.aria_root / "Debates"
        self.scenarios_dir = self.aria_root / "Scenarios"
        self.sessions_dir = self.aria_root / "Sessions"
        self.tickers_dir = self.aria_root / "Tickers"

    # ── Path / availability checks ───────────────────────────────────────────

    def vault_exists(self) -> bool:
        return self.vault_path.exists()

    def ensure_dirs(self):
        for d in (self.debates_dir, self.scenarios_dir, self.sessions_dir, self.tickers_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _slugify(text: str) -> str:
        text = re.sub(r"[^\w\s-]", "", text).strip().replace(" ", "_")
        return text[:60]

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().strftime("%Y%m%d_%H%M")

    def _frontmatter(self, title: str, tags: List[str], extra: dict = None) -> str:
        lines = ["---"]
        lines.append(f'title: "{title}"')
        lines.append(f"date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"tags: [{', '.join(tags)}]")
        if extra:
            for k, v in extra.items():
                lines.append(f"{k}: {v}")
        lines.append("---")
        return "\n".join(lines)

    def _write(self, folder: Path, filename: str, content: str) -> Optional[str]:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / filename
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return str(path)
        except Exception as e:
            return None  # Caller decides how to report failure

    # ── Export methods ────────────────────────────────────────────────────────

    def export_debate(self, ticker: str, debate_text: str, conviction: str = "", direction: str = "") -> dict:
        """Export a cognitive debate result as a markdown note."""
        ticker = ticker.upper()
        ts = self._timestamp()
        filename = f"{ticker}_{ts}.md"

        frontmatter = self._frontmatter(
            title=f"{ticker} — Cognitive Debate",
            tags=["trading", "aria", "debate", f"ticker/{ticker}"],
            extra={
                "ticker": ticker,
                "conviction": conviction or "unspecified",
                "direction": direction or "unspecified",
            },
        )
        body = f"\n# {ticker} — ARIA Cognitive Debate\n\n[[{ticker}]]\n\n{debate_text}\n"
        content = frontmatter + body

        path = self._write(self.debates_dir, filename, content)
        return {"success": path is not None, "path": path, "type": "debate", "ticker": ticker}

    def export_scenario(self, scenario_name: str, report_text: str) -> dict:
        """Export a scenario analysis run as a markdown note."""
        ts = self._timestamp()
        slug = self._slugify(scenario_name)
        filename = f"{slug}_{ts}.md"

        frontmatter = self._frontmatter(
            title=f"Scenario — {scenario_name}",
            tags=["trading", "aria", "scenario"],
        )
        body = f"\n# Scenario: {scenario_name}\n\n{report_text}\n"
        content = frontmatter + body

        path = self._write(self.scenarios_dir, filename, content)
        return {"success": path is not None, "path": path, "type": "scenario", "name": scenario_name}

    def export_session(self, summary: str, key_tickers: List[str] = None) -> dict:
        """Export a session summary — typically called at the end of a chat session."""
        ts = self._timestamp()
        filename = f"session_{ts}.md"
        key_tickers = key_tickers or []

        tags = ["trading", "aria", "session"] + [f"ticker/{t.upper()}" for t in key_tickers]
        frontmatter = self._frontmatter(
            title=f"ARIA Session — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            tags=tags,
        )

        ticker_links = " ".join(f"[[{t.upper()}]]" for t in key_tickers) if key_tickers else "_none flagged_"
        body = f"\n# ARIA Session Summary\n\n**Tickers discussed:** {ticker_links}\n\n{summary}\n"
        content = frontmatter + body

        path = self._write(self.sessions_dir, filename, content)
        return {"success": path is not None, "path": path, "type": "session"}

    def export_ticker_note(self, ticker: str, note_text: str, append: bool = True) -> dict:
        """
        Write or append to a persistent per-ticker note. Unlike debates/scenarios
        (timestamped, one-shot), this accumulates a running log per ticker.
        """
        ticker = ticker.upper()
        filename = f"{ticker}.md"
        path = self.tickers_dir / filename

        timestamp_header = f"\n\n## {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{note_text}\n"

        try:
            self.tickers_dir.mkdir(parents=True, exist_ok=True)
            if path.exists() and append:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(timestamp_header)
            else:
                frontmatter = self._frontmatter(
                    title=ticker,
                    tags=["trading", "aria", "ticker-note", f"ticker/{ticker}"],
                )
                with open(path, "w", encoding="utf-8") as f:
                    f.write(frontmatter + f"\n# {ticker}\n" + timestamp_header)
            return {"success": True, "path": str(path), "type": "ticker_note", "ticker": ticker}
        except Exception:
            return {"success": False, "path": None, "type": "ticker_note", "ticker": ticker}

    def status_message(self, result: dict) -> str:
        """Format a short confirmation/error message for chat.py to print after an export."""
        if result.get("success"):
            return f"  [Saved to Obsidian: {result['path']}]"
        if not self.vault_exists():
            return (
                f"  [Obsidian export skipped — vault not found at {self.vault_path}. "
                f"Pass vault_path explicitly if your DigitalBrain vault is elsewhere.]"
            )
        return "  [Obsidian export failed — check folder permissions.]"
