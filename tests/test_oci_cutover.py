"""Exercise the cutover's corruption gate and retained rollback state."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile

import pytest


@pytest.fixture
def cutover(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    import oci_restore
    spec = importlib.util.spec_from_file_location("oci_cutover_checked", scripts / "oci_cutover.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root, private = tmp_path / "apps", tmp_path / "private"
    private.mkdir()
    (tmp_path / "deploy").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for obj in (module, oci_restore):
        monkeypatch.setattr(obj, "ROOT", root)
        monkeypatch.setattr(obj, "PRIVATE", private)
    for app in ("aria", "sentinel"):
        data = root / app / "data"
        (data / "models").mkdir(parents=True)
        (data / "old-marker").write_text("keep for rollback")
        (data / "models/weights").write_bytes(b"weights")
        (private / f"{app}.env").write_text("ARIA_TESTING=1\nARIA_RUN_BRAIN=false\nSENTINEL_LIGHT_START=1\nARIA_LIVE_TRADING_ENABLED=false\nOWNER_SECRET=preserve\n")
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs: calls.append(command))
    return module, tmp_path, calls


def bundle(path, bad_hash=False, traversal=False):
    name = "../outside" if traversal else "aria/data/desk_config.json"
    files = {name: b'{"auto_execute":true,"reflex_enabled":true}', "sentinel/data/sentinel_config.json": b"{}"}
    manifest = {"files": {n: {"sha256": "bad" if bad_hash else hashlib.sha256(data).hexdigest()} for n, data in files.items()}, "sqlite_snapshots": []}
    files["MANIFEST.json"] = json.dumps(manifest).encode()
    with tarfile.open(path, "w:gz") as archive:
        for n, data in files.items():
            member = tarfile.TarInfo(n)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


@pytest.mark.parametrize("corruption", ["hash", "traversal"])
def test_rejects_bad_state_before_stopping_services(cutover, monkeypatch, corruption):
    module, root, calls = cutover
    archive = root / "state.tar.gz"
    bundle(archive, bad_hash=corruption == "hash", traversal=corruption == "traversal")
    monkeypatch.setattr(sys, "argv", ["cutover", str(archive)])
    with pytest.raises(ValueError):
        module.main()
    assert calls == []
    assert (module.ROOT / "aria/data/old-marker").is_file()


def test_cutover_keeps_rollback_models_credentials_and_execution_gates(cutover, monkeypatch):
    module, root, calls = cutover
    archive = root / "state.tar.gz"
    bundle(archive)
    monkeypatch.setattr(sys, "argv", ["cutover", str(archive)])
    module.main()
    marker = json.loads((module.PRIVATE / "cutover-complete.json").read_text())
    assert (Path(marker["rollback"]) / "aria/old-marker").read_text() == "keep for rollback"
    assert (module.ROOT / "aria/data/models/weights").read_bytes() == b"weights"
    desk = json.loads((module.ROOT / "aria/data/desk_config.json").read_text())
    assert desk["auto_execute"] is False and desk["reflex_enabled"] is False
    env = (module.PRIVATE / "aria.env").read_text()
    assert "OWNER_SECRET=preserve" in env and "ARIA_LIVE_TRADING_ENABLED=false" in env
    assert "ARIA_TESTING=0" in env
    assert calls[0][-3:] == ["stop", "aria", "sentinel"]
    assert calls[-1][-3:] == ["start", "aria", "sentinel"]
    with pytest.raises(SystemExit, match="already applied"):
        module.main()
