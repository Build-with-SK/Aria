"""
scripts/init_owner_auth.py
==========================
First-run setup. Produces the owner credential for ARIA or SENTINEL.

WHY A COMMAND RATHER THAN A DEFAULT
-----------------------------------
The alternative to this script is a server that invents a credential when it
finds none — and every system that has ever done that has shipped `admin`,
`changeme` or a value derived from the hostname. An unconfigured server here
says SERVER NOT INITIALIZED and refuses, which is a worse first five minutes and
a better decade.

This script never writes a secret into the repository. It prints what to put in
your environment, or writes it to a file OUTSIDE the tree with 0600 permissions
if you ask it to.

USAGE
-----
    python scripts/init_owner_auth.py                  # a new random credential
    python scripts/init_owner_auth.py --app sentinel   # for SENTINEL instead
    python scripts/init_owner_auth.py --hash           # store it hashed
    python scripts/init_owner_auth.py --passphrase     # hash one you choose
    python scripts/init_owner_auth.py --out ~/.aria.env   # write it, 0600

WHICH FORM TO CHOOSE
--------------------
A generated credential is 256 bits of randomness. There is no dictionary to work
through and no password to guess, so storing it in the clear in a file only you
can read is sound, and it is the simplest thing that works.

`--hash` stores an scrypt hash instead of the value. Choose it when the
credential is something a human chose and might have reused, or when the
environment file is more exposed than the machine (a shared host, a config
management system, a backup that leaves the machine). The server can verify
against the hash and cannot recover the credential from it.

`--passphrase` reads a phrase from stdin and hashes it, for when you want
something you can type from memory on a phone. Weaker than a random token by a
long way; the throttle in ownerauth is what makes it survivable.
"""
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.auth import ownerauth  # noqa: E402

APPS = {
    "aria": {
        "token": "ARIA_OWNER_TOKEN",
        "hash": "ARIA_OWNER_TOKEN_HASH",
        "session": "ARIA_SESSION_SECRET",
        "service": "ARIA_SERVICE_TOKEN",
    },
    "sentinel": {
        "token": "SENTINEL_TOKEN",
        "hash": "SENTINEL_TOKEN_HASH",
        "session": "SENTINEL_SESSION_SECRET",
        "service": "SENTINEL_CONSULT_TOKEN",
    },
}


def _load_env_files(app: str) -> None:
    """Read the .env the chosen application would read.

    SENTINEL is resolved rather than hardcoded, the same order its own tooling
    uses: $SENTINEL_HOME, then ../atlas, then ../sentinel. Values already in the
    environment win, because an operator who exported one meant it.
    """
    candidates = [ROOT / ".env"]
    if app == "sentinel":
        env_home = (os.environ.get("SENTINEL_HOME") or "").strip()
        homes = [Path(env_home).expanduser()] if env_home else [
            ROOT.parent / "atlas", ROOT.parent / "sentinel"]
        candidates = [h / ".env" for h in homes if (h / "sentinel").is_dir()]

    for path in candidates:
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate the owner credential for ARIA or SENTINEL.")
    ap.add_argument("--app", choices=sorted(APPS), default="aria")
    ap.add_argument("--hash", action="store_true",
                    help="store an scrypt hash instead of the value")
    ap.add_argument("--passphrase", action="store_true",
                    help="hash a phrase you type instead of a random token")
    ap.add_argument("--out", metavar="PATH",
                    help="write the variables to this file with 0600 "
                         "permissions (must be outside the repository)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing file")
    args = ap.parse_args()

    names = APPS[args.app]

    # Look where the applications actually look. The first version of this
    # checked os.environ only, so on a machine whose credential lives in a .env
    # file — which is how both of them are configured and documented — the
    # guard below never fired and the script cheerfully printed a NEW
    # credential, implying a rotation it had not performed and the owner had
    # not asked for.
    _load_env_files(args.app)

    # Refuse to silently replace a working configuration. Rotating is a
    # deliberate act with a consequence — every existing session dies with the
    # credential — and it should not happen because somebody re-ran a setup
    # command to see what it printed.
    live = os.environ.get(names["token"]) or os.environ.get(names["hash"])
    if live and not args.force:
        print(f"{names['token']} (or {names['hash']}) is already set in this "
              f"environment.\n"
              f"Rotating it will sign out every device. Re-run with --force if "
              f"that is what you want.")
        return 1

    if args.passphrase:
        one = getpass.getpass("Owner passphrase: ")
        two = getpass.getpass("Again: ")
        if one != two:
            print("They do not match.")
            return 1
        if len(one) < 12:
            print("Too short. Twelve characters is the floor, and a random "
                  "token is better than anything you will remember.")
            return 1
        credential, hashed = one, True
    else:
        credential, hashed = secrets.token_urlsafe(32), args.hash

    session_secret = secrets.token_urlsafe(48)

    if hashed:
        stored = f"{names['hash']}={ownerauth.hash_secret(credential)}"
    else:
        stored = f"{names['token']}={credential}"

    block = "\n".join([
        f"# {args.app.upper()} owner authentication.",
        "# Keep this file outside the repository, readable only by you.",
        stored,
        f"{names['session']}={session_secret}",
    ])

    if args.out:
        out = Path(args.out).expanduser()
        try:
            out.resolve().relative_to(ROOT.resolve())
            print(f"Refusing to write inside the repository: {out}\n"
                  "Secrets belong outside the tree — try ~/.aria.env")
            return 1
        except ValueError:
            pass                       # outside the repo, which is the point
        if out.exists() and not args.force:
            print(f"{out} already exists. Re-run with --force to replace it.")
            return 1
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(block + "\n", encoding="utf-8")
        try:
            os.chmod(out, 0o600)
        except OSError:
            print("NOTE: could not set 0600 permissions (expected on Windows). "
                  "Check this file is not readable by other accounts.")
        print(f"Written to {out} (0600).")
    else:
        print(block)
        print()

    if args.passphrase or not hashed:
        print("SIGN IN WITH THIS VALUE — it is not shown again:")
        print()
        print(f"    {credential}")
        print()

    print("Then, on the Mac mini:")
    print(f"  1. load it into the service environment "
          f"(launchd EnvironmentVariables, or `set -a; . {args.out or '~/.env'}`)")
    print("  2. restart the service")
    print("  3. check GET /api/auth/status reports "
          "authentication_configured: true")
    print()
    print("Rotating later signs out every device, which is the point: "
          "sessions minted under the old credential must not survive it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
