"""
src/selfmod/protected.py
========================
THE FENCE. Paths ARIA may never modify, and the machinery to prove she has
not.

A rule that lives only in a document is one a future session will not know
about, so this one is machine-checked: `tests/test_protected_paths.py` walks
the history and fails the suite if any ARIA-authored commit touched a
protected path. CI runs `scripts/check_protected_paths.py` on every push, and
`src/selfmod/sandbox.py` refuses to propose such a branch in the first place.

Three layers, deliberately: the sandbox stops the honest mistake, the test
stops the branch that got made anyway, and CI stops the branch that skipped
the sandbox.

WHAT IS FENCED AND WHY
----------------------
- `src/auth/`                       ownership must stay provable, not inferable
- `src/execution/live_guard.py`     the live-money gate (PaperOnlyBroker)
- `src/execution/approval_queue.py` the human approval step the gate feeds
- `.github/workflows/`              the checks that catch regressions
- `src/selfmod/protected.py`        this file — the fence cannot move itself
- `tests/test_protected_paths.py`   the test that enforces it
- `scripts/check_protected_paths.py` the CI entry point

The last three exist because a fence that can rewrite its own posts is
scenery. This file is not configurable from JSON for the same reason: a config
file is a path, and a path can be edited by a commit.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

#: Prefixes (directories, trailing slash) and exact files. Matched against
#: repo-relative POSIX paths, case-insensitively — Windows and Linux agree.
PROTECTED: tuple[str, ...] = (
    "src/auth/",
    "src/execution/live_guard.py",
    "src/execution/approval_queue.py",
    ".github/workflows/",
    "src/selfmod/protected.py",
    "tests/test_protected_paths.py",
    "scripts/check_protected_paths.py",
)

#: One line per fenced path, for the refusal message. A refusal that says only
#: "denied" teaches a future session nothing.
REASONS: dict[str, str] = {
    "src/auth/": "ownership must stay provable, not inferable",
    "src/execution/live_guard.py": "the live-money gate — invariant 2",
    "src/execution/approval_queue.py": "the human approval step — invariant 2",
    ".github/workflows/": "the checks that catch regressions",
    "src/selfmod/protected.py": "the fence cannot move its own posts",
    "tests/test_protected_paths.py": "the test that enforces the fence",
    "scripts/check_protected_paths.py": "the CI entry point for the fence",
}

#: How an ARIA-authored commit identifies itself. The sandbox sets exactly
#: this identity; anything else claiming to be her is caught by the prefix
#: rules below, which are deliberately generous — over-matching costs a human
#: one explanatory commit, under-matching costs the invariant.
ARIA_AUTHOR_NAME = "ARIA"
ARIA_AUTHOR_EMAIL = "aria@aria.local"

_ARIA_TRAILER_MARKERS = (
    "co-authored-by: aria",
    "authored-by: aria",
    "aria-authored: true",
)


def normalise(path: str) -> str:
    """Repo-relative POSIX, lower-cased, no leading './' or '/'."""
    p = str(path).replace("\\", "/").strip().strip('"')
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/").lower()


def is_protected(path: str) -> bool:
    """True if `path` is behind the fence. Directory rules match anything
    beneath them; file rules match exactly."""
    p = normalise(path)
    if not p:
        return False
    for rule in PROTECTED:
        r = rule.lower()
        if r.endswith("/"):
            if p == r.rstrip("/") or p.startswith(r):
                return True
        elif p == r:
            return True
    return False


def reason_for(path: str) -> str:
    p = normalise(path)
    for rule in PROTECTED:
        r = rule.lower()
        hit = p.startswith(r) if r.endswith("/") else p == r
        if hit:
            return REASONS.get(rule, "protected")
    return ""


def protected_hits(paths) -> list[str]:
    """The subset of `paths` that is fenced, de-duplicated, order preserved."""
    seen, hits = set(), []
    for p in paths:
        n = normalise(p)
        if n and n not in seen and is_protected(n):
            seen.add(n)
            hits.append(n)
    return hits


def is_aria_author(name: str = "", email: str = "", message: str = "") -> bool:
    """Whether a commit is ARIA's work.

    Author identity OR a co-author trailer counts: a change she wrote and a
    human committed for her is still her change, and routing round the fence
    by borrowing the owner's git identity is precisely the move this has to
    catch.
    """
    n = (name or "").strip().lower()
    e = (email or "").strip().lower()
    if n == "aria" or n.startswith("aria ") or n.startswith("aria<"):
        return True
    if e == ARIA_AUTHOR_EMAIL or e.startswith("aria@"):
        return True
    body = (message or "").lower()
    return any(marker in body for marker in _ARIA_TRAILER_MARKERS)


# ── git ──────────────────────────────────────────────────────────────────────

@dataclass
class Violation:
    sha: str
    author: str
    subject: str
    paths: list[str] = field(default_factory=list)

    def describe(self) -> str:
        lines = [f"{self.sha[:10]}  {self.author}  {self.subject}"]
        lines += [f"      {p}   ← {reason_for(p)}" for p in self.paths]
        return "\n".join(lines)


class GitUnavailable(RuntimeError):
    """No git, or not a repository. The caller decides whether that is a skip
    or a failure — CI treats it as a failure, a developer's checkout may not."""


def _git(repo: Path, *args: str, timeout: int = 60) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace")
    except FileNotFoundError as e:
        raise GitUnavailable("git executable not found") from e
    except subprocess.TimeoutExpired as e:
        raise GitUnavailable(f"git timed out: {' '.join(args)}") from e
    if out.returncode != 0:
        raise GitUnavailable(
            f"git {' '.join(args)} failed ({out.returncode}): "
            f"{(out.stderr or '').strip()[:300]}")
    return out.stdout


def git_available(repo: Path = ROOT) -> bool:
    try:
        _git(repo, "rev-parse", "--git-dir", timeout=15)
        return True
    except GitUnavailable:
        return False


_REC = "\x1e"
_FLD = "\x1f"


def parse_log(raw: str) -> list[tuple[str, str, str, str, list[str]]]:
    """Parse the `--format` used by `scan_history` into
    (sha, name, email, message, paths). Split out so it can be tested without
    a repository."""
    commits = []
    for record in raw.split(_REC):
        record = record.strip("\n")
        if not record.strip():
            continue
        parts = record.split(_FLD)
        if len(parts) < 5:
            continue
        sha, name, email, message, tail = parts[0], parts[1], parts[2], parts[3], parts[4]
        paths = [ln.strip() for ln in tail.splitlines() if ln.strip()]
        commits.append((sha.strip(), name, email, message, paths))
    return commits


def scan_history(repo: Path = ROOT, rev_range: str | None = None,
                 max_commits: int = 5000) -> list[Violation]:
    """Every ARIA-authored commit in `rev_range` (default: everything
    reachable from HEAD) that touched a fenced path.

    Two passes, because git reports merges differently from ordinary commits:

    1. The full walk — every commit on every branch that landed. This is the
       one that matters: a fenced change made on a side branch is caught here
       whether or not it was later merged.
    2. Merges only, with `-m --first-parent`, which is the sole way to see the
       files a merge commit itself resolved. A plain `--name-only` reports a
       merge as having changed nothing, so an ARIA-authored conflict
       resolution inside `src/auth/` would otherwise read as clean.
    """
    fmt = f"--format={_REC}%H{_FLD}%an{_FLD}%ae{_FLD}%B{_FLD}"
    passes = (
        ["log", f"--max-count={max_commits}", fmt, "--name-only"],
        ["log", f"--max-count={max_commits}", fmt, "--name-only",
         "-m", "--first-parent", "--min-parents=2"],
    )

    violations: dict[str, Violation] = {}
    for args in passes:
        raw = _git(repo, *(args + ([rev_range] if rev_range else [])), timeout=180)
        for sha, name, email, message, paths in parse_log(raw):
            if not is_aria_author(name, email, message):
                continue
            hits = protected_hits(paths)
            if not hits:
                continue
            subject = ((message or "").strip().splitlines() or ["(no subject)"])[0]
            existing = violations.get(sha)
            if existing is None:
                violations[sha] = Violation(sha=sha, author=f"{name} <{email}>",
                                            subject=subject[:72], paths=hits)
            else:
                for h in hits:
                    if h not in existing.paths:
                        existing.paths.append(h)
    return list(violations.values())


def changed_paths(repo: Path = ROOT, base: str = "", head: str = "HEAD") -> list[str]:
    """Files a branch changes relative to its merge base with `base`. With no
    `base`, the working tree (staged + unstaged + untracked) instead."""
    if base:
        merge_base = _git(repo, "merge-base", base, head).strip()
        raw = _git(repo, "diff", "--name-only", f"{merge_base}..{head}")
        return [ln.strip() for ln in raw.splitlines() if ln.strip()]
    raw = _git(repo, "status", "--porcelain", "--untracked-files=all")
    paths = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        entry = line[3:] if len(line) > 3 else ""
        # renames read as "old -> new"; both sides matter
        if " -> " in entry:
            paths.extend(part.strip() for part in entry.split(" -> "))
        elif entry.strip():
            paths.append(entry.strip())
    return paths


def format_violations(violations: list[Violation]) -> str:
    if not violations:
        return "no ARIA-authored commit has touched a protected path"
    head = (f"{len(violations)} ARIA-authored commit(s) touched protected "
            f"paths — invariants 2, 3 and 4 of docs/ARIA_NEXT_SESSION.md:")
    return "\n".join([head, *(v.describe() for v in violations)])
