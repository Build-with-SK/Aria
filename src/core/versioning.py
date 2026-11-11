"""
src/core/versioning.py
======================
A model version that identifies the code that made a prediction.

A prediction is only reproducible if the record says which logic produced it.
A timestamp says when, not what; a weights version says which multipliers, not
which modules. This hashes the package source itself, so two predictions with
the same fingerprint were made by byte-identical code (line endings
normalised, so the Windows laptop and the Mac mini agree on identical files).

It is deliberately not a git SHA: the running process may hold uncommitted
edits, and the record must describe what actually ran.
"""
from __future__ import annotations

import hashlib
import threading
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
_lock = threading.Lock()


@lru_cache(maxsize=32)
def _fingerprint(package: str) -> str:
    base = ROOT / package
    h = hashlib.sha256()
    files = sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts) \
        if base.is_dir() else ([base] if base.exists() else [])
    if not files:
        return "unknown"
    for p in files:
        h.update(p.relative_to(ROOT).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))
        h.update(b"\0")
    return h.hexdigest()[:12]


def code_fingerprint(package: str) -> str:
    """12-hex fingerprint of a package directory (or single file) under the
    repo root, e.g. "src/v5". Cached for the life of the process — the code a
    process runs does not change underneath it."""
    with _lock:
        return _fingerprint(package)


def model_version(name: str, package: str, **parts) -> str:
    """"v5|code=ab12cd34ef56|weights=3" — name, code fingerprint, then any
    further named parts in a fixed order so equal inputs give equal strings."""
    extra = "".join(f"|{k}={parts[k]}" for k in sorted(parts) if parts[k] is not None)
    return f"{name}|code={code_fingerprint(package)}{extra}"
