"""Restore a verified state archive on the fresh OCI host, then stage privately."""
from __future__ import annotations
import hashlib
import json
import os
import secrets
import sqlite3
import tarfile
from pathlib import Path

ROOT = Path.home() / "apps"
PRIVATE = Path.home() / ".config" / "aria-sentinel"
MODEL = "qwen2.5:7b-instruct-q4_K_M"


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    path.chmod(0o600)


def update_json(path: Path, changes: dict) -> None:
    original = json.loads(path.read_text()) if path.exists() else {}
    original.update(changes)
    write_json(path, original)


def configure_paths() -> None:
    aria, sentinel = ROOT / "aria", ROOT / "sentinel"
    update_json(aria / "data/desk_config.json", {"auto_execute": False, "reflex_enabled": False})
    update_json(aria / "data/vault_config.json", {"vault_path": str(ROOT / "DigitalBrain")})
    write_json(aria / "configs/inference_tiers.json", {tier: [["ollama", MODEL]] for tier in ("FAST", "STANDARD", "DEEP")})
    update_json(sentinel / "data/sentinel_config.json", {
        "aria_url": "http://127.0.0.1:8000", "vault_path": str(ROOT / "DigitalBrain"),
        "max_parallel_workers": 1, "frontier_enabled": False,
        "gods_eye_repo": str(ROOT / "gods-eye-view"), "gods_eye_url": "http://127.0.0.1:4174",
        "browser_use_repo": str(ROOT / "browser-use"),
        "projects": [{"name": name, "path": str(ROOT / name), "purpose": "Owner cloud application"} for name in ("aria", "sentinel")],
    })
    update_json(sentinel / "data/mind/router_config.json", {
        "ollama_url": "http://127.0.0.1:11434", "local_model": MODEL,
        "consult_enabled": False, "resource_fallback_model": "", "local_num_ctx": 8192,
    })


def main() -> None:
    marker = PRIVATE / "restore-complete.json"
    if marker.exists():
        raise SystemExit("State was already restored; refusing to overwrite it")
    for app in ("aria", "sentinel"):
        if (ROOT / app / "data").exists():
            raise SystemExit(f"{app} data already exists; inspect before restoring")
    with tarfile.open(Path.home() / "deploy" / "state.tar.gz") as tar:
        manifest = json.loads(tar.extractfile("MANIFEST.json").read())
        for name, meta in manifest["files"].items():
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts or rel.parts[:2] not in (("aria", "data"), ("sentinel", "data")):
                raise ValueError("Unexpected archive path")
            member = tar.getmember(name)
            if not member.isfile() or hashlib.sha256(tar.extractfile(member).read()).hexdigest() != meta["sha256"]:
                raise ValueError("State archive failed verification")
        tar.extractall(ROOT, members=[tar.getmember(n) for n in manifest["files"]], filter="data")
    for name in manifest["sqlite_snapshots"]:
        connection = sqlite3.connect(f"file:{ROOT / name}?mode=ro", uri=True)
        try:
            if connection.execute("pragma quick_check").fetchone()[0] != "ok":
                raise ValueError("Restored database failed quick_check")
        finally:
            connection.close()
    configure_paths()
    PRIVATE.mkdir(parents=True, exist_ok=True)
    PRIVATE.chmod(0o700)
    credentials = {key: secrets.token_urlsafe(32) for key in (
        "ARIA_OWNER_TOKEN", "ARIA_SESSION_SECRET", "ARIA_SERVICE_TOKEN",
        "SENTINEL_TOKEN", "SENTINEL_SESSION_SECRET", "SENTINEL_CONSULT_TOKEN")}
    write_json(PRIVATE / "owner-access.json", {
        "ARIA_URL": "http://aria-cloud.localhost:18000/app/",
        "SENTINEL_URL": "http://sentinel-cloud.localhost:18300/",
        **credentials,
    })
    common = {"PYTHONUNBUFFERED": "1", "OLLAMA_URL": "http://127.0.0.1:11434",
              "OLLAMA_BASE": "http://127.0.0.1:11434", "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"}
    environments = {
        "aria": {**common, **{k: v for k, v in credentials.items() if k.startswith("ARIA_") or k == "SENTINEL_CONSULT_TOKEN"},
            "ARIA_TESTING": "1", "ARIA_RUN_BRAIN": "false", "ARIA_LIVE_TRADING_ENABLED": "false",
            "ARIA_ALLOW_VENDOR_BRAIN": "0", "ARIA_TRUST_PROXY": "0", "ALPACA_PAPER": "true",
            "ARIA_BASE_URL": "http://aria-cloud.localhost:18000", "ARIA_FREE_MODEL": MODEL},
        "sentinel": {**common, **{k: v for k, v in credentials.items() if k.startswith("SENTINEL_") or k == "ARIA_SERVICE_TOKEN"},
            "SENTINEL_LIGHT_START": "1", "SENTINEL_TRUST_PROXY": "0",
            "SENTINEL_ALLOWED_ORIGINS": "http://sentinel-cloud.localhost:18300,http://127.0.0.1:8300",
            "SENTINEL_ANATOMY_PATH": str(ROOT / "anatomy"), "ARIA_URL": "http://127.0.0.1:8000"},
    }
    for app, env in environments.items():
        path = PRIVATE / f"{app}.env"
        path.write_text("".join(f"{key}={value}\n" for key, value in env.items()), encoding="utf-8")
        path.chmod(0o600)
    write_json(marker, {"files": len(manifest["files"]), "sqlite_checks": len(manifest["sqlite_snapshots"]), "stage": "workers-disabled"})
    print(json.dumps({"restored": len(manifest["files"]), "sqlite_checks": len(manifest["sqlite_snapshots"]), "credentials": "written privately; not printed"}))


if __name__ == "__main__":
    os.umask(0o077)
    main()
