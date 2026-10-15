"""Regression: CompressionStore() with no explicit path must resolve
DEFAULT_DB at call time, not at import time.

store.py used to default to `db_path: Path | str = DEFAULT_DB`, which bakes
the real production path into the function signature the moment the module
is imported. Any later redirect of `store.DEFAULT_DB` (as the isolation guard
does for every test session) was then silently ignored, and the guard's own
assertions were the only thing standing between the suite and a write into
data/compression_store.db.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.compression import store as store_mod
from src.compression.store import CompressionStore


def test_default_path_is_resolved_at_instantiation(tmp_path, monkeypatch):
    redirected = tmp_path / "redirected_compression_store.db"
    monkeypatch.setattr(store_mod, "DEFAULT_DB", redirected)

    store = store_mod.CompressionStore()
    assert store.db_path == redirected

    sid = store.put("stub", "hello from the redirected store")
    assert store.get(sid) == "hello from the redirected store"

    assert redirected.exists()

    real_default = (Path(__file__).resolve().parent.parent
                     / "data" / "compression_store.db")
    assert store.db_path.resolve() != real_default.resolve()


def test_explicit_path_still_overrides_default(tmp_path):
    explicit = tmp_path / "explicit.db"
    store = CompressionStore(explicit)
    assert store.db_path == explicit
