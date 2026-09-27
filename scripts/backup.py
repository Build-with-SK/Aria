"""
scripts/backup.py
=================
Back up ARIA and SENTINEL's state. Never their secrets.

    python scripts/backup.py                 # make a backup
    python scripts/backup.py --list          # what exists
    python scripts/backup.py --verify FILE   # check an archive against its manifest
    python scripts/backup.py --restore FILE --to DIR   # unpack for inspection

WHAT IS KEPT
------------
Everything under each application's `data/` directory that is state rather than
cache: conversation records, ratings and corrections, memory (including the
ChromaDB stores), research and the prediction ledger, desk history, configuration.

WHAT IS NEVER KEPT
------------------
Secrets. `.env` files, session signing keys, the session store, API-key files —
matched by name below and checked again by a test. A backup is the copy of your
data most likely to leave the machine (an external disk, a cloud folder), which
makes it exactly the wrong place for a credential. After a restore, secrets come
back from wherever you keep them (a password manager), and every device signs in
again — which is correct: sessions should not survive a machine being rebuilt.

Also skipped: caches and raw downloads that are regenerated on the next run, and
logs. Pass --include-models to keep trained adapters, which are large.

DATABASES ARE COPIED WITH SQLITE'S OWN BACKUP API
-------------------------------------------------
A byte copy of a SQLite file that is being written can capture a half-written
page, and the result opens as "database disk image is malformed" on the day you
need it. `sqlite3.Connection.backup` takes a consistent snapshot while the
application keeps running. Every *.db / *.sqlite / *.sqlite3 goes that way.

WHERE ARCHIVES GO
-----------------
`$SERVER_BACKUP_DIR`, else `~/ServerBackups`. Outside both repositories, always —
the tool refuses a destination inside either tree so a backup can never end up
committed. Retention: the newest 14 are kept (`--keep N`).

RESTORING
---------
`--restore` unpacks into a directory you name and never over live data. Stop the
services, move the unpacked `aria/data` and `sentinel/data` into place, restore
secrets, start the services. docs/MAC_MINI_SERVER.md has the full procedure.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import os
import sqlite3
import sys
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent

#: Never archived, whatever directory they turn up in. Checked against the file
#: NAME, case-insensitively. tests/test_backup.py asserts none of these survive.
SECRET_PATTERNS = (
    ".env", ".env.*", "*.env",
    ".session_key", "*.session_key", "session_key*",
    "sessions.json", "sessions.json.tmp",
    "keys.json", "*_keys.json", "*keys*.json",
    "*.pem", "*.key", "*.p8", "*.p12",
    "*secret*", "*credential*", "*token*",
    "cloudflared*.json",
)

#: Regenerated on the next run, or not state at all.
SKIP_DIRS = {"cache", "raw", "logs", "__pycache__", "scratch", "tmp", "outbox"}
SKIP_FILES = ("*.log", "*.log.*", "*.pyc", "*.tmp", "*.lock", "*-journal",
              "*.db-wal", "*.db-shm", "*.sqlite3-wal", "*.sqlite3-shm")
MODEL_DIRS = {"models"}

SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3")


def _is_secret(name: str) -> bool:
    n = name.lower()
    return any(fnmatch.fnmatch(n, pat) for pat in SECRET_PATTERNS)


def _skip_file(name: str) -> bool:
    n = name.lower()
    return any(fnmatch.fnmatch(n, pat) for pat in SKIP_FILES)


def sentinel_home() -> Path | None:
    env = (os.environ.get("SENTINEL_HOME") or "").strip()
    cands = [Path(env).expanduser()] if env else [ROOT.parent / "atlas",
                                                  ROOT.parent / "sentinel"]
    for c in cands:
        if (c / "sentinel").is_dir():
            return c
    return None


def sources() -> dict[str, Path]:
    """{archive prefix: data directory}. SENTINEL only if it is installed."""
    out = {"aria/data": ROOT / "data"}
    home = sentinel_home()
    if home is not None and (home / "data").is_dir():
        out["sentinel/data"] = home / "data"
    return out


def backup_dir() -> Path:
    env = (os.environ.get("SERVER_BACKUP_DIR") or "").strip()
    return Path(env).expanduser() if env else Path.home() / "ServerBackups"


def _inside_a_repo(path: Path) -> bool:
    p = path.resolve()
    repos = [ROOT.resolve()]
    home = sentinel_home()
    if home is not None:
        repos.append(home.resolve())
    for repo in repos:
        try:
            p.relative_to(repo)
            return True
        except ValueError:
            continue
    return False


def walk(src: Path, include_models: bool):
    """Yield (path, relative) for every file worth keeping. Never a secret."""
    for dirpath, dirnames, filenames in os.walk(src):
        d = Path(dirpath)
        keep = []
        for name in dirnames:
            low = name.lower()
            if low in SKIP_DIRS or low.startswith("."):
                continue
            if low in MODEL_DIRS and not include_models:
                continue
            keep.append(name)
        dirnames[:] = keep                      # prune the walk in place
        for name in filenames:
            if _is_secret(name) or _skip_file(name):
                continue
            path = d / name
            if path.is_symlink():
                continue                        # never follow out of the tree
            yield path, path.relative_to(src)


def _sqlite_snapshot(path: Path) -> bytes | None:
    """A consistent copy of a live SQLite file, or None if it is not one."""
    try:
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return None
    try:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "snap.db"
            dst = sqlite3.connect(out)
            try:
                src.backup(dst)
            finally:
                dst.close()
            return out.read_bytes()
    except sqlite3.Error:
        return None
    finally:
        src.close()


def make_backup(*, dest: Path | None = None, include_models: bool = False,
                keep: int = 14, stamp: str | None = None) -> Path:
    dest = (dest or backup_dir()).expanduser()
    if _inside_a_repo(dest):
        raise SystemExit(f"refusing to write a backup inside a repository: {dest}\n"
                         "Set SERVER_BACKUP_DIR to somewhere outside both trees.")
    dest.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(dest, 0o700)
    except OSError:
        pass

    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    archive = dest / f"server-backup-{stamp}.tar.gz"
    tmp = archive.with_suffix(".partial")
    manifest = {"created": datetime.now().isoformat(timespec="seconds"),
                "host": os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", ""),
                "sources": {}, "files": {}, "sqlite_snapshots": [],
                "excluded_by_rule": {"secret_patterns": list(SECRET_PATTERNS),
                                     "skipped_dirs": sorted(SKIP_DIRS),
                                     "models_included": include_models}}

    with tarfile.open(tmp, "w:gz") as tar:
        for prefix, src in sources().items():
            manifest["sources"][prefix] = str(src)
            for path, rel in walk(src, include_models):
                arc = f"{prefix}/{rel.as_posix()}"
                data = None
                if path.suffix.lower() in SQLITE_SUFFIXES:
                    data = _sqlite_snapshot(path)
                    if data is not None:
                        manifest["sqlite_snapshots"].append(arc)
                if data is None:
                    try:
                        data = path.read_bytes()
                    except OSError:
                        continue                # vanished mid-walk; not fatal
                info = tarfile.TarInfo(arc)
                info.size = len(data)
                info.mtime = int(path.stat().st_mtime) if path.exists() else int(time.time())
                info.mode = 0o600
                tar.addfile(info, io.BytesIO(data))
                manifest["files"][arc] = {"bytes": len(data),
                                          "sha256": hashlib.sha256(data).hexdigest()}
        blob = json.dumps(manifest, indent=1).encode()
        info = tarfile.TarInfo("MANIFEST.json")
        info.size, info.mtime, info.mode = len(blob), int(time.time()), 0o600
        tar.addfile(info, io.BytesIO(blob))

    tmp.replace(archive)                      # never leave a half-written archive
    try:
        os.chmod(archive, 0o600)
    except OSError:
        pass
    prune(dest, keep)
    return archive


def prune(dest: Path, keep: int) -> list[Path]:
    olds = sorted(dest.glob("server-backup-*.tar.gz"))[:-keep] if keep > 0 else []
    for p in olds:
        p.unlink(missing_ok=True)
    return olds


def verify(archive: Path) -> dict:
    """Recompute every hash against the manifest. {ok, files, problems}."""
    problems = []
    with tarfile.open(archive, "r:gz") as tar:
        manifest = json.loads(tar.extractfile("MANIFEST.json").read())
        for arc, meta in manifest["files"].items():
            member = tar.extractfile(arc)
            if member is None:
                problems.append(f"missing: {arc}")
                continue
            if hashlib.sha256(member.read()).hexdigest() != meta["sha256"]:
                problems.append(f"hash mismatch: {arc}")
        for name in tar.getnames():
            if _is_secret(Path(name).name):
                problems.append(f"SECRET IN ARCHIVE: {name}")
    return {"ok": not problems, "files": len(manifest["files"]),
            "problems": problems}


def restore(archive: Path, to: Path) -> Path:
    """Unpack into `to`, which must not exist yet. Never over live data."""
    to = to.expanduser()
    if to.exists() and any(to.iterdir()):
        raise SystemExit(f"{to} exists and is not empty. Restore into a fresh "
                         "directory, then move the pieces into place by hand.")
    for live in sources().values():
        try:
            to.resolve().relative_to(live.resolve())
            raise SystemExit("refusing to restore into a live data directory")
        except ValueError:
            pass
    to.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar.getmembers():
            # No absolute paths, no traversal: an archive is data, not a script.
            if m.name.startswith("/") or ".." in Path(m.name).parts or not m.isfile():
                continue
            tar.extract(m, to)
    return to


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[3])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--verify", metavar="ARCHIVE")
    ap.add_argument("--restore", metavar="ARCHIVE")
    ap.add_argument("--to", metavar="DIR")
    ap.add_argument("--keep", type=int, default=14)
    ap.add_argument("--include-models", action="store_true")
    args = ap.parse_args()

    if args.list:
        for p in sorted(backup_dir().glob("server-backup-*.tar.gz")):
            print(f"{p.stat().st_size / 1e6:8.1f} MB  {p}")
        return 0
    if args.verify:
        r = verify(Path(args.verify))
        print(json.dumps(r, indent=1))
        return 0 if r["ok"] else 1
    if args.restore:
        if not args.to:
            print("--restore needs --to DIR")
            return 2
        out = restore(Path(args.restore), Path(args.to))
        print(f"unpacked into {out}. Nothing live was touched.")
        return 0

    started = time.time()
    archive = make_backup(include_models=args.include_models, keep=args.keep)
    r = verify(archive)
    print(f"{archive}  ({archive.stat().st_size / 1e6:.1f} MB, {r['files']} files, "
          f"{time.time() - started:.1f}s)  verify: {'ok' if r['ok'] else r['problems']}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
