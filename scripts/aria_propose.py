"""
scripts/aria_propose.py
=======================
The sandbox, from the command line (docs/ARIA_NEXT_SESSION.md step 2).

    # 1. a workspace of her own, branched off main
    python scripts/aria_propose.py start --branch aria/tighten-carry-threshold

    # 2. ...she edits and commits in that worktree...

    # 3. every reason the proposal may not proceed, in one pass
    python scripts/aria_propose.py check --branch aria/tighten-carry-threshold

    # 4. the proposal, recorded. Add --open-pr to actually open one.
    python scripts/aria_propose.py propose \
        --branch aria/tighten-carry-threshold \
        --rationale data/selfmod/rationale.json

The rationale file answers four questions and is required:

    {"what_changed": "...", "why": "...",
     "evidence": "...", "expected_improvement": "..."}

`propose` without `--open-pr` records the proposal and prints the PR body for
a human to read. `--open-pr` pushes the branch and opens a draft PR — an
outward-facing act, so it never happens as a side effect of a passing check.
Merging is the owner's and is not automatable from here.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.selfmod import sandbox as S
from src.selfmod.protected import ROOT

EXIT_OK, EXIT_REFUSED, EXIT_ERROR = 0, 1, 2


def _load_rationale(path: str) -> S.Rationale:
    if not path:
        return S.Rationale()
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"rationale file not found: {p}")
    return S.Rationale.from_dict(json.loads(p.read_text(encoding="utf-8")))


def cmd_start(args) -> int:
    path = S.create_workspace(args.branch, base=args.base)
    print(f"workspace ready: {path}")
    print(f"  branch:   {args.branch} (off {args.base})")
    print(f"  identity: {S.ARIA_AUTHOR_NAME} <{S.ARIA_AUTHOR_EMAIL}>")
    print("\nEdit and commit there. Then:")
    print(f"  python scripts/aria_propose.py check --branch {args.branch}")
    return EXIT_OK


def cmd_check(args) -> int:
    worktree = Path(args.worktree) if args.worktree else S.workspace_path(args.branch)
    result = S.check(worktree, branch=args.branch, base=args.base,
                     rationale=_load_rationale(args.rationale),
                     run_tests=not args.no_tests,
                     test_timeout=args.test_timeout)
    print(result.describe())
    return EXIT_OK if result.ok else EXIT_REFUSED


def cmd_propose(args) -> int:
    worktree = Path(args.worktree) if args.worktree else S.workspace_path(args.branch)
    rationale = _load_rationale(args.rationale)
    result = S.check(worktree, branch=args.branch, base=args.base,
                     rationale=rationale, run_tests=not args.no_tests,
                     test_timeout=args.test_timeout)
    print(result.describe())

    pr_url = ""
    if result.ok and args.open_pr:
        title = args.title or f"ARIA: {rationale.what_changed.strip().splitlines()[0][:60]}"
        try:
            pr_url = S.open_pull_request(result, rationale, title=title,
                                         human_approved=True, draft=not args.ready)
            print(f"\npull request: {pr_url or '(created)'}")
        except Exception as e:
            print(f"\ncould not open the PR: {e}", file=sys.stderr)
            record = S.record_proposal(result, rationale)
            print(f"proposal recorded anyway: {record}")
            return EXIT_ERROR

    record = S.record_proposal(result, rationale, pr_url=pr_url)
    print(f"\nproposal recorded: {record.relative_to(ROOT)}")

    if result.ok and not args.open_pr:
        print("\n" + "─" * 70)
        print(rationale.render(branch=result.branch, tests=result.tests,
                               changed=result.changed))
        print("─" * 70)
        print("\nNo PR was opened. Add --open-pr when you want one.")
    return EXIT_OK if result.ok else EXIT_REFUSED


def cmd_list(args) -> int:
    if not S.PROPOSALS.exists():
        print("no proposals yet")
        return EXIT_OK
    files = sorted(S.PROPOSALS.glob("prop-*.json"))
    if not files:
        print("no proposals yet")
        return EXIT_OK
    for f in files[-args.limit:]:
        d = json.loads(f.read_text(encoding="utf-8"))
        chk = d.get("check", {})
        state = "accepted" if chk.get("ok") else f"refused ({len(chk.get('refusals') or [])})"
        print(f"{d['id']}  {d['at']}  {chk.get('branch', '?'):<38} {state}")
        if not chk.get("ok"):
            for r in (chk.get("refusals") or [])[:3]:
                print(f"    - {r.splitlines()[0][:100]}")
    return EXIT_OK


def cmd_cleanup(args) -> int:
    removed = S.remove_workspace(args.branch, force=args.force)
    print("workspace removed" if removed else "no such workspace")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, *, needs_rationale=False):
        p.add_argument("--branch", required=True)
        p.add_argument("--base", default=S.MAIN_BRANCH)
        p.add_argument("--worktree", default="")
        p.add_argument("--rationale", default="",
                       required=needs_rationale,
                       help="path to a JSON rationale file")
        p.add_argument("--no-tests", action="store_true",
                       help="skip the suite — always refuses; for inspecting "
                            "the other checks in isolation")
        p.add_argument("--test-timeout", type=int, default=3600)

    p = sub.add_parser("start", help="create the worktree")
    p.add_argument("--branch", required=True)
    p.add_argument("--base", default=S.MAIN_BRANCH)
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("check", help="list every reason it may not proceed")
    common(p)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("propose", help="check, record, and optionally open a PR")
    common(p, needs_rationale=True)
    p.add_argument("--open-pr", action="store_true",
                   help="push the branch and open a draft PR (human go-ahead)")
    p.add_argument("--ready", action="store_true",
                   help="with --open-pr, open it ready for review, not draft")
    p.add_argument("--title", default="")
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser("list", help="the proposal ledger")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("cleanup", help="remove the worktree")
    p.add_argument("--branch", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_cleanup)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
