"""
scripts/init_service_tokens.py
==============================
Set up the credentials the two systems use to talk to each other. Never prints
a value.

    python scripts/init_service_tokens.py            # report only
    python scripts/init_service_tokens.py --apply    # fix what the report finds

FOUR CREDENTIALS, FOUR VALUES
-----------------------------
    ARIA_OWNER_TOKEN        human  -> ARIA        ARIA's .env only
    SENTINEL_TOKEN          human  -> SENTINEL    SENTINEL's .env only
    ARIA_SERVICE_TOKEN      SENTINEL -> ARIA      both .env files (same value)
    SENTINEL_SERVICE_TOKEN  ARIA -> SENTINEL      both sides (same value)
      (read also as SENTINEL_CONSULT_TOKEN, its original name, from either .env
       or the key files: ARIA data/research_keys.json, SENTINEL
       data/sentinel/keys.json)

--apply does exactly three things, and nothing else:

  1. If ARIA_SERVICE_TOKEN is missing on both sides, generates one (256 bits)
     and appends it to both .env files. If it exists on one side only, copies
     it to the other. If the two sides disagree, stops — that needs a human.
  2. Removes ARIA_OWNER_TOKEN from SENTINEL's .env and key file. SENTINEL must
     never hold ARIA's owner credential: it can reach the broker routes.
  3. Removes the environment variables Google/GitHub/Apple sign-in used from
     ARIA's .env (they were removed from the code).

It then checks the four credentials are pairwise distinct and reports.
Existing values are never changed and never rotated — rotation is
`init_owner_auth.py --force`, deliberately a separate act.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
ARIA_ENV = ROOT / ".env"
ARIA_KEYS = ROOT / "data" / "research_keys.json"

OBSOLETE_OAUTH = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GITHUB_CLIENT_ID",
                  "GITHUB_CLIENT_SECRET", "APPLE_CLIENT_ID", "APPLE_CLIENT_SECRET",
                  "ARIA_OWNER_EMAIL", "ARIA_FRONTEND_URL")


def sentinel_home() -> Path | None:
    env = (os.environ.get("SENTINEL_HOME") or "").strip()
    for c in ([Path(env).expanduser()] if env else [ROOT.parent / "atlas",
                                                    ROOT.parent / "sentinel"]):
        if (c / "sentinel").is_dir():
            return c
    return None


def read_env(path: Path) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if t and not t.startswith("#") and "=" in t:
                k, _, v = t.partition("=")
                out[k.strip()] = v.strip()
    return out


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def remove_env_keys(path: Path, keys) -> list[str]:
    """Drop `KEY=...` lines for the given keys. Comments and every other line
    are kept byte for byte. Returns the names removed."""
    if not path.exists():
        return []
    removed, kept = [], []
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        t = line.strip()
        name = t.partition("=")[0].strip() if "=" in t and not t.startswith("#") else ""
        if name in keys:
            removed.append(name)
            continue
        kept.append(line)
    if removed:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(kept), encoding="utf-8")
        tmp.replace(path)
    return removed


def append_env(path: Path, key: str, value: str, note: str) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    text += f"\n# {note}\n{key}={value}\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def main() -> int:
    apply = "--apply" in sys.argv
    home = sentinel_home()
    if home is None:
        print("SENTINEL not found beside ARIA (set SENTINEL_HOME). Nothing done.")
        return 1
    s_env, s_keys = home / ".env", home / "data" / "sentinel" / "keys.json"
    actions, problems = [], []

    a, s = read_env(ARIA_ENV), read_env(s_env)

    # 1. the two peer credentials. Each is ONE value held by BOTH sides — the
    #    caller sends it, the receiver compares it — and the two of them are
    #    different values, because one credential opening both directions means
    #    a leak at either end opens both.
    def first(mapping: dict, names) -> str:
        for n in names:
            v = str(mapping.get(n, "") or "").strip()
            if v:
                return v
        return ""

    peers = (
        ("ARIA_SERVICE_TOKEN", ("ARIA_SERVICE_TOKEN",),
         "SENTINEL -> ARIA scoped service credential"),
        # Read under its original name too, so an existing install is not
        # re-provisioned with a second value that then disagrees.
        ("SENTINEL_SERVICE_TOKEN",
         ("SENTINEL_SERVICE_TOKEN", "SENTINEL_CONSULT_TOKEN"),
         "ARIA -> SENTINEL consultation credential"),
    )
    for name, aliases, role in peers:
        av = first(a, aliases) or first(read_json(ARIA_KEYS), aliases)
        sv = first(s, aliases) or first(read_json(s_keys), aliases)
        if av and sv and not hmac.compare_digest(av, sv):
            problems.append(f"{name} differs between ARIA and SENTINEL — decide "
                            f"which is right and make them equal")
        elif not av and not sv:
            actions.append(f"generate {name} into both .env files")
            if apply:
                v = secrets.token_urlsafe(32)
                note = f"{role} (init_service_tokens.py)"
                append_env(ARIA_ENV, name, v, note)
                append_env(s_env, name, v, note)
        elif not (av and sv):
            actions.append(f"copy {name} to the side that lacks it")
            if apply:
                note = f"{role} (copied)"
                if av:
                    append_env(s_env, name, av, note)
                else:
                    append_env(ARIA_ENV, name, sv, note)

    # 2. SENTINEL must not hold ARIA's owner token
    if s.get("ARIA_OWNER_TOKEN"):
        actions.append("remove ARIA_OWNER_TOKEN from SENTINEL's .env")
        if apply:
            remove_env_keys(s_env, {"ARIA_OWNER_TOKEN"})
    keys = read_json(s_keys)
    if keys.get("ARIA_OWNER_TOKEN"):
        actions.append("remove ARIA_OWNER_TOKEN from SENTINEL's key file")
        if apply:
            keys.pop("ARIA_OWNER_TOKEN", None)
            tmp = s_keys.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(keys, indent=2), encoding="utf-8")
            tmp.replace(s_keys)

    # 3. obsolete OAuth variables
    stale = [k for k in OBSOLETE_OAUTH if k in a]
    if stale:
        actions.append(f"remove obsolete OAuth variables from ARIA's .env: {', '.join(stale)}")
        if apply:
            remove_env_keys(ARIA_ENV, set(stale))

    # 4. pairwise distinctness, re-read after any change
    a, s = read_env(ARIA_ENV), read_env(s_env)
    ak, sk = read_json(ARIA_KEYS), read_json(s_keys)
    creds = {
        "ARIA_OWNER_TOKEN": a.get("ARIA_OWNER_TOKEN", ""),
        "SENTINEL_TOKEN": s.get("SENTINEL_TOKEN", ""),
        "ARIA_SERVICE_TOKEN": a.get("ARIA_SERVICE_TOKEN", ""),
        "SENTINEL_SERVICE_TOKEN": (a.get("SENTINEL_SERVICE_TOKEN") or a.get("SENTINEL_CONSULT_TOKEN")
                                   or ak.get("SENTINEL_SERVICE_TOKEN") or ak.get("SENTINEL_CONSULT_TOKEN") or ""),
    }
    s_side = (s.get("SENTINEL_SERVICE_TOKEN") or s.get("SENTINEL_CONSULT_TOKEN")
              or sk.get("SENTINEL_SERVICE_TOKEN") or sk.get("SENTINEL_CONSULT_TOKEN") or "")
    if creds["SENTINEL_SERVICE_TOKEN"] and s_side and not hmac.compare_digest(
            creds["SENTINEL_SERVICE_TOKEN"], s_side):
        problems.append("SENTINEL_SERVICE_TOKEN differs between ARIA and SENTINEL")
    names = list(creds)
    for i, x in enumerate(names):
        for y in names[i + 1:]:
            if creds[x] and creds[y] and hmac.compare_digest(creds[x], creds[y]):
                problems.append(f"{x} and {y} are the same value — they must differ")

    print("service tokens:", "APPLIED" if apply else "report (use --apply)")
    for x in actions:
        print(f"  {'done' if apply else 'todo'}  {x}")
    for n, v in creds.items():
        print(f"  {'set ' if v else 'MISSING'}  {n}")
    for p in problems:
        print(f"  PROBLEM  {p}")
    if apply and actions:
        print("  restart both services for the change to take effect")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
