"""
src/research/base.py
====================
The Source contract.

One idea, borrowed from agent-reach: a source declares an *ordered* list of
backends. backends[0] is preferred, the rest are fallbacks. When a scraper
rots — and they all rot — the fix is reordering a list, not rewriting a
caller. Nothing above this layer names a backend.

The second idea is that shutil.which() is not proof of health. A stale venv
shim answers which() and then fails to execute. available() must actually
run something cheap before a source claims to work.
"""
from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Document:
    """One fetched item, in the shape the store and the citation layer want."""
    url: str
    title: str
    text: str
    source: str                      # channel name: web / rss / youtube / github
    backend: str                     # what actually served it
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.text)


class SourceError(RuntimeError):
    """A source could not serve this URL. Carries the backend that failed."""


class Source(ABC):
    name: str = ""
    description: str = ""
    backends: list[str] = []
    #: True when the backend needs no binary, no key, and no login.
    zero_config: bool = False

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Does this URL belong to this source?"""

    @abstractmethod
    def fetch(self, url: str) -> Document:
        """Retrieve the URL. Raise SourceError if no backend can serve it."""

    def available(self) -> tuple[bool, str]:
        """(usable, human-readable reason). Override when a binary is needed."""
        return True, self.backends[0] if self.backends else "builtin"


def probe_command(argv: list[str], timeout: int = 10) -> bool:
    """Really execute a cheap command; True only if it exits 0.

    shell=False always — argv never comes from fetched content, and keeping
    it a list means it never could.
    """
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
            shell=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
