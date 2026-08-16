"""
scripts/promote_adapter.py
==========================
The gate a new adapter has to pass (docs/ARIA_NEXT_SESSION.md step 6).

    # what is serving right now
    venv/Scripts/python.exe scripts/promote_adapter.py status

    # register a candidate that came back from the cluster
    venv/Scripts/python.exe scripts/promote_adapter.py register \
        --version 2026-09-a --path data/training/adapters/2026-09-a \
        --base-model Qwen/Qwen2.5-7B-Instruct

    # score it against the incumbent on the held-out split, and decide
    venv/Scripts/python.exe scripts/promote_adapter.py decide \
        --version 2026-09-a --predictions candidate.jsonl \
        --incumbent-predictions incumbent.jsonl --holdout holdout.jsonl

    # act on an approved decision (a person, named)
    venv/Scripts/python.exe scripts/promote_adapter.py promote \
        --version 2026-09-a --approved-by "Soundariyan"

The prediction files are JSONL, one object per held-out row IN THE SAME ORDER,
each carrying the model's reply as `{"text": "..."}` or a probability as
`{"p_up": 0.62}`. Pairing is positional and both files must line up with the
holdout — a comparison run on mismatched rows is worse than no comparison.

`decide` never promotes. `promote` refuses anything `decide` held.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.training import adapters as A
from src.training import evaluate as E


def _read_jsonl(path: str) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _probabilities(rows: list[dict]) -> list[float | None]:
    out = []
    for r in rows:
        if isinstance(r.get("p_up"), (int, float)):
            out.append(float(r["p_up"]))
        else:
            out.append(E.parse_probability(r.get("text", "")))
    return out


def cmd_status(args) -> int:
    print(json.dumps(A.status(), indent=2, default=str))
    for d in A.history()[-5:]:
        print(f"\n{d.get('at')}  {d.get('action')}  {d.get('version')} "
              f"vs {d.get('against')}")
    return 0


def cmd_register(args) -> int:
    adapter = A.Adapter(version=args.version, path=args.path,
                        base_model=args.base_model, trained_at=args.trained_at,
                        notes=args.notes,
                        contains_personal_data=args.contains_personal_data)
    A.register(adapter)
    print(f"registered {args.version} — a candidate, not serving. Run "
          f"`decide` next.")
    return 0


def cmd_decide(args) -> int:
    holdout = _read_jsonl(args.holdout)
    candidate = _probabilities(_read_jsonl(args.predictions))
    incumbent = _probabilities(_read_jsonl(args.incumbent_predictions))
    if not (len(holdout) == len(candidate) == len(incumbent)):
        print(f"row counts differ (holdout {len(holdout)}, candidate "
              f"{len(candidate)}, incumbent {len(incumbent)}). Pairing is "
              f"positional; a comparison on mismatched rows is worse than "
              f"none.", file=sys.stderr)
        return 2

    comparison = E.compare(candidate, incumbent, holdout)
    print(comparison.describe())

    decision = A.decide(args.version, comparison)
    print("\n" + decision.describe())

    out = Path(args.out or f"decision_{args.version}.json")
    out.write_text(json.dumps(decision.to_dict(), indent=2, default=str),
                   encoding="utf-8")
    print(f"\ndecision written: {out}")
    if not decision.promote:
        A.record_hold(decision)
        print("held, and recorded — the held ones are the evidence the gate "
              "does something")
    return 0 if decision.promote else 1


def cmd_promote(args) -> int:
    path = Path(args.decision or f"decision_{args.version}.json")
    if not path.exists():
        print(f"no decision file at {path} — run `decide` first. An adapter is "
              f"promoted on evidence, not on a version number.", file=sys.stderr)
        return 2
    data = json.loads(path.read_text(encoding="utf-8"))
    decision = A.Decision(**data)
    try:
        A.promote(decision, approved_by=args.approved_by)
    except PermissionError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"{decision.version} is now serving. {decision.against} is kept as "
          f"the rollback target.")
    return 0


def cmd_rollback(args) -> int:
    try:
        A.rollback(approved_by=args.approved_by, reason=args.reason)
    except (ValueError, PermissionError) as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps(A.status(), indent=2, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status"); p.set_defaults(func=cmd_status)

    p = sub.add_parser("register")
    p.add_argument("--version", required=True)
    p.add_argument("--path", default="")
    p.add_argument("--base-model", default="")
    p.add_argument("--trained-at", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--contains-personal-data", action="store_true")
    p.set_defaults(func=cmd_register)

    p = sub.add_parser("decide")
    p.add_argument("--version", required=True)
    p.add_argument("--predictions", required=True)
    p.add_argument("--incumbent-predictions", required=True)
    p.add_argument("--holdout", required=True)
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_decide)

    p = sub.add_parser("promote")
    p.add_argument("--version", required=True)
    p.add_argument("--approved-by", required=True)
    p.add_argument("--decision", default="")
    p.set_defaults(func=cmd_promote)

    p = sub.add_parser("rollback")
    p.add_argument("--approved-by", required=True)
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_rollback)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
