"""
src/research/
=============
ARIA's reach into the open internet. Read anything by URL, or sweep every
source at once for leads; every retrieval is archived so it can be cited.

    from src.research import read, hunt, status

    read("https://reddit.com/r/wallstreetbets/comments/...")   # one thing
    hunt("NVDA supply constraint", ticker="NVDA")              # everything

The hand fetches on demand; the eye (src/research/eye.py) stays open, looks
on its own cadence, and reports only what changed:

    from src.research.eye import watch, blink, briefing

Sources: web (Jina), RSS/Atom, YouTube, GitHub, Reddit, StockTwits, SEC
EDGAR, Google News, Hacker News — all free and unauthenticated — plus X and
Meta, which activate only with an official API credential.

Design borrowed from Panniantong/agent-reach (MIT): ordered backends per
source, real probes instead of shutil.which(), and an SSRF guard on every
untrusted URL.
"""
from .base import Document, Source, SourceError
from .eye import Observation, Watch, blink, briefing, watch, watches
from .leads import Sweep, hunt
from .reach import read, read_feed, route, status
from .store import archive, citation, load, records
from .url import normalize_public_url

__all__ = [
    "Document",
    "Observation",
    "Source",
    "SourceError",
    "Sweep",
    "Watch",
    "archive",
    "blink",
    "briefing",
    "citation",
    "hunt",
    "load",
    "normalize_public_url",
    "read",
    "read_feed",
    "records",
    "route",
    "status",
    "watch",
    "watches",
]
