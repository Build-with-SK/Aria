"""
Vault read containment.

`/api/vault/note?path=` exposes `VaultIndex.read_note` to anything that can
reach the API. If containment is wrong, that endpoint becomes an arbitrary file
read — .env, SSH keys, anything the server process can open.

The original check was `str(path).startswith(str(vault))`, which passes for a
SIBLING directory whose name merely begins with the vault's name. These tests
pin the corrected behaviour.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.brain.vault import VaultIndex


class _Vault(VaultIndex):
    """read_note without the ChromaDB/embedding model construction."""

    def __init__(self, vault_path):
        self.vault = Path(vault_path)


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "DigitalBrain"
    (root / "01 - Trading").mkdir(parents=True)
    (root / "01 - Trading" / "note.md").write_text("inside the vault", encoding="utf-8")

    # A secret next to the vault, and a sibling whose name SHARES THE PREFIX.
    (tmp_path / "secret.env").write_text("ANTHROPIC_API_KEY=sk-do-not-leak", encoding="utf-8")
    sibling = tmp_path / "DigitalBrain-backup"
    sibling.mkdir()
    (sibling / "private.md").write_text("private sibling note", encoding="utf-8")
    return _Vault(root)


def test_a_note_inside_the_vault_reads(vault):
    assert vault.read_note("01 - Trading/note.md") == "inside the vault"


def test_parent_traversal_is_refused(vault):
    assert vault.read_note("../secret.env") is None
    assert vault.read_note("../../secret.env") is None
    assert vault.read_note("01 - Trading/../../secret.env") is None


def test_prefix_sibling_directory_is_refused(vault):
    """The exact hole the old startswith check left open."""
    assert vault.read_note("../DigitalBrain-backup/private.md") is None


def test_absolute_path_is_refused(vault, tmp_path):
    assert vault.read_note(str(tmp_path / "secret.env")) is None


def test_a_directory_is_not_readable_as_a_note(vault):
    assert vault.read_note("01 - Trading") is None


def test_missing_note_returns_none(vault):
    assert vault.read_note("nope.md") is None


def test_malformed_path_returns_none_rather_than_raising(vault):
    for bad in ("\x00", "con:/x", "?" * 300):
        assert vault.read_note(bad) is None


def test_no_absolute_machine_path_is_hardcoded_in_source():
    """A literal like C:\\Users\\<name>\\... leaks a username into a public repo
    and breaks for every other user.

    This asserts on the SOURCE, not on the resolved value: the resolved default
    legitimately lands beside the repo, which on the author's machine is under
    their home directory. What must not exist is the literal.
    """
    import re
    root = Path(__file__).parent.parent
    drive_literal = re.compile(r"""["']?[A-Za-z]:\\\\?Users\\\\?""")
    for rel in ("src/brain/vault.py", "src/brain/brain_daemon.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert not drive_literal.search(src), f"{rel} hardcodes an absolute user path"


def test_the_vault_default_is_derived_from_the_repo_location(monkeypatch):
    monkeypatch.delenv("ARIA_VAULT_PATH", raising=False)
    import importlib

    from src.brain import vault as vault_mod
    importlib.reload(vault_mod)
    assert vault_mod.DEFAULT_VAULT == str(vault_mod.ROOT.parent / "DigitalBrain")


def test_the_vault_path_is_env_overridable(monkeypatch, tmp_path):
    monkeypatch.setenv("ARIA_VAULT_PATH", str(tmp_path / "elsewhere"))
    import importlib

    from src.brain import vault as vault_mod
    importlib.reload(vault_mod)
    assert vault_mod.DEFAULT_VAULT == str(tmp_path / "elsewhere")
    importlib.reload(vault_mod)      # leave the module as found
