"""
src/research/sources/youtube.py
===============================
YouTube transcripts via yt-dlp.

Transcripts only — metadata and subtitles, never the video. That keeps this
cheap and keeps the artifact something a report can quote. Earnings calls,
central bank pressers and analyst interviews are the reason this source
exists at all.

yt-dlp is not installed in the venv today, so available() reports why and
fetch() raises a SourceError naming the fix rather than a stack trace. This
source degrades; it never breaks a research run.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..base import Document, Source, SourceError, probe_command
from ..url import host_matches, normalize_public_url

TIMEOUT = 120
SUB_LANGS = "en,en-US,en-GB"
INSTALL_HINT = "install with: venv\\Scripts\\python.exe -m pip install yt-dlp"

# Matches "00:00:03.000 --> 00:00:07.000" and the cue-setting suffix after it.
_TIMESTAMP = re.compile(r"^\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->")
_TAGS = re.compile(r"<[^>]+>")


def _ytdlp_argv() -> list[str] | None:
    """Prefer a real yt-dlp binary, fall back to the module in this venv."""
    binary = shutil.which("yt-dlp")
    if binary:
        return [binary]
    import sys
    if probe_command([sys.executable, "-m", "yt_dlp", "--version"], timeout=20):
        return [sys.executable, "-m", "yt_dlp"]
    return None


def _vtt_to_text(vtt: str) -> str:
    """Strip WebVTT scaffolding down to deduplicated spoken lines.

    Auto-generated captions repeat each line as the rolling window advances,
    so consecutive duplicates are collapsed.
    """
    lines: list[str] = []
    for raw in vtt.splitlines():
        line = _TAGS.sub("", raw).strip()
        if (
            not line
            or line == "WEBVTT"
            or _TIMESTAMP.match(line)
            or line.startswith(("Kind:", "Language:", "NOTE", "STYLE"))
            or line.isdigit()
        ):
            continue
        if not lines or lines[-1] != line:
            lines.append(line)
    return "\n".join(lines)


class YouTubeSource(Source):
    name = "youtube"
    description = "YouTube video metadata and transcript"
    backends = ["yt-dlp"]
    zero_config = False

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "youtube.com", "youtu.be", "m.youtube.com")

    def available(self) -> tuple[bool, str]:
        if _ytdlp_argv() is None:
            return False, f"yt-dlp not found — {INSTALL_HINT}"
        return True, "yt-dlp"

    def fetch(self, url: str) -> Document:
        clean = normalize_public_url(url)
        base = _ytdlp_argv()
        if base is None:
            raise SourceError(f"yt-dlp not available for {clean} — {INSTALL_HINT}")

        with tempfile.TemporaryDirectory(prefix="aria-yt-") as tmp:
            outdir = Path(tmp)
            argv = base + [
                "--skip-download",
                "--write-sub",
                "--write-auto-sub",
                "--sub-lang", SUB_LANGS,
                "--sub-format", "vtt",
                "--dump-json",
                "--no-playlist",
                "--no-warnings",
                "-o", str(outdir / "%(id)s.%(ext)s"),
                clean,
            ]
            try:
                proc = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=TIMEOUT,
                    shell=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise SourceError(f"yt-dlp failed on {clean}: {exc}") from exc

            if proc.returncode != 0:
                detail = (proc.stderr or "").strip().splitlines()
                raise SourceError(
                    f"yt-dlp exited {proc.returncode} on {clean}: "
                    f"{detail[-1] if detail else 'no detail'}"
                )

            try:
                info = json.loads((proc.stdout or "").strip().splitlines()[0])
            except (ValueError, IndexError) as exc:
                raise SourceError(f"yt-dlp returned no metadata for {clean}") from exc

            transcript = ""
            for vtt in sorted(outdir.glob("*.vtt")):
                transcript = _vtt_to_text(vtt.read_text(encoding="utf-8", errors="replace"))
                if transcript:
                    break

        title = info.get("title") or clean
        body = transcript or "(no transcript available for this video)"
        return Document(
            url=clean,
            title=title,
            text=f"# {title}\n\n{body}",
            source=self.name,
            backend="yt-dlp",
            meta={
                "video_id": info.get("id", ""),
                "channel": info.get("channel") or info.get("uploader", ""),
                "upload_date": info.get("upload_date", ""),
                "duration_s": info.get("duration"),
                "has_transcript": bool(transcript),
            },
        )
