"""
scripts/check_protected_paths.py
================================
CI entry point for the fence (docs/ARIA_NEXT_SESSION.md step 1).

    python scripts/check_protected_paths.py --require-git
    python scripts/check_protected_paths.py --base origin/main   # PR range
    python scripts/check_protected_paths.py --working-tree       # pre-commit

Exit 0 clean, 1 on a violation, 2 when the check could not run. `--require-git`
turns "could not run" into a failure, which is what CI wants: a fence that
reports "skipped" is a fence that is off.

The history scan is the real check. `--base` narrows it to one branch for a
faster PR signal; `--working-tree` is for a pre-commit hook, where nothing is
committed yet and the author is not yet known — there it asks whether the
change ITSELF touches fenced paths, and is meant to run only when ARIA is the
one editing (`--assume-aria`).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.selfmod import protected as P

EXIT_OK, EXIT_VIOLATION, EXIT_CANNOT_RUN = 0, 1, 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fail if ARIA touched a protected path.")
    ap.add_argument("--base", default="",
                    help="only scan commits this branch adds on top of BASE "
                         "(e.g. origin/main)")
    ap.add_argument("--working-tree", action="store_true",
                    help="check uncommitted changes instead of history")
    ap.add_argument("--assume-aria", action="store_true",
                    help="with --working-tree, treat the edits as ARIA's")
    ap.add_argument("--require-git", action="store_true",
                    help="fail rather than skip when git is unavailable")
    ap.add_argument("--repo", default=str(P.ROOT))
    args = ap.parse_args(argv)

    repo = Path(args.repo)

    if not P.git_available(repo):
        msg = f"cannot verify the fence: no git repository at {repo}"
        if args.require_git:
            print(f"FENCE UNVERIFIABLE — {msg}", file=sys.stderr)
            return EXIT_CANNOT_RUN
        print(f"skipped — {msg}")
        return EXIT_OK

    print("Fenced paths:")
    for rule in P.PROTECTED:
        print(f"  {rule:<38} {P.REASONS.get(rule, '')}")
    print()

    try:
        if args.working_tree:
            paths = P.changed_paths(repo)
            hits = P.protected_hits(paths)
            scope = f"{len(paths)} uncommitted path(s)"
            if hits and not args.assume_aria:
                print(f"note: uncommitted changes touch fenced paths "
                      f"({', '.join(hits)}). That is the owner's prerogative; "
                      f"pass --assume-aria to treat it as a violation.")
                hits = []
            if hits:
                print("FENCE VIOLATION — ARIA may not modify:", file=sys.stderr)
                for h in hits:
                    print(f"  {h}   ← {P.reason_for(h)}", file=sys.stderr)
                return EXIT_VIOLATION
        else:
            rev_range = f"{args.base}..HEAD" if args.base else None
            violations = P.scan_history(repo, rev_range=rev_range)
            scope = rev_range or "all history reachable from HEAD"
            if violations:
                print(P.format_violations(violations), file=sys.stderr)
                return EXIT_VIOLATION
    except P.GitUnavailable as e:
        print(f"FENCE UNVERIFIABLE — {e}", file=sys.stderr)
        return EXIT_CANNOT_RUN if args.require_git else EXIT_OK

    print(f"clean — {scope} checked, no ARIA-authored change behind the fence")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
