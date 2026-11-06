"""Package current application source for OCI without state or credentials."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path

SOURCE_DIRS = {
    "aria": {"backend", "src", "scripts", "frontend", "configs", "assets", "static", "templates"},
    "sentinel": {"sentinel", "src", "mind", "face", "scripts", "deploy", "assets", "static", "templates", "tools", "prompts"},
}
SKIP_PARTS = {"venv", ".venv", "node_modules", "__pycache__", ".git", "dist", ".pytest_cache"}
ROOT_FILES = {"requirements.txt", "requirements-browser.txt", "pyproject.toml", "setup.cfg", "setup.py", "LICENSE", "LICENSE.md", "README.md"}
SECRET_SUFFIXES = {".key", ".pem", ".p8", ".p12", ".pfx", ".env"}
SECRET_NAMES = {"keys.json", "credentials.json", "research_keys.json", "sessions.json", "token.txt"}
SECRET_CONTENT = re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bAKIA[A-Z0-9]{16}\b|\bsk-ant-api\d{2}-[A-Za-z0-9_-]{30,}")


def allowed(rel: Path, app: str) -> bool:
    if any(p.lower() in SKIP_PARTS or p.startswith(".") for p in rel.parts):
        return False
    if rel.name.lower() in SECRET_NAMES or rel.suffix.lower() in SECRET_SUFFIXES:
        return False
    if len(rel.parts) == 1:
        return rel.name in ROOT_FILES or rel.suffix == ".py"
    return rel.parts[0] in SOURCE_DIRS[app] and not rel.parts[0:2] == ("frontend", "dist")


def bundle(roots: dict[str, Path], frontend_dist: Path, destination: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    for root in roots.values():
        if destination.resolve().is_relative_to(root.resolve()):
            raise ValueError("Archive destination must be outside application source trees")
    manifest: dict = {"revisions": {}, "files": {}}
    with tarfile.open(destination, "w:gz") as tar:
        def add(path: Path, name: str) -> None:
            if path.is_symlink() or not path.is_file():
                return
            payload = path.read_bytes()
            if SECRET_CONTENT.search(payload):
                raise ValueError(f"Credential-like material detected in {name}; archive must not be uploaded")
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            member.mode = 0o600
            tar.addfile(member, io.BytesIO(payload))
            manifest["files"][name] = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}

        for app, root in roots.items():
            manifest["revisions"][app] = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            names = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]).decode().split("\0")
            for name in sorted(set(filter(None, names))):
                rel = Path(name)
                if allowed(rel, app):
                    path = root / rel
                    if path.resolve().is_relative_to(root.resolve()):
                        add(path, f"{app}/{rel.as_posix()}")
        if not (frontend_dist / "index.html").is_file():
            raise ValueError("A verified frontend build is required")
        for path in sorted(frontend_dist.rglob("*")):
            add(path, f"aria/frontend/dist/{path.relative_to(frontend_dist).as_posix()}")
        payload = json.dumps(manifest, sort_keys=True).encode()
        member = tarfile.TarInfo("SOURCE_MANIFEST.json")
        member.size, member.mode = len(payload), 0o600
        tar.addfile(member, io.BytesIO(payload))
    # Verify every archive member before permitting transfer.
    with tarfile.open(destination) as tar:
        for name, meta in manifest["files"].items():
            if hashlib.sha256(tar.extractfile(name).read()).hexdigest() != meta["sha256"]:
                raise ValueError(f"Archive verification failed: {name}")
    return {"archive": str(destination), "files": len(manifest["files"]), "bytes": destination.stat().st_size, "verified": True}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aria", type=Path, required=True)
    ap.add_argument("--sentinel", type=Path, required=True)
    ap.add_argument("--frontend-dist", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    print(json.dumps(bundle({"aria": args.aria.resolve(), "sentinel": args.sentinel.resolve()}, args.frontend_dist.resolve(), args.out.resolve())))
