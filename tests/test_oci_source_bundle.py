from pathlib import Path

from scripts.oci_source_bundle import allowed


def test_data_package_is_source_but_runtime_state_is_not():
    assert allowed(Path("src/data/providers.py"), "aria")
    assert not allowed(Path("data/positions.json"), "aria")
    assert not allowed(Path("data/sentinel/conversations.jsonl"), "sentinel")


def test_credentials_and_environment_are_excluded_in_source_directories():
    for name in ("src/private.key", "face/.env", "scripts/keys.json", "src/credentials.json"):
        assert not allowed(Path(name), "sentinel")


def test_unrelated_projects_and_environments_are_not_bundled():
    for name in ("venv/lib/site.py", "frontend/node_modules/pkg/index.js", "private-notes/note.md"):
        assert not allowed(Path(name), "aria")
    assert allowed(Path("sentinel/anatomy.py"), "sentinel")
