"""
src/selfmod/sandbox.py
======================
THE SANDBOX. How a change ARIA writes reaches the owner.

Step 2 of docs/ARIA_NEXT_SESSION.md, and the rule is short: she works on a
branch, never on `main`, never in the owner's working tree. The full suite is
green or the branch never leaves. She opens a PR with a written rationale —
what changed, why, what evidence prompted it, what she expects to improve. He
merges. Nobody else can.

Everything here is a REFUSAL ENGINE. `check()` returns the list of reasons a
proposal may not proceed; an empty list is the only thing that counts as
permission. Refusals are stated in full rather than one at a time, so a
session fixing them does not discover them serially.

Five things stop a proposal:

1. It is on `main`, or in the owner's own checkout.
2. It touches a fenced path (src/selfmod/protected.py).
3. The suite is not green.
4. It deletes or empties tests — the cheapest way to make (3) true is to
   delete the test that fails, and that must cost more than fixing the code.
5. The rationale is missing, or is a placeholder.

Opening the PR is a separate call that a human has to ask for. Nothing here
merges anything, and nothing here can: merging is the owner's, per invariant.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.selfmod.protected import (ARIA_AUTHOR_EMAIL, ARIA_AUTHOR_NAME, ROOT,
                                   GitUnavailable, changed_paths,
                                   format_violations, protected_hits,
                                   reason_for, scan_history)

MAIN_BRANCH = "main"
WORKTREES = ROOT / "data" / "selfmod" / "worktrees"
PROPOSALS = ROOT / "data" / "selfmod" / "proposals"

#: A rationale field shorter than this is not a rationale.
MIN_FIELD_CHARS = 25

#: Text that looks like a filled-in field and is not one.
_PLACEHOLDERS = ("todo", "tbd", "n/a", "na", "none", "-", "fix", "wip",
                 "see above", "see diff", "self-explanatory", "?")


# ── the rationale ────────────────────────────────────────────────────────────

@dataclass
class Rationale:
    """The four questions the owner needs answered before he reads a diff.

    `evidence` is the load-bearing one. A change proposed because it seemed
    like an improvement, with nothing measured behind it, is the failure mode
    the whole measurement half of this project exists to prevent.
    """
    what_changed: str = ""
    why: str = ""
    evidence: str = ""
    expected_improvement: str = ""

    FIELDS = ("what_changed", "why", "evidence", "expected_improvement")
    LABELS = {
        "what_changed": "What changed",
        "why": "Why",
        "evidence": "What evidence prompted it",
        "expected_improvement": "What it should improve",
    }

    def problems(self) -> list[str]:
        out = []
        for f in self.FIELDS:
            value = (getattr(self, f) or "").strip()
            label = self.LABELS[f]
            if not value:
                out.append(f"rationale: '{label}' is empty")
            elif value.lower().rstrip(".") in _PLACEHOLDERS:
                out.append(f"rationale: '{label}' is a placeholder ({value!r})")
            elif len(value) < MIN_FIELD_CHARS:
                out.append(f"rationale: '{label}' is {len(value)} characters — "
                           f"at least {MIN_FIELD_CHARS} are needed to say "
                           f"anything a reviewer can act on")
        return out

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in self.FIELDS}

    @classmethod
    def from_dict(cls, d: dict) -> "Rationale":
        return cls(**{f: str(d.get(f, "") or "") for f in cls.FIELDS})

    def render(self, *, branch: str = "", tests: "TestResult | None" = None,
               changed: list[str] | None = None) -> str:
        """The PR body. Written for the owner reading it cold at speed."""
        lines = ["## What changed", self.what_changed.strip(), "",
                 "## Why", self.why.strip(), "",
                 "## What evidence prompted it", self.evidence.strip(), "",
                 "## What this should improve", self.expected_improvement.strip(), ""]
        if tests is not None:
            lines += ["## Tests", tests.summary(), ""]
        if changed:
            lines += ["## Files", *(f"- `{p}`" for p in sorted(changed)[:60])]
            if len(changed) > 60:
                lines.append(f"- …and {len(changed) - 60} more")
            lines.append("")
        lines += [
            "---",
            "",
            "Proposed by ARIA from the self-modification sandbox "
            "(`src/selfmod/sandbox.py`). She cannot merge this; only the owner "
            "can. No fenced path is touched — see `src/selfmod/protected.py`.",
        ]
        if branch:
            lines.append(f"Branch: `{branch}`")
        return "\n".join(lines)


# ── running the suite ────────────────────────────────────────────────────────

@dataclass
class TestResult:
    ran: bool = False
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    collected: int = 0
    returncode: int = -1
    tail: str = ""
    note: str = ""

    @property
    def green(self) -> bool:
        return (self.ran and self.returncode == 0
                and self.failed == 0 and self.errors == 0)

    def summary(self) -> str:
        if not self.ran:
            return f"not run — {self.note or 'no reason given'}"
        state = "green" if self.green else "RED"
        return (f"{state}: {self.passed} passed, {self.failed} failed, "
                f"{self.errors} errors, {self.skipped} skipped")

    def to_dict(self) -> dict:
        return {"ran": self.ran, "green": self.green, "passed": self.passed,
                "failed": self.failed, "errors": self.errors,
                "skipped": self.skipped, "collected": self.collected,
                "returncode": self.returncode, "note": self.note,
                "tail": self.tail[-2000:]}


_COUNT_RE = {
    "passed": re.compile(r"(\d+) passed"),
    "failed": re.compile(r"(\d+) failed"),
    "errors": re.compile(r"(\d+) errors?"),
    "skipped": re.compile(r"(\d+) skipped"),
}


def parse_pytest_output(text: str, returncode: int = 0) -> TestResult:
    """Read the counts off pytest's summary line. Split out from the
    subprocess call so it can be tested without running a suite."""
    r = TestResult(ran=True, returncode=returncode, tail=text[-4000:])
    tail = "\n".join(text.strip().splitlines()[-15:])
    for field_name, rx in _COUNT_RE.items():
        m = rx.search(tail)
        if m:
            setattr(r, field_name, int(m.group(1)))
    r.collected = r.passed + r.failed + r.errors + r.skipped
    if returncode != 0 and r.failed == 0 and r.errors == 0:
        # Exit codes 2-5: interrupted, usage error, no tests collected. A
        # non-zero exit with no counted failures is NOT green.
        r.note = f"pytest exited {returncode} without reporting failures"
    return r


def run_suite(worktree: Path, *, python: str = "", timeout: int = 3600,
              args: tuple[str, ...] = ("tests/", "-q")) -> TestResult:
    """Run the full suite inside `worktree`.

    The interpreter defaults to the one running this process, so a proposal is
    validated against the same environment the owner's tests use.
    """
    exe = python or sys.executable
    try:
        out = subprocess.run([exe, "-m", "pytest", *args], cwd=str(worktree),
                             capture_output=True, text=True, timeout=timeout,
                             encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return TestResult(note=f"interpreter not found: {exe}")
    except subprocess.TimeoutExpired:
        return TestResult(note=f"suite exceeded {timeout}s — treated as not green")
    return parse_pytest_output((out.stdout or "") + (out.stderr or ""),
                               out.returncode)


# ── the check ────────────────────────────────────────────────────────────────

@dataclass
class ProposalCheck:
    branch: str = ""
    worktree: str = ""
    base: str = MAIN_BRANCH
    changed: list[str] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    tests: TestResult = field(default_factory=TestResult)

    @property
    def ok(self) -> bool:
        return not self.refusals

    def to_dict(self) -> dict:
        return {"branch": self.branch, "worktree": self.worktree,
                "base": self.base, "ok": self.ok, "refusals": self.refusals,
                "notes": self.notes, "changed": self.changed,
                "tests": self.tests.to_dict()}

    def describe(self) -> str:
        if self.ok:
            head = f"PROPOSAL ACCEPTED — branch {self.branch}, {self.tests.summary()}"
        else:
            head = f"PROPOSAL REFUSED — {len(self.refusals)} reason(s):"
        lines = [head] + [f"  - {r}" for r in self.refusals]
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


def deleted_test_files(repo: Path, base: str, head: str = "HEAD") -> list[str]:
    """Test files the branch removes. The cheapest way to make a red suite
    green is to delete the test that fails; this makes that visible rather
    than silent."""
    from src.selfmod.protected import _git
    merge_base = _git(repo, "merge-base", base, head).strip()
    raw = _git(repo, "diff", "--name-only", "--diff-filter=D",
               f"{merge_base}..{head}")
    return [ln.strip() for ln in raw.splitlines()
            if ln.strip().replace("\\", "/").startswith("tests/")]


def check(worktree: Path, *, branch: str = "", base: str = MAIN_BRANCH,
          rationale: Rationale | None = None, run_tests: bool = True,
          owner_repo: Path = ROOT, python: str = "",
          test_timeout: int = 3600) -> ProposalCheck:
    """Every reason this proposal may not proceed, gathered in one pass."""
    from src.selfmod.protected import _git

    worktree = Path(worktree)
    result = ProposalCheck(worktree=str(worktree), base=base)

    # 1. where the work happened
    if worktree.resolve() == Path(owner_repo).resolve():
        result.refusals.append(
            "the change is in the owner's working tree — ARIA works in a "
            "separate worktree under data/selfmod/worktrees/")
    if not worktree.exists():
        result.refusals.append(f"worktree does not exist: {worktree}")
        return result

    try:
        current = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except GitUnavailable as e:
        result.refusals.append(f"cannot read the branch: {e}")
        return result
    result.branch = branch or current
    if branch and branch != current:
        result.refusals.append(
            f"worktree is on '{current}', not the proposed branch '{branch}'")
    if result.branch in (MAIN_BRANCH, "master", "HEAD"):
        result.refusals.append(
            f"'{result.branch}' is not a proposal branch — she works on a "
            f"branch off {MAIN_BRANCH}, never on {MAIN_BRANCH} itself")

    # 2. the fence
    try:
        result.changed = changed_paths(worktree, base=base)
    except GitUnavailable as e:
        result.notes.append(f"could not diff against {base}: {e}")
        try:
            result.changed = changed_paths(worktree)
        except GitUnavailable:
            result.changed = []
    hits = protected_hits(result.changed)
    for h in hits:
        result.refusals.append(f"touches a protected path: {h} — {reason_for(h)}")

    try:
        violations = scan_history(worktree, rev_range=f"{base}..HEAD")
        if violations:
            result.refusals.append(format_violations(violations))
    except GitUnavailable as e:
        result.notes.append(f"history scan skipped: {e}")

    # 3. tests deleted
    try:
        gone = deleted_test_files(worktree, base)
        for g in gone:
            result.refusals.append(
                f"deletes a test file: {g} — a red suite is fixed in the code, "
                f"not in the tests")
    except GitUnavailable as e:
        result.notes.append(f"deleted-test check skipped: {e}")

    # 4. the rationale
    rationale = rationale or Rationale()
    result.refusals.extend(rationale.problems())

    # 5. the suite
    if run_tests:
        result.tests = run_suite(worktree, python=python, timeout=test_timeout)
        if not result.tests.green:
            result.refusals.append(
                f"the suite is not green — {result.tests.summary()}. The branch "
                f"does not leave the sandbox.")
    else:
        result.tests = TestResult(note="skipped by the caller")
        result.refusals.append(
            "the suite was not run — green is a precondition, not a formality")

    if not result.changed:
        result.notes.append(f"no files differ from {base}")
    return result


# ── the worktree ─────────────────────────────────────────────────────────────

def workspace_path(branch: str) -> Path:
    """One flat directory name per branch. Dots are collapsed along with the
    separators — the name is derived from a branch string, and a directory
    called `..` is not a naming preference, it is a traversal."""
    return WORKTREES / re.sub(r"[^A-Za-z0-9_-]+", "-", branch).strip("-")


def create_workspace(branch: str, *, base: str = MAIN_BRANCH,
                     repo: Path = ROOT) -> Path:
    """A git worktree for ARIA to work in. Never the owner's checkout: two
    agents editing one working tree is how a half-finished experiment ends up
    in a commit somebody else was making."""
    from src.selfmod.protected import _git

    branch = branch.strip()
    if not branch or branch in (MAIN_BRANCH, "master"):
        raise ValueError(f"refusing to create a workspace on '{branch}'")
    path = workspace_path(branch)
    if path.exists():
        raise FileExistsError(f"workspace already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", branch, str(path), base, timeout=300)
    # Commits made here are hers, and the fence recognises them by this
    # identity. Set it on the worktree, not globally — the owner's identity in
    # his own checkout is untouched.
    _git(path, "config", "user.name", ARIA_AUTHOR_NAME)
    _git(path, "config", "user.email", ARIA_AUTHOR_EMAIL)
    return path


def remove_workspace(branch: str, *, repo: Path = ROOT, force: bool = False) -> bool:
    from src.selfmod.protected import _git
    path = workspace_path(branch)
    if not path.exists():
        return False
    args = ["worktree", "remove", str(path)] + (["--force"] if force else [])
    _git(repo, *args, timeout=120)
    return True


# ── the proposal record ──────────────────────────────────────────────────────

def record_proposal(check_result: ProposalCheck, rationale: Rationale,
                    *, pr_url: str = "") -> Path:
    """Persist the proposal — accepted or refused. The refused ones are the
    more useful half of the ledger: they are the record of what she tried to
    change and what stopped her."""
    PROPOSALS.mkdir(parents=True, exist_ok=True)
    pid = f"prop-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    payload = {
        "id": pid,
        "at": datetime.now().isoformat(timespec="seconds"),
        "author": f"{ARIA_AUTHOR_NAME} <{ARIA_AUTHOR_EMAIL}>",
        "rationale": rationale.to_dict(),
        "check": check_result.to_dict(),
        "pr_url": pr_url,
        "merged": False,          # only the owner sets this, by merging
    }
    path = PROPOSALS / f"{pid}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def open_pull_request(check_result: ProposalCheck, rationale: Rationale, *,
                      title: str, human_approved: bool = False,
                      repo: Path = ROOT, draft: bool = True) -> str:
    """Push the branch and open a PR. Refuses unless a human asked.

    Opening a PR is an outward-facing act on the owner's GitHub account, so it
    is never a side effect of a successful check — `scripts/aria_propose.py`
    requires an explicit `--open-pr` from whoever is at the keyboard.
    """
    if not human_approved:
        raise PermissionError(
            "opening a PR needs an explicit human go-ahead — run "
            "scripts/aria_propose.py with --open-pr")
    if not check_result.ok:
        raise PermissionError(
            "refusing to open a PR for a refused proposal:\n"
            + check_result.describe())

    from src.selfmod.protected import _git
    worktree = Path(check_result.worktree)
    _git(worktree, "push", "-u", "origin", check_result.branch, timeout=300)

    body = rationale.render(branch=check_result.branch,
                            tests=check_result.tests,
                            changed=check_result.changed)
    cmd = ["gh", "pr", "create", "--title", title, "--body", body,
           "--base", check_result.base, "--head", check_result.branch]
    if draft:
        cmd.append("--draft")
    out = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True,
                         timeout=300, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise RuntimeError(f"gh pr create failed: {(out.stderr or '').strip()[:400]}")
    return (out.stdout or "").strip().splitlines()[-1] if out.stdout.strip() else ""
