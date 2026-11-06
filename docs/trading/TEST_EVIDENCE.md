# Test evidence

## Baseline — revision 58f5f25

Windows Python 3.11.8, pytest 9.1.1, Pydantic 2.13.4; shared existing venv,
isolated worktree `aria-multiasset-trading`, no production server started.

`..\trading-intelligence-system\venv\Scripts\python.exe -m pytest -q --disable-warnings --tb=short`

Actual result: **3321 passed, 10 skipped, 1 failed**, 1 warning, 401.49 seconds.
Failure: `tests/test_stabilization.py::test_the_universe_audit_reports_without_repairing`:
audit saw 29 rows while the initial session fallback contained 16. Earlier tests
had added instruments to the session-wide temporary master. The fixture itself
still contains 16 rows. This is test-order contamination, not a market-data defect.

Correction: audit a newly built fallback database in this test's own `tmp_path`.
Keep exact row count, name/duplicate/identity and byte-for-byte no-write assertions.
`pytest -q tests/test_stabilization.py --disable-warnings --tb=short`:
**22 passed in 1.04s**. The complete final run is recorded below when available.

Existing isolation redirected 283 ARIA modules during baseline; no blocked
production writes reported. Ten skips and the warning remain baseline limitations;
no claim that a skipped external integration passed.
