"""
tests/test_path_traversal.py
============================
User-controlled paths must not escape the directory they belong to.

Two places in ARIA take a path-like string from a request and turn it into a
filesystem read or write:

    /api/vault/note?path=      reads a note out of the owner's Obsidian vault
    /api/daily-report?date=    the date IS the filename

Both are owner-only, so a traversal here is not a public disclosure — but
"authenticated" is not a containment strategy, and the vault is the most
personal data in the system. These tests attack both.

WHY `is_relative_to` AND NOT `startswith`
-----------------------------------------
A prefix test passes for a SIBLING directory whose name merely begins with the
vault's: `…/DigitalBrain-backup` satisfies `startswith("…/DigitalBrain")`. That
is not a hypothetical — it is one directory name away from being true on this
machine, where a `DigitalBrain_Backup_20260416_150646` already exists next to
the vault.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """A vault with a secret next to it, and a sibling that shares its prefix."""
    root = tmp_path / "DigitalBrain"
    (root / "01 - Trading").mkdir(parents=True)
    (root / "note.md").write_text("a real note", encoding="utf-8")
    (root / "01 - Trading" / "nested.md").write_text("nested note", encoding="utf-8")

    (tmp_path / "secret.txt").write_text("PRIVATE", encoding="utf-8")
    sibling = tmp_path / "DigitalBrain-backup"
    sibling.mkdir()
    (sibling / "leaked.md").write_text("SHOULD NOT BE READABLE", encoding="utf-8")

    from src.brain import vault as vault_mod
    monkeypatch.setattr(vault_mod, "get_vault_path", lambda: root, raising=False)

    class _V:
        def __init__(self):
            self.vault = root
        read_note = vault_mod.VaultIndex.read_note
    return _V(), tmp_path


# ── the vault ──────────────────────────────────────────────────────────────

def test_a_normal_note_reads(vault):
    v, _ = vault
    assert v.read_note("note.md") == "a real note"


def test_a_nested_note_reads(vault):
    v, _ = vault
    assert v.read_note("01 - Trading/nested.md") == "nested note"


@pytest.mark.parametrize("attack", [
    "../secret.txt",
    "../../secret.txt",
    "01 - Trading/../../secret.txt",
    "./../secret.txt",
    "..\\secret.txt",
    "....//secret.txt",
])
def test_dot_dot_traversal_is_refused(vault, attack):
    v, _ = vault
    assert v.read_note(attack) is None, f"{attack!r} escaped the vault"


def test_an_absolute_path_is_refused(vault):
    v, tmp = vault
    assert v.read_note(str(tmp / "secret.txt")) is None
    assert v.read_note("/etc/passwd") is None
    assert v.read_note("C:\\Windows\\win.ini") is None


def test_a_sibling_directory_sharing_the_prefix_is_refused(vault):
    """The `startswith` bug, made concrete.

    `DigitalBrain-backup` begins with `DigitalBrain`. A prefix check would let
    this through; `is_relative_to` does not.
    """
    v, _ = vault
    assert v.read_note("../DigitalBrain-backup/leaked.md") is None


def test_a_nonexistent_file_is_none_not_an_error(vault):
    v, _ = vault
    assert v.read_note("does-not-exist.md") is None


def test_a_directory_is_not_readable_as_a_note(vault):
    v, _ = vault
    assert v.read_note("01 - Trading") is None


@pytest.mark.parametrize("junk", ["", "   ", "\x00", "a" * 400, "…/…", "\n"])
def test_malformed_input_returns_none_rather_than_raising(vault, junk):
    """A 300-character name reaches stat() and raises ENAMETOOLONG on Linux
    where Windows fails earlier — every step has to stay inside the guard or
    the API 500s on one platform and 404s on another."""
    v, _ = vault
    assert v.read_note(junk) is None


def test_containment_is_checked_with_is_relative_to_not_a_prefix():
    src = Path("src/brain/vault.py").read_text(encoding="utf-8")
    body = src[src.index("def read_note"):]
    body = body[:body.index("\n    def ", 5)]
    assert "is_relative_to" in body, (
        "containment is no longer checked with is_relative_to — a startswith "
        "test passes for a sibling directory sharing the vault's name")
    assert ".resolve()" in body, "the path is not canonicalised before checking"


# ── the daily report: the date is the filename ─────────────────────────────

@pytest.mark.parametrize("attack", [
    "../../etc/passwd", "../secret", "2026-08-27/../../x", "..",
    "2026-08-27.json", "/etc/passwd", "C:\\Windows\\win.ini",
    "2026-13-45", "today", "%2e%2e%2f", "..%2f..%2fsecret",
])
def test_the_report_date_refuses_anything_that_is_not_a_date(attack):
    from src.report import daily
    with pytest.raises(ValueError):
        daily.get(attack)
    with pytest.raises(ValueError):
        daily.generate(attack)


def test_a_valid_date_is_accepted_and_stays_inside_the_store(tmp_path, monkeypatch):
    from src.report import daily
    monkeypatch.setattr(daily, "REPORTS", tmp_path / "reports")
    got = daily.get("2026-08-27")
    assert got["date"] == "2026-08-27"
    # Nothing was created outside the store.
    assert not (tmp_path / "etc").exists()


def test_the_date_guard_is_a_real_date_parse_not_a_regex():
    """A regex on `\\d{4}-\\d{2}-\\d{2}` accepts 2026-13-45. `date.fromisoformat`
    does not, and being unable to name a file is the point."""
    src = Path("src/report/daily.py").read_text(encoding="utf-8")
    body = src[src.index("def _valid_day"):]
    body = body[:body.index("\ndef ", 5)]
    assert "fromisoformat" in body


# ── argv injection: no shell, but argv is still an interface ───────────────

@pytest.mark.parametrize("bad", [
    "--help", "-v", "--verbose", "", "   ", "a" * 200,
    "model; rm -rf /", "model && cat /etc/passwd", "model\nother",
    "../../etc/passwd", "$(whoami)", "`id`",
])
def test_a_model_name_that_is_not_a_model_name_is_refused(bad):
    """`ollama pull` is called with `shell=False` and an argv list, so this was
    never command injection. It was ARGUMENT injection: a name starting with a
    dash is read by ollama as a flag."""
    from fastapi import HTTPException
    import backend.main as m
    with pytest.raises(HTTPException) as e:
        m.pull_model(bad)
    assert e.value.status_code == 400


@pytest.mark.parametrize("good", [
    "llama3.1:8b", "qwen2.5:7b-instruct-q4_K_M", "gemma3:4b",
    "library/mistral:latest", "nomic-embed-text",
])
def test_real_model_names_still_pass(good, monkeypatch):
    import backend.main as m
    launched = []
    class _P:
        def __init__(self, argv, **kw):
            launched.append(argv)
    monkeypatch.setattr("subprocess.Popen", _P)
    out = m.pull_model(good)
    assert out["model"] == good
    assert launched and launched[0][:2] == ["ollama", "pull"]


def test_no_shell_is_ever_used_for_a_subprocess():
    """`shell=True` anywhere near a request parameter is the real hole. There
    should be none at all."""
    import re
    for path in list(Path("backend").rglob("*.py")) + list(Path("src").rglob("*.py")):
        text = path.read_text(encoding="utf-8-sig")
        assert not re.search(r"shell\s*=\s*True", text), (
            f"{path} launches a subprocess through a shell")


def test_the_pipeline_runner_never_takes_user_text():
    """`/api/run` accepts a bool, not a string, so nothing a caller types
    reaches argv."""
    import inspect
    import backend.main as m
    sig = inspect.signature(m.trigger_run)
    # `from __future__ import annotations` makes annotations strings, so compare
    # by name rather than identity.
    annotation = sig.parameters["no_sentiment"].annotation
    assert annotation in (bool, "bool"), (
        f"/api/run's no_sentiment is {annotation!r}, not a bool — a string "
        f"parameter here would put caller text straight into argv")
    src = inspect.getsource(m.trigger_run)
    assert 'args = "--no-sentiment" if no_sentiment else ""' in src, (
        "the pipeline args are no longer a fixed literal chosen by a bool")
