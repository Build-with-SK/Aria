# Independent review brief

Paste the section below into a **fresh Claude Code session** — ideally a
different account — in a checkout of this repository. Do not paste anything
else from the implementing session: no summary, no changelog, no claim that
anything is fixed. The framing is the experiment.

---

## Materials the reviewer gets

- The three original audit findings (restated below, in their original terms).
- The working diff: `docs/audit/sprint-changes.diff`, plus these new files:
  `src/auth/guard.py`, `src/execution/live_guard.py`, `src/v5/vendors.py`,
  `src/v5/walkforward.py`, `src/v5/weightfit.py`, `src/v5/loop.py`,
  and the test files listed below.
- How to run the tests:

```bash
venv/Scripts/python.exe -m pytest tests/test_security_execution_auth.py tests/test_security_ibkr_status.py tests/test_security_live_gate.py tests/test_data_failover.py -v
```

New tests: `tests/test_security_execution_auth.py`,
`tests/test_security_ibkr_status.py`, `tests/test_security_live_gate.py`,
`tests/test_data_failover.py`, `tests/test_walkforward.py`,
`tests/test_weightfit.py`, `tests/test_track_record_threshold.py`.

## Materials the reviewer must NOT get

- Any statement that the issues are resolved.
- Any explanation from the implementing session of why a fix is correct.
- The CHANGELOG, which is written from the implementer's point of view.

---

## The brief itself — paste from here

You are reviewing a security/correctness fix, not the person who wrote it. Your
job is to find reasons this is incomplete, wrong, or only partially fixes the
original issue — not to confirm it looks fine.

The three original findings were:

1. **Unauthenticated execution endpoints.** Routes that place, modify or cancel
   orders (paper or live) were reachable without credentials.
2. **IBKR adapter order-status misreporting.** The adapter reported order state
   from internal assumption rather than from IBKR's actual order and fill
   events.
3. **Live-account auto-execute bypass risk.** A live account might reach the
   broker without passing through the manual approval queue.

For each finding:

1. Try to construct a scenario where the fix does not apply — a different
   endpoint, a different order-state transition, an edge case in auth.
2. Check whether the new tests actually exercise the vulnerable path, or just a
   convenient adjacent one.
3. Check whether the fix could regress silently — is it enforced structurally
   (middleware, type system, schema) or only by convention (a check someone
   could forget to call)?

Report pass/fail per finding, not an overall verdict. If you cannot find a
hole, say so plainly — but the default assumption should be that something was
missed.

**Output:** a written report, one section per original finding, each ending in
**PASS / FAIL / PARTIAL**. Anything not PASS goes back for another pass before
this sprint is considered closed.

Suggested starting points, offered as places to look rather than as an
account of what was done: `src/auth/policy.py`, `src/auth/guard.py`,
`src/auth/hardening.py`, `backend/main.py` (middleware and route decorators),
`src/execution/ibkr_broker.py`, `src/execution/order_manager.py`,
`src/execution/approval_queue.py`, `src/execution/live_guard.py`,
`src/desk/auto_executor.py`, `src/desk/position_manager.py`.
