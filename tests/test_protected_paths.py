"""
tests/test_protected_paths.py
=============================
THE FENCE, enforced.

Step 1 of docs/ARIA_NEXT_SESSION.md: this must fail if a commit authored by
ARIA touches the auth layer, the live-money gate, the CI workflows, or this
test. It is the first thing built because everything after it — a model that
can write code, a sandbox that can open PRs — is only safe if this holds.

Two kinds of test here:

- Pure ones (no git) that pin the rule set itself. They fail if a future
  session quietly drops a path from the fence.
- A history walk that fails on the actual repository. It skips when there is
  no git — a source tarball is not a repository — and CI closes that hole by
  running `scripts/check_protected_paths.py --require-git`, which treats a
  missing git as a failure rather than a skip.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.selfmod import protected as P

ROOT = Path(__file__).resolve().parent.parent


# ── the rule set itself ──────────────────────────────────────────────────────

def test_the_four_named_in_the_brief_are_fenced():
    """These four are named in ARIA_NEXT_SESSION.md step 1. Dropping any one
    of them is the change this assertion exists to catch."""
    for path in ("src/auth/policy.py",
                 "src/auth/guard.py",
                 "src/execution/live_guard.py",
                 ".github/workflows/security.yml",
                 "tests/test_protected_paths.py"):
        assert P.is_protected(path), f"{path} must be behind the fence"


def test_the_fence_protects_itself():
    """A fence that can rewrite its own posts is scenery."""
    assert P.is_protected("src/selfmod/protected.py")
    assert P.is_protected("tests/test_protected_paths.py")
    assert P.is_protected("scripts/check_protected_paths.py")


def test_approval_queue_is_fenced():
    """Invariant 2 names the approval queue alongside PaperOnlyBroker: a gate
    whose queue can be edited is a gate with a side door."""
    assert P.is_protected("src/execution/approval_queue.py")


def test_ordinary_source_is_not_fenced():
    for path in ("src/v5/pipeline.py", "backend/main.py",
                 "frontend/src/App.jsx", "src/inference/router.py",
                 "tests/test_v5.py", "docs/HANDOFF.md"):
        assert not P.is_protected(path), f"{path} should be editable"


def test_every_fenced_path_states_a_reason():
    """A refusal that says only 'denied' teaches the next session nothing."""
    for rule in P.PROTECTED:
        assert P.REASONS.get(rule), f"{rule} has no stated reason"


def test_path_separators_and_case_do_not_evade_the_fence():
    for spelling in (r"src\auth\policy.py", "./src/auth/policy.py",
                     "/src/auth/policy.py", "SRC/AUTH/Policy.PY",
                     r".\.github\workflows\security.yml"):
        assert P.is_protected(spelling), f"{spelling!r} evaded the fence"


def test_a_directory_rule_covers_new_files_beneath_it():
    """The fence is on `src/auth/`, not on a list of files in it — a new file
    added there is protected on the day it is created."""
    assert P.is_protected("src/auth/a_file_that_does_not_exist_yet.py")
    assert P.is_protected(".github/workflows/brand_new.yml")


def test_near_misses_are_not_fenced():
    """Prefix matching must not spill into neighbouring names."""
    assert not P.is_protected("src/authors/notes.py")
    assert not P.is_protected("src/execution/live_guard_notes.md")
    assert not P.is_protected("tests/test_protected_paths_helper.py")


def test_protected_hits_reports_only_the_fenced_subset():
    hits = P.protected_hits(["src/v5/pipeline.py", "src/auth/policy.py",
                             "README.md", r"src\execution\live_guard.py",
                             "src/auth/policy.py"])
    assert hits == ["src/auth/policy.py", "src/execution/live_guard.py"]


# ── who counts as ARIA ───────────────────────────────────────────────────────

def test_aria_identity_is_recognised():
    assert P.is_aria_author("ARIA", "aria@aria.local")
    assert P.is_aria_author("aria", "")
    assert P.is_aria_author("ARIA (self-modification)", "")
    assert P.is_aria_author("", "aria@localhost")


def test_a_co_author_trailer_counts_as_aria():
    """A change she wrote and a human committed for her is still her change.
    Borrowing the owner's git identity is exactly the evasion to catch."""
    assert P.is_aria_author("that_finance_guy", "owner@example.com",
                            "Tidy the auth layer\n\nCo-Authored-By: ARIA <aria@aria.local>")


def test_the_owner_is_not_aria():
    assert not P.is_aria_author("that_finance_guy", "owner@example.com",
                                "Ordinary commit touching src/auth/")
    assert not P.is_aria_author("Soundariyan Karunakaran", "s@example.com")


def test_maria_is_not_aria():
    """The name rules are generous on purpose, but not to the point of
    catching every human whose name contains the letters."""
    assert not P.is_aria_author("Maria Lopez", "maria@example.com")


# ── parsing, without needing a repository ────────────────────────────────────

def test_parse_log_splits_records_and_file_lists():
    raw = (f"{P._REC}abc123{P._FLD}ARIA{P._FLD}aria@aria.local{P._FLD}"
           f"Subject line\n\nbody{P._FLD}\nsrc/auth/policy.py\nREADME.md\n"
           f"{P._REC}def456{P._FLD}Owner{P._FLD}o@example.com{P._FLD}"
           f"Another{P._FLD}\nsrc/v5/pipeline.py\n")
    commits = P.parse_log(raw)
    assert [c[0] for c in commits] == ["abc123", "def456"]
    assert commits[0][1] == "ARIA"
    assert commits[0][4] == ["src/auth/policy.py", "README.md"]
    assert commits[1][4] == ["src/v5/pipeline.py"]


def test_format_violations_names_the_file_and_the_reason():
    v = P.Violation(sha="deadbeefcafe", author="ARIA <aria@aria.local>",
                    subject="Improve my own auth", paths=["src/auth/policy.py"])
    text = P.format_violations([v])
    assert "deadbeef" in text
    assert "src/auth/policy.py" in text
    assert "provable" in text          # the reason, not just the path
    assert P.format_violations([]).startswith("no ARIA-authored commit")


# ── the repository itself ────────────────────────────────────────────────────

needs_git = pytest.mark.skipif(
    not P.git_available(ROOT),
    reason="not a git checkout — CI covers this via "
           "scripts/check_protected_paths.py --require-git")


@needs_git
def test_no_aria_commit_has_touched_a_protected_path():
    """The assertion the whole file exists for."""
    violations = P.scan_history(ROOT)
    assert not violations, P.format_violations(violations)


@needs_git
def test_the_working_tree_is_scannable():
    """`changed_paths` is what the sandbox calls before it proposes anything;
    if it cannot read the working tree, the sandbox's check is vacuous."""
    paths = P.changed_paths(ROOT)
    assert isinstance(paths, list)


@needs_git
def test_the_fence_catches_a_real_aria_commit(tmp_path):
    """End-to-end, in a throwaway repository: an ARIA-authored commit that
    touches a protected path must be reported. Without this the suite could
    pass simply because the scan silently returns nothing."""
    repo = tmp_path / "repo"
    (repo / "src" / "auth").mkdir(parents=True)

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True, text=True)

    git("init", "-b", "main")
    git("config", "user.name", P.ARIA_AUTHOR_NAME)
    git("config", "user.email", P.ARIA_AUTHOR_EMAIL)
    git("config", "commit.gpgsign", "false")

    (repo / "README.md").write_text("innocent\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "An ordinary change")
    assert P.scan_history(repo) == [], "a harmless ARIA commit must pass"

    (repo / "src" / "auth" / "policy.py").write_text("ROLE = 'owner'\n",
                                                     encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "Grant myself owner")

    violations = P.scan_history(repo)
    assert len(violations) == 1
    assert violations[0].paths == ["src/auth/policy.py"]
    assert "Grant myself owner" in violations[0].subject
