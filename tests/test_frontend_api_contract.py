"""
tests/test_frontend_api_contract.py
===================================
Every API path the frontend calls must exist in the backend, and the fields
the provenance-bearing views read must be in the responses that feed them.

The frontend has no test runner (and the project adds no npm packages), so the
frontend/backend boundary is checked from here: the paths are read out of
frontend/src and matched against the live FastAPI route table — the same
regexes Starlette routes with — rather than against strings in main.py.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SRC = ROOT / "frontend" / "src"
pytestmark = pytest.mark.skipif(not SRC.exists(), reason="frontend not present")

PATH_RE = re.compile(r"""['"`](/api/[^'"`\s]*)""")


def _frontend_paths() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for f in SRC.rglob("*.js*"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        for raw in PATH_RE.findall(text):
            found.setdefault(raw, []).append(f.relative_to(ROOT).as_posix())
    return found


def _concrete(raw: str) -> str:
    """`/api/x/${encodeURIComponent(s)}?q=${q}` -> `/api/x/SAMPLE`."""
    path = raw.split("?", 1)[0]
    path = re.sub(r"\$\{[^}]*\}?[^/]*", "SAMPLE", path)
    return path.rstrip("/") or "/"


def _route_regexes():
    from backend.main import app
    out = []
    for r in app.routes:
        rx = getattr(r, "path_regex", None)
        if rx is not None and getattr(r, "path", "").startswith("/api"):
            out.append((r.path, rx))
    return out


def test_every_api_path_the_frontend_calls_exists():
    routes = _route_regexes()
    paths = _frontend_paths()
    assert len(paths) > 50, f"only {len(paths)} API paths found — extraction is broken"
    missing = {}
    for raw, files in sorted(paths.items()):
        concrete = _concrete(raw)
        if concrete.endswith("SAMPLE") and concrete.count("/") <= 2:
            continue                      # a bare template prefix, not a route
        if not any(rx.match(concrete) for _, rx in routes):
            missing[raw] = sorted(set(files))
    assert not missing, ("the frontend calls routes the backend does not have: "
                         + "; ".join(f"{k} (in {', '.join(v)})" for k, v in missing.items()))


def test_the_contract_test_can_fail():
    """A matcher that accepts everything would make the test above decorative."""
    routes = _route_regexes()
    assert not any(rx.match("/api/this-route-does-not-exist") for _, rx in routes)
    assert any(rx.match("/api/ledger/predictions") for _, rx in routes)


# ── fields the provenance views read ────────────────────────────────────────

def test_the_ledger_view_reads_fields_the_ledger_returns(tmp_path, monkeypatch):
    """TrackRecordLedger shows "said N%" only for a STATED probability and a
    "strength" for a mapped one. If the API stopped returning these fields the
    page would silently show neither — so assert the API carries them."""
    page = (SRC / "pages" / "TrackRecordLedger.jsx").read_text(encoding="utf-8")
    for field in ("probability_kind", "p_correct"):
        assert field in page
    monkeypatch.setenv("ARIA_CORE_DB", str(tmp_path / "c.db"))
    from src.core import bus, ledger
    monkeypatch.setattr(bus, "_initialised", False)
    monkeypatch.setattr(ledger, "_ready", False)
    ledger.record_prediction(subject="AAPL", claim="c", direction="bull",
                             p_correct=0.6, probability_kind="stated",
                             source="v5", announce=False)
    row = ledger.predictions(limit=1)[0]
    assert {"probability_kind", "p_correct", "model_version", "information_session",
            "regime_at", "regime_basis", "data_as_of"} <= set(row)
