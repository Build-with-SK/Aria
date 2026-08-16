"""
src/selfmod/
============
The fence and the sandbox — the two things that have to exist BEFORE ARIA is
allowed to change her own source.

`protected.py` says what she may never touch. `sandbox.py` says how a change
she proposes reaches the owner: a branch off `main`, in a worktree that is not
his, a green suite, and a written rationale he reads before merging.

Neither module is hers to edit. `protected.py` protects itself; see the
PROTECTED tuple and `tests/test_protected_paths.py`.
"""
