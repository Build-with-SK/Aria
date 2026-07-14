"""
experience_journal.py — ARIA Experience Journal
Writes structured learning journals to Obsidian after trade outcomes resolve.

This is how ARIA thinks like a seasoned trader who keeps a trade journal:
- What did I call and why
- What actually happened
- Where did my thinking break down
- What would I do differently next time
- What pattern should I remember
"""

from datetime import datetime
from pathlib import Path
from typing import Optional


class ExperienceJournal:
    """
    Writes post-trade learning journals to the Obsidian vault.
    Called automatically when outcomes resolve, or manually triggered.
    """

    JOURNAL_FOLDER = "ARIA/journal"
    LESSONS_FILE   = "ARIA/memory/lessons-learned.md"
    POSTMORTEM_FOLDER = "ARIA/journal/postmortems"

    def __init__(self, vault_path: str):
        self.vault = Path(vault_path)
        self._ensure_folders()

    def _ensure_folders(self):
        (self.vault / self.JOURNAL_FOLDER).mkdir(parents=True, exist_ok=True)
        (self.vault / self.POSTMORTEM_FOLDER).mkdir(parents=True, exist_ok=True)

        lessons_path = self.vault / self.LESSONS_FILE
        if not lessons_path.exists():
            lessons_path.write_text(
                "# ARIA Lessons Learned\n"
                "Auto-maintained by ARIA. Each lesson is dated and sourced from a real trade outcome.\n\n"
                "## Critical Lessons\n\n"
                "## Regime-Specific Lessons\n\n"
                "## Ticker-Specific Lessons\n\n"
                "## Positive Patterns (What Works)\n\n",
                encoding="utf-8"
            )

    # ── Per-trade postmortem ──────────────────────────────────────────────────

    def write_postmortem(self, outcome: dict):
        """
        Write a detailed postmortem for a single resolved trade.
        Called automatically by the outcome tracker when a signal resolves.
        """
        ticker = outcome.get("ticker", "UNKNOWN")
        direction = outcome.get("direction", "?").upper()
        confidence = outcome.get("confidence", "?")
        issued_at = outcome.get("issued_at", "")[:10]
        regime = outcome.get("regime", "unknown")
        reasoning = outcome.get("reasoning", "No reasoning recorded.")
        final_verdict = outcome.get("final_verdict", "UNRESOLVED")
        outcomes_by_horizon = outcome.get("outcomes", {})

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.vault / self.POSTMORTEM_FOLDER / f"{issued_at}_{ticker}_{final_verdict}.md"

        emoji = {"HIT": "✅", "MISS": "❌", "MIXED": "⚠️", "UNRESOLVED": "❓"}.get(final_verdict, "")

        lines = [
            f"# {emoji} {ticker} — {direction} — {final_verdict}",
            f"Date issued: {issued_at} | Resolved: {datetime.now().strftime('%Y-%m-%d')}",
            f"Tags: #aria-postmortem #{ticker.lower()} #{final_verdict.lower()} #{regime.replace(' ', '-').lower()}",
            "",
            "---",
            "",
            "## What I Called",
            f"- **Ticker:** {ticker}",
            f"- **Direction:** {direction}",
            f"- **Confidence:** {confidence}",
            f"- **Signal Score:** {outcome.get('score', 'N/A')}",
            f"- **Regime:** {regime}",
            f"- **Entry Price:** ${outcome.get('entry_price', 'N/A')}",
            "",
            "## My Reasoning at the Time",
            f"> {reasoning}",
            "",
            "## What Actually Happened",
        ]

        for horizon, result in sorted(outcomes_by_horizon.items()):
            verdict_emoji = {"HIT": "✅", "MISS": "❌", "FLAT": "➖"}.get(result.get("verdict", ""), "")
            lines.append(
                f"- **{horizon}:** {verdict_emoji} {result.get('pct_move', '?')}% move "
                f"(exit: ${result.get('exit_price', '?')}) — {result.get('verdict', '?')}"
            )

        lines += [
            "",
            "## Final Verdict",
            f"**{final_verdict}** {emoji}",
            "",
            "## Post-Trade Analysis",
            "",
        ]

        if final_verdict == "MISS":
            lines += [
                "### What Went Wrong",
                "- [ ] My regime assessment was incorrect",
                "- [ ] I overweighted technical signals vs macro",
                "- [ ] The signal was a false positive at this confidence level",
                "- [ ] An external event invalidated the thesis",
                "- [ ] I had a directional bias I didn't challenge hard enough",
                "",
                "### What I Should Have Done Differently",
                "> *(Fill this in — be specific about the thinking error)*",
                "",
                "### Lesson for Next Time",
                "> *(What rule or heuristic would have saved this trade?)*",
            ]
        elif final_verdict == "HIT":
            lines += [
                "### What Worked",
                "- [ ] Regime alignment was correct",
                "- [ ] Signal score accurately reflected conviction",
                "- [ ] The thesis held through the time horizon",
                "",
                "### Pattern to Remember",
                "> *(What setup led to this hit? How can I recognize it again?)*",
            ]
        else:
            lines += [
                "### Mixed Result Notes",
                "> *(What can I learn from the conflicting outcomes across horizons?)*",
            ]

        lines += [
            "",
            "---",
            f"*Auto-generated by ARIA on {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        ]

        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path)

    # ── Weekly / periodic learning journal ────────────────────────────────────

    def write_learning_journal(self, outcome_tracker, mistake_analyzer):
        """
        Write a weekly learning journal summarizing recent outcomes and lessons.
        This is the high-level reflection document — like a trader's weekly review.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        path = self.vault / self.JOURNAL_FOLDER / f"{today}_weekly-learning.md"

        recent = outcome_tracker.recent_resolved(20)
        stats = outcome_tracker.accuracy_stats()
        analysis = mistake_analyzer.analyze()

        hits = [r for r in recent if r.get("final_verdict") == "HIT"]
        misses = [r for r in recent if r.get("final_verdict") == "MISS"]

        lines = [
            f"# ARIA Weekly Learning Journal — {today}",
            f"Tags: #aria-journal #learning #weekly-review",
            "",
            "---",
            "",
            "## Performance This Period",
            f"- **Total resolved signals:** {stats.get('total', 0)}",
            f"- **Overall hit rate:** {stats.get('hit_rate', 'N/A')}%",
            f"- **Recent sample (last 20):** {len(hits)} hits / {len(misses)} misses",
            "",
            "### By Regime",
        ]

        for regime, rdata in stats.get("by_regime", {}).items():
            total_r = rdata["hits"] + rdata["misses"]
            if total_r > 0:
                acc = round(rdata["hits"] / total_r * 100, 1)
                lines.append(f"- **{regime}:** {acc}% accuracy ({total_r} trades)")

        lines += [
            "",
            "### By Confidence Level",
        ]
        for conf, cdata in stats.get("by_confidence", {}).items():
            total_c = cdata["hits"] + cdata["misses"]
            if total_c > 0:
                acc = round(cdata["hits"] / total_c * 100, 1)
                lines.append(f"- **{conf}:** {acc}% accuracy ({total_c} trades)")

        lines += [
            "",
            "---",
            "",
            "## What I Got Right",
        ]
        for r in hits[-5:]:
            lines.append(f"- **{r['ticker']}** ({r.get('direction','?').upper()}) — {r.get('regime','?')} — Score: {r.get('score','?')}")

        lines += [
            "",
            "## What I Got Wrong",
        ]
        for r in misses[-5:]:
            first_outcome = next(iter(r.get("outcomes", {}).values()), {})
            move = first_outcome.get("pct_move", "?")
            lines.append(
                f"- **{r['ticker']}** ({r.get('direction','?').upper()}) — {r.get('regime','?')} — "
                f"Actual move: {move}% | My confidence: {r.get('confidence','?')}"
            )

        lines += [
            "",
            "---",
            "",
            "## Lessons & Patterns Detected",
        ]

        if analysis.get("status") == "insufficient_data":
            lines.append("*Not enough data yet — keep logging decisions.*")
        else:
            for lesson in analysis.get("lessons", []):
                severity = "⚠️" if "CRITICAL" in lesson or "wrong" in lesson.lower() else "•"
                lines.append(f"{severity} {lesson}")

        lines += [
            "",
            "---",
            "",
            "## What I Will Do Differently",
            "> *(Fill this in manually — this is your most important section)*",
            "",
            "1. ",
            "2. ",
            "3. ",
            "",
            "## Open Questions",
            "> *(What am I still unsure about? What needs more data?)*",
            "",
            "- ",
            "",
            "---",
            f"*Auto-generated by ARIA on {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        ]

        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path)

    # ── Append to lessons-learned file ────────────────────────────────────────

    def append_lesson(self, lesson: str, category: str = "General", source_ticker: str = ""):
        """
        Append a distilled lesson to the persistent lessons-learned file.
        This file is the 'wisdom' file ARIA reads at the start of every session.
        """
        path = self.vault / self.LESSONS_FILE
        today = datetime.now().strftime("%Y-%m-%d")
        source = f" (from {source_ticker})" if source_ticker else ""

        entry = f"- [{today}]{source} {lesson}\n"

        if path.exists():
            existing = path.read_text(encoding="utf-8")

            # Route to right section
            section_map = {
                "regime": "## Regime-Specific Lessons",
                "ticker": "## Ticker-Specific Lessons",
                "positive": "## Positive Patterns (What Works)",
            }
            target_section = section_map.get(category.lower(), "## Critical Lessons")

            if target_section in existing:
                existing = existing.replace(
                    target_section,
                    target_section + "\n" + entry,
                    1,
                )
                path.write_text(existing, encoding="utf-8")
            else:
                with path.open("a", encoding="utf-8") as f:
                    f.write(f"\n{entry}")
        else:
            self._ensure_folders()
            self.append_lesson(lesson, category, source_ticker)

    # ── Read lessons for ARIA context injection ───────────────────────────────

    def read_lessons(self) -> str:
        """Read the lessons file for injection into ARIA system prompt."""
        path = self.vault / self.LESSONS_FILE
        if path.exists():
            return path.read_text(encoding="utf-8")
        return "[No lessons recorded yet]"
