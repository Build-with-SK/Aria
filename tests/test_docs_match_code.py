"""
tests/test_docs_match_code.py
=============================
The README must describe THIS repository, not a previous one.

Documentation drifts silently and nothing fails, which is why it is the part of
a project most likely to be lying by the time someone reads it. This README had
a "twelve destinations" table listing pages that had been deleted two
consolidations earlier — accurate when written, wrong for months, and invisible
because no test looked.

So these tests read the README and check its claims against the code: routes it
documents must be registered, files it names must exist, pages it lists must be
in the rail, and pages that were deleted must not reappear. They are cheap and
they fail the moment the story stops being coherent.

They deliberately do NOT check prose, tone or completeness. A README can be
thin and still be true; it cannot be specific and wrong.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

pytestmark = pytest.mark.skipif(not README.exists(), reason="no README")


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def _ro(db: Path):
    """Read-only connection — tests/conftest.py refuses a writable one."""
    return sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)


# ── everything the README points at must exist ─────────────────────────────

def test_every_documented_endpoint_is_registered():
    import backend.main as m
    registered = {r.path.rstrip("/") for r in m.app.routes}
    documented = {e.rstrip("/") for e in
                  re.findall(r"`(?:GET|POST) (/api/[^`?]+)", _readme())}
    assert documented, "the README documents no endpoints at all"
    missing = sorted(documented - registered)
    assert not missing, f"the README documents endpoints that do not exist: {missing}"


def test_every_named_source_file_exists():
    named = set(re.findall(r"`(src/[a-z_/]+\.py)`", _readme()))
    missing = sorted(p for p in named if not (ROOT / p).exists())
    assert not missing, f"the README names files that do not exist: {missing}"


def test_every_named_page_exists():
    named = set(re.findall(r"`(?:pages|frontend/src)/([A-Za-z]+)\.jsx`", _readme()))
    missing = sorted(p for p in named
                     if not (ROOT / "frontend/src/pages" / f"{p}.jsx").exists())
    assert not missing, f"the README names pages that do not exist: {missing}"


# ── and must not describe what was removed ─────────────────────────────────

def _destinations_table() -> str:
    """Just the table of places a user can go — not the prose around it.

    Scoped deliberately. The README EXPLAINS that Live Mind was removed and why,
    which is useful history and must not fail this test; what must never happen
    is a deleted surface being listed as somewhere you can still go. Explaining
    a removal and offering it are opposite things.
    """
    readme = _readme()
    start = readme.find("## The eight destinations")
    if start < 0:
        return ""
    end = readme.find("\n## ", start + 10)
    return readme[start:end if end > 0 else len(readme)]


@pytest.mark.parametrize("dead", [
    "BrainHub", "Intelligence", "Thinking", "Signals",
    "Recommendations", "Quant Lab", "Live Mind", "Command", "Stress",
])
def test_no_deleted_surface_is_listed_as_a_destination(dead):
    """Only the NAME column counts.

    "Signals" appears in the Market row as one of the things Market knows, and
    that is the architecture working — signals are a capability, not a place to
    visit. What must not happen is `**Signals**` appearing as a destination in
    its own right. So this reads the bold names, not the descriptions.
    """
    table = _destinations_table()
    assert table, "the README has no destinations table to check"
    names = re.findall(r"\|\s*\*\*([^*]+)\*\*\s*\|", table)
    assert names, "the destinations table has no bold destination names"
    assert dead not in names, (
        f"the destinations table offers '{dead}' as a place to go, and it no "
        f"longer exists. Current destinations: {names}")


def test_deleted_pages_are_only_ever_mentioned_in_the_past_tense():
    """A deleted page may appear in the README as history — never as a file
    the reader is invited to open."""
    readme = _readme()
    for dead in ("BrainHub.jsx", "Intelligence.jsx", "Thinking.jsx",
                 "Signals.jsx", "Recommendations.jsx", "QuantLab.jsx"):
        assert f"`{dead}`" not in readme and f"`pages/{dead}`" not in readme, (
            f"the README points at {dead} as if it were a file to open")


def test_the_destination_table_matches_the_rail():
    """The single most drift-prone thing in the file."""
    sidebar = (ROOT / "frontend/src/components/Sidebar.jsx").read_text(encoding="utf-8")
    nav = [l for l in re.findall(r"label: '([A-Z ]+)'", sidebar) if l.strip()]
    assert nav, "could not read the rail"
    readme = _readme()
    for label in nav:
        assert label in readme or label.title() in readme, (
            f"the rail has a '{label}' destination the README never mentions")
    count_words = {7: "seven", 8: "eight", 9: "nine", 10: "ten"}
    word = count_words.get(len(nav))
    if word:
        assert re.search(rf"\b{word}\b", readme, re.I), (
            f"the rail has {len(nav)} destinations; the README does not say "
            f"'{word}'")


def test_every_registered_worker_is_documented():
    src = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    workers = re.findall(r'workers\.register\("([a-z_]+)"', src)
    assert workers, "no workers are registered"
    readme = _readme()
    missing = sorted(w for w in workers if w not in readme)
    assert not missing, f"registered workers absent from the README: {missing}"


# ── numbers ────────────────────────────────────────────────────────────────

def test_the_symbol_and_venue_counts_are_not_overstated():
    db = ROOT / "data" / "universe.db"
    if not db.exists():
        pytest.skip("no symbol master in this checkout")
    with _ro(db) as conn:
        n, venues = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT exchange) FROM symbols").fetchone()
    claimed = re.search(r"([\d,]+)\+? symbols across (\d+) venues", _readme())
    if not claimed:
        pytest.skip("the README makes no symbol/venue claim")
    claimed_n = int(claimed.group(1).replace(",", ""))
    claimed_v = int(claimed.group(2))
    assert n >= claimed_n, f"README claims {claimed_n} symbols; there are {n}"
    assert claimed_v == venues, f"README claims {claimed_v} venues; there are {venues}"


def test_quoted_ledger_figures_are_dated_rather_than_presented_as_current():
    """A count that decays is worse than no count.

    The ledger grows every day the daemons run, so any exact figure in a README
    is wrong within a week. Requiring the README to match exactly would mean a
    weekly documentation edit and a red suite in between — so the rule is that
    a quoted figure must be DATED. A dated snapshot stays true forever; an
    undated one silently becomes a lie.
    """
    readme = _readme()
    for m in re.finditer(r"([\d,]+) predictions, ([\d,]+) resolved", readme):
        window = readme[max(0, m.start() - 160):m.start()]
        assert re.search(r"as of \d{4}-\d{2}-\d{2}", window, re.I), (
            f"the README quotes '{m.group(0)}' with no 'as of <date>' before "
            f"it. Ledger counts move; an undated one becomes false on its own.")


def test_the_readme_verdict_matches_what_calibration_actually_says():
    """The COUNT may age. The claim must not.

    "Indistinguishable from chance" is the part a reader acts on, and it is
    computed, not written — so if the record ever does separate from chance,
    this fails and the README has to be rewritten rather than quietly left
    understating the system.
    """
    db = ROOT / "data" / "aria_core.db"
    if not db.exists():
        pytest.skip("no ledger in this checkout")
    try:
        from src.core.calibration import summary_line
        live = summary_line().lower()
    except Exception as e:
        pytest.skip(f"calibration unavailable: {e}")

    readme = _readme().lower()
    if "indistinguishable from chance" in readme:
        assert ("indistinguishable" in live or "not measurable" in live
                or "no resolved" in live), (
            f"the README claims no demonstrated edge, but calibration now says: "
            f"{live}. Update the README — this is the good kind of failure.")


def test_the_module_count_is_real():
    import src.v5.modules  # noqa: F401  — registration happens on import
    from src.v5.registry import REGISTRY
    m = re.search(r"(\d+) independently-callable modules", _readme())
    if not m:
        pytest.skip("the README makes no module-count claim")
    assert int(m.group(1)) == len(REGISTRY), (
        f"README claims {m.group(1)} modules; {len(REGISTRY)} are registered")


def test_the_app_base_path_matches_the_vite_config():
    """`/app/` has to be true in three places or deep links 404."""
    vite = (ROOT / "frontend" / "vite.config.js").read_text(encoding="utf-8")
    assert "base: '/app/'" in vite
    backend = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    assert 'app.mount("/app"' in backend, "the backend no longer mounts /app"
    assert "/app/" in _readme(), (
        "the README does not tell the reader the app lives under /app/, which "
        "is the single most common way to lose ten minutes on this repo")
