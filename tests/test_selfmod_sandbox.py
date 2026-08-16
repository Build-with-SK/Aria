"""
tests/test_selfmod_sandbox.py
=============================
THE SANDBOX (docs/ARIA_NEXT_SESSION.md step 2).

The sandbox is a refusal engine, so nearly every test here asserts a refusal.
The one acceptance test matters just as much: a gate that refuses everything
is not a gate, it is an outage, and a future session would route around it.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.selfmod import protected as P
from src.selfmod import sandbox as S

ROOT = Path(__file__).resolve().parent.parent

needs_git = pytest.mark.skipif(not P.git_available(ROOT),
                               reason="not a git checkout")

GOOD = S.Rationale(
    what_changed="Lowered the carry module's bull threshold from 0.60 to 0.55.",
    why="Its information coefficient is negative while its edge is negative too; "
        "the threshold is the part the walk-forward says is miscalibrated.",
    evidence="data/v5/walk_forward.json run 2026-08-02: carry IC -0.215 over "
             "31 measured modules, 48 effective observations after the breadth discount.",
    expected_improvement="Fewer directional calls from a module that ranks "
                         "backwards; no change expected to the other 40 modules.",
)


# ── the rationale ────────────────────────────────────────────────────────────

def test_an_empty_rationale_is_four_refusals():
    problems = S.Rationale().problems()
    assert len(problems) == 4
    assert any("What changed" in p for p in problems)
    assert any("evidence" in p.lower() for p in problems)


def test_placeholders_do_not_count_as_a_rationale():
    r = S.Rationale(what_changed="TODO", why="n/a", evidence="-",
                    expected_improvement="see diff")
    problems = r.problems()
    assert len(problems) == 4
    assert all("placeholder" in p for p in problems)


def test_a_one_word_answer_is_refused():
    r = S.Rationale(what_changed="Changed a number", why="It was wrong",
                    evidence="A backtest", expected_improvement="Better results")
    assert len(r.problems()) == 4       # all four are under the length floor


def test_a_real_rationale_passes():
    assert GOOD.problems() == []


def test_the_pr_body_answers_all_four_questions():
    body = GOOD.render(branch="aria/x", tests=S.TestResult(ran=True, passed=518),
                       changed=["src/v5/modules/carry.py"])
    for heading in ("What changed", "Why", "What evidence prompted it",
                    "What this should improve", "Tests", "Files"):
        assert f"## {heading}" in body
    assert "carry.py" in body
    assert "only the owner" in body     # who may merge is stated in the PR itself


# ── reading the suite's result ───────────────────────────────────────────────

def test_green_output_parses():
    r = S.parse_pytest_output("....\n518 passed, 3 skipped in 41.20s", 0)
    assert r.green and r.passed == 518 and r.skipped == 3


def test_red_output_is_not_green():
    r = S.parse_pytest_output("F...\n2 failed, 516 passed in 44.10s", 1)
    assert not r.green and r.failed == 2
    assert "RED" in r.summary()


def test_a_nonzero_exit_with_no_failures_is_not_green():
    """Exit 4 is a usage error, exit 5 is 'no tests collected'. Both report
    zero failures, and neither is a green suite."""
    r = S.parse_pytest_output("no tests ran in 0.01s", 5)
    assert not r.green
    assert "exited 5" in r.note


def test_a_suite_that_never_ran_is_not_green():
    assert not S.TestResult(note="skipped").green


# ── the checks, against real repositories ────────────────────────────────────

def _git(repo: Path, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A miniature repo with a green suite on `main` and an ARIA branch."""
    r = tmp_path / "repo"
    (r / "tests").mkdir(parents=True)
    (r / "src").mkdir()
    (r / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n",
                                            encoding="utf-8")
    (r / "src" / "thing.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.name", P.ARIA_AUTHOR_NAME)
    _git(r, "config", "user.email", P.ARIA_AUTHOR_EMAIL)
    _git(r, "config", "commit.gpgsign", "false")
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "base")
    _git(r, "checkout", "-b", "aria/change")
    return r


@needs_git
def test_a_clean_proposal_is_accepted(repo):
    """The acceptance case. Without it the suite could pass with a sandbox
    that refuses everything, which is an outage wearing a gate's clothes."""
    (repo / "src" / "thing.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "commit", "-am", "Change a value")

    result = S.check(repo, branch="aria/change", rationale=GOOD,
                     owner_repo=tmp_owner(repo), test_timeout=300)
    assert result.ok, result.describe()
    assert result.tests.green
    assert result.changed == ["src/thing.py"]


def tmp_owner(repo: Path) -> Path:
    """Any path that is not the worktree — the owner's checkout is elsewhere."""
    return repo.parent / "owners-checkout"


@needs_git
def test_working_on_main_is_refused(repo):
    _git(repo, "checkout", "main")
    result = S.check(repo, rationale=GOOD, run_tests=False,
                     owner_repo=tmp_owner(repo))
    assert not result.ok
    assert any("never on main" in r for r in result.refusals)


@needs_git
def test_the_owners_working_tree_is_refused(repo):
    result = S.check(repo, branch="aria/change", rationale=GOOD,
                     run_tests=False, owner_repo=repo)
    assert any("owner's working tree" in r for r in result.refusals)


@needs_git
def test_touching_a_protected_path_is_refused(repo):
    (repo / "src" / "auth").mkdir()
    (repo / "src" / "auth" / "policy.py").write_text("ROLE='owner'\n",
                                                     encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "Adjust the auth layer")

    result = S.check(repo, branch="aria/change", rationale=GOOD,
                     run_tests=False, owner_repo=tmp_owner(repo))
    assert not result.ok
    assert any("src/auth/policy.py" in r for r in result.refusals)
    # and the history scan reports it independently of the diff check
    assert any("provable" in r for r in result.refusals)


@needs_git
def test_deleting_a_test_is_refused(repo):
    """The cheapest way to make a red suite green is to delete the test."""
    (repo / "tests" / "test_ok.py").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "Remove a test")

    result = S.check(repo, branch="aria/change", rationale=GOOD,
                     run_tests=False, owner_repo=tmp_owner(repo))
    assert any("deletes a test file" in r for r in result.refusals)


@needs_git
def test_a_red_suite_keeps_the_branch_in_the_sandbox(repo):
    (repo / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert False\n", encoding="utf-8")
    _git(repo, "commit", "-am", "Break a test")

    result = S.check(repo, branch="aria/change", rationale=GOOD,
                     owner_repo=tmp_owner(repo), test_timeout=300)
    assert not result.ok
    assert any("not green" in r for r in result.refusals)
    assert result.tests.failed == 1


@needs_git
def test_skipping_the_suite_is_itself_a_refusal(repo):
    result = S.check(repo, branch="aria/change", rationale=GOOD,
                     run_tests=False, owner_repo=tmp_owner(repo))
    assert any("green is a precondition" in r for r in result.refusals)


@needs_git
def test_every_refusal_is_reported_at_once(repo):
    """A gate that reveals one problem per run turns a fix into a guessing
    game."""
    _git(repo, "checkout", "main")
    (repo / "src" / "auth").mkdir()
    (repo / "src" / "auth" / "guard.py").write_text("x=1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "several problems at once")

    result = S.check(repo, rationale=S.Rationale(), run_tests=False,
                     owner_repo=repo)
    text = result.describe()
    assert "REFUSED" in text
    assert len(result.refusals) >= 6     # branch, tree, fence, rationale ×4, suite


# ── the worktree and the PR ──────────────────────────────────────────────────

def test_a_workspace_on_main_is_refused():
    with pytest.raises(ValueError):
        S.create_workspace("main")
    with pytest.raises(ValueError):
        S.create_workspace("")


def test_workspace_paths_are_sanitised():
    assert S.workspace_path("aria/fix-carry").name == "aria-fix-carry"
    assert ".." not in S.workspace_path("../../etc/passwd").name


def test_a_pr_needs_a_human_go_ahead():
    ok = S.ProposalCheck(branch="aria/x", worktree="/tmp/x")
    with pytest.raises(PermissionError, match="human go-ahead"):
        S.open_pull_request(ok, GOOD, title="t")


def test_a_refused_proposal_cannot_become_a_pr():
    bad = S.ProposalCheck(branch="aria/x", worktree="/tmp/x",
                          refusals=["the suite is not green"])
    with pytest.raises(PermissionError, match="refused proposal"):
        S.open_pull_request(bad, GOOD, title="t", human_approved=True)


def test_refused_proposals_are_recorded_too(tmp_path, monkeypatch):
    """The refused half of the ledger is the more interesting half: it is the
    record of what she tried to change and what stopped her."""
    monkeypatch.setattr(S, "PROPOSALS", tmp_path / "proposals")
    bad = S.ProposalCheck(branch="aria/x", worktree="/tmp/x",
                          refusals=["touches a protected path: src/auth/policy.py"])
    path = S.record_proposal(bad, GOOD)
    assert path.exists()
    import json
    d = json.loads(path.read_text(encoding="utf-8"))
    assert d["check"]["ok"] is False
    assert d["merged"] is False
    assert "src/auth/policy.py" in d["check"]["refusals"][0]
