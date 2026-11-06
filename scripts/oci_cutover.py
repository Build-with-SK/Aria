"""Apply a final verified state snapshot after local writers have stopped.

Prior cloud data directories are retained for rollback. External credentials
are not transferred. Live trading and automatic execution stay disabled.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tarfile

from oci_restore import ROOT, PRIVATE, configure_paths, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    if (PRIVATE / "cutover-complete.json").exists():
        raise SystemExit("Cutover already applied; refusing to replace cloud state")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stage = Path.home() / "deploy" / ("final-state-" + stamp)
    stage.mkdir(mode=0o700)
    with tarfile.open(args.archive) as archive:
        manifest = json.loads(archive.extractfile("MANIFEST.json").read())
        for name, meta in manifest["files"].items():
            path = Path(name)
            member = archive.getmember(name)
            if path.is_absolute() or ".." in path.parts or path.parts[:2] not in (("aria", "data"), ("sentinel", "data")) or not member.isfile():
                raise ValueError("Unexpected state member")
            if hashlib.sha256(archive.extractfile(member).read()).hexdigest() != meta["sha256"]:
                raise ValueError("Final state checksum mismatch")
        archive.extractall(stage, members=[archive.getmember(n) for n in manifest["files"]], filter="data")
    for name in manifest["sqlite_snapshots"]:
        with sqlite3.connect(f"file:{stage / name}?mode=ro", uri=True) as db:
            if db.execute("pragma quick_check").fetchone()[0] != "ok":
                raise ValueError("Final database check failed")
    subprocess.run(["sudo", "-n", "systemctl", "stop", "aria", "sentinel"], check=True)
    rollback = Path.home() / "deploy" / ("pre-cutover-" + stamp)
    rollback.mkdir(mode=0o700)
    for app in ("aria", "sentinel"):
        old, new = ROOT / app / "data", stage / app / "data"
        if (old / "models").exists():
            if (new / "models").exists():
                raise RuntimeError("Unexpected models in state backup")
            (old / "models").rename(new / "models")
        old.rename(rollback / app)
        new.rename(old)
    configure_paths()
    for app, replacements in {
        "aria": {"ARIA_TESTING": "0", "ARIA_RUN_BRAIN": "true"},
        "sentinel": {"SENTINEL_LIGHT_START": "0"},
    }.items():
        path = PRIVATE / (app + ".env")
        lines = path.read_text().splitlines()
        result = [line.split("=", 1)[0] + "=" + replacements[line.split("=", 1)[0]]
                  if line.split("=", 1)[0] in replacements else line for line in lines]
        path.write_text("\n".join(result) + "\n")
        path.chmod(0o600)
    write_json(PRIVATE / "cutover-complete.json", {
        "utc": stamp, "files": len(manifest["files"]), "sqlite_checks": len(manifest["sqlite_snapshots"]),
        "rollback": str(rollback), "live_trading": False, "automatic_execution": False,
    })
    subprocess.run(["sudo", "-n", "systemctl", "start", "aria", "sentinel"], check=True)
    print(json.dumps({"cutover": "applied", "files": len(manifest["files"]),
                      "sqlite_checks": len(manifest["sqlite_snapshots"]), "prior_cloud_state": "retained"}))


if __name__ == "__main__":
    main()
