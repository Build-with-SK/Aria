"""
tests/test_backup.py
====================
The backup keeps state, never secrets, and a live database comes out whole.

Runs against a synthetic data tree: the owner's real data/ is never read or
written by this file.
"""
import sqlite3
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts import backup  # noqa: E402


@pytest.fixture
def tree(tmp_path, monkeypatch):
    aria = tmp_path / "aria" / "data"
    sent = tmp_path / "atlas"
    (sent / "sentinel").mkdir(parents=True)
    sdata = sent / "data"
    for d in (aria / "auth", aria / "cache", aria / "brain_memory" / "chromadb",
              aria / "models", aria / "logs", sdata / "sentinel"):
        d.mkdir(parents=True, exist_ok=True)

    # state that must survive
    (aria / "signals.json").write_text('{"AAPL": 1}')
    (sdata / "sentinel" / "conversations.jsonl").write_text('{"kind":"TURN"}\n')
    (aria / "models" / "adapter.bin").write_bytes(b"x" * 10)
    db = sqlite3.connect(aria / "brain_memory" / "chromadb" / "chroma.sqlite3")
    db.execute("create table t (v text)")
    db.executemany("insert into t values (?)", [(str(i),) for i in range(500)])
    db.commit()
    db.close()

    # secrets that must never survive
    secrets = {
        aria.parent / ".env": "ARIA_OWNER_TOKEN=real",
        aria / ".session_key": "k",
        aria / "auth" / "sessions.json": "{}",
        aria / "research_keys.json": '{"k": "v"}',
        sdata / "sentinel" / "keys.json": "{}",
        sdata / "sentinel" / ".session_key": "k",
        aria / "cloudflared-creds.json": "{}",
        aria / "owner_token.txt": "t",
    }
    for p, v in secrets.items():
        p.write_text(v)
    # regenerable noise
    (aria / "cache" / "big.bin").write_bytes(b"0" * 100)
    (aria / "logs" / "aria.log").write_text("log")
    (aria / "aria.log").write_text("log")

    monkeypatch.setattr(backup, "ROOT", tmp_path / "aria")
    monkeypatch.setenv("SENTINEL_HOME", str(sent))
    return {"aria": aria, "sentinel": sdata, "secrets": secrets,
            "out": tmp_path / "backups"}


def _names(archive):
    with tarfile.open(archive, "r:gz") as tar:
        return set(tar.getnames())


def test_state_is_kept(tree):
    a = backup.make_backup(dest=tree["out"], stamp="t1")
    names = _names(a)
    assert "aria/data/signals.json" in names
    assert "sentinel/data/sentinel/conversations.jsonl" in names
    assert "aria/data/brain_memory/chromadb/chroma.sqlite3" in names
    assert "MANIFEST.json" in names


def test_no_secret_is_ever_kept(tree):
    a = backup.make_backup(dest=tree["out"], stamp="t1")
    names = _names(a)
    for secret in tree["secrets"]:
        assert not any(n.endswith("/" + secret.name) or n == secret.name
                       for n in names), f"{secret.name} was archived"
    assert backup.verify(a)["ok"]


def test_caches_logs_and_models_are_skipped_by_default(tree):
    names = _names(backup.make_backup(dest=tree["out"], stamp="t1"))
    assert not any("/cache/" in n or n.endswith(".log") for n in names)
    assert not any("/models/" in n for n in names)
    names = _names(backup.make_backup(dest=tree["out"], stamp="t2",
                                      include_models=True))
    assert "aria/data/models/adapter.bin" in names


def test_a_sqlite_database_is_snapshotted_and_opens(tree, tmp_path):
    a = backup.make_backup(dest=tree["out"], stamp="t1")
    out = backup.restore(a, tmp_path / "restored")
    db = sqlite3.connect(out / "aria/data/brain_memory/chromadb/chroma.sqlite3")
    assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
    assert db.execute("select count(*) from t").fetchone()[0] == 500


def test_verify_catches_tampering(tree, tmp_path):
    a = backup.make_backup(dest=tree["out"], stamp="t1")
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(a, "r:gz") as src, tarfile.open(bad, "w:gz") as dst:
        for m in src.getmembers():
            data = src.extractfile(m).read()
            if m.name == "aria/data/signals.json":
                data = b'{"AAPL": 999}'
                m.size = len(data)
            import io
            dst.addfile(m, io.BytesIO(data))
    r = backup.verify(bad)
    assert not r["ok"] and any("signals.json" in p for p in r["problems"])


def test_retention_keeps_the_newest(tree):
    for i in range(5):
        backup.make_backup(dest=tree["out"], stamp=f"2026010{i}_000000", keep=3)
    kept = sorted(p.name for p in tree["out"].glob("server-backup-*.tar.gz"))
    assert kept == [f"server-backup-2026010{i}_000000.tar.gz" for i in (2, 3, 4)]


def test_refuses_to_write_inside_a_repository(tree):
    with pytest.raises(SystemExit):
        backup.make_backup(dest=backup.ROOT / "backups")


def test_restore_never_overwrites_a_non_empty_directory(tree, tmp_path):
    a = backup.make_backup(dest=tree["out"], stamp="t1")
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "x").write_text("mine")
    with pytest.raises(SystemExit):
        backup.restore(a, occupied)
    assert (occupied / "x").read_text() == "mine"


def test_restore_ignores_path_traversal(tree, tmp_path):
    evil = tmp_path / "evil.tar.gz"
    import io
    with tarfile.open(evil, "w:gz") as t:
        for name in ("../escape.txt", "/abs.txt", "ok/fine.txt"):
            info = tarfile.TarInfo(name)
            info.size = 1
            t.addfile(info, io.BytesIO(b"x"))
    out = backup.restore(evil, tmp_path / "r")
    assert (out / "ok" / "fine.txt").exists()
    assert not (tmp_path / "escape.txt").exists()
