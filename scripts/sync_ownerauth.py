"""
scripts/sync_ownerauth.py
=========================
Copy the canonical owner-session core into SENTINEL, and report the checksum.

WHY THIS EXISTS
---------------
`src/auth/ownerauth.py` is shared by two applications that must not import each
other — ARIA has to run when SENTINEL is absent and vice versa, and making one
system's *authentication* depend on the other's source tree being present would
be a worse failure than a duplicated file.

So the file is duplicated on purpose, and kept honest mechanically rather than by
memory: a test in each repository pins the checksum of its own copy. Edit the
canonical file, run this, paste the printed checksum into both tests, and they
agree. Edit either copy by hand and that repository's test fails and says which
one drifted.

USAGE
-----
    venv\\Scripts\\python.exe scripts/sync_ownerauth.py            # copy + report
    venv\\Scripts\\python.exe scripts/sync_ownerauth.py --check    # report only

SENTINEL's location is resolved, never hardcoded — the same rule the rest of this
repository follows for the vault. Order:

    $SENTINEL_HOME  ->  ../atlas  ->  ../sentinel
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
CANONICAL = ROOT / "src" / "auth" / "ownerauth.py"
TARGET_REL = Path("sentinel") / "ownerauth.py"


def sentinel_home() -> Path | None:
    env = (os.environ.get("SENTINEL_HOME") or "").strip()
    if env:
        p = Path(env).expanduser()
        return p if (p / "sentinel").is_dir() else None
    for name in ("atlas", "sentinel"):
        p = ROOT.parent / name
        if (p / "sentinel").is_dir():
            return p
    return None


def digest(path: Path) -> str:
    """Hash the CONTENT, newline-normalised.

    Git is configured to rewrite line endings in these repositories, so a byte
    comparison would report drift every time a file crossed that boundary — a
    false alarm that teaches people to ignore the check, which is worse than not
    having one.
    """
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    check_only = "--check" in sys.argv

    if not CANONICAL.exists():
        print(f"canonical file missing: {CANONICAL}")
        return 2

    want = digest(CANONICAL)
    print(f"canonical : {CANONICAL.relative_to(ROOT)}")
    print(f"sha256    : {want}")

    home = sentinel_home()
    if home is None:
        print("\nSENTINEL not found beside this repository. Set SENTINEL_HOME, "
              "or place it at ../atlas. Nothing was copied.")
        return 1

    target = home / TARGET_REL
    if target.exists() and digest(target) == want:
        print(f"sentinel  : {target} — already in sync")
        return 0

    if check_only:
        print(f"sentinel  : {target} — OUT OF SYNC")
        print("\nRun without --check to copy the canonical file over it.")
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(CANONICAL.read_bytes())
    print(f"sentinel  : {target} — updated")
    print("\nPaste this into EXPECTED_SHA256 in both:")
    print("  aria     tests/test_ownerauth.py")
    print("  sentinel tests/test_ownerauth.py")
    print(f"\n    {want}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
