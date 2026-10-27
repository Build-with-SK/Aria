# ARIA release candidate — reconciliation session 2026-10-26/27

**Recommendation: BLOCKED for production cutover or live trading.** Continue
isolated verification. No production merge, deployment, model replacement,
broker write or live execution was performed. These results describe the
candidate source, not the running installation.

## Source and reconciliation

- Exact verified source: `6c30df588b552e21bf8166f81f5963ca799b7d2f`.
- Review branch: `codex/aria-release-reconcile-20261026`.
- Initial primary: `aria/integrity-upgrade` at `b5b47f2`; its eight modified
  tracked data files and two untracked runtime files were preserved in place.
- Candidate includes final integration `edc2796`, operational work `e4e4de1`,
  Claude review/probes `5270df6`, and Claude's exact perception/test change
  from `d7e9362` (`8da6aa3`). Repairs are `7d0d3a1`; follow-up is `6c30df5`.
- Only isolated development branches were integrated. `main` and the primary
  integration/runtime branch were not moved. Other agents' worktrees and
  uncommitted evaluation work were not overwritten.

Full branch refs, ancestry counts, worktree statuses and manifest hashes are
retained in private `aria/reconciliation.json`. Public summaries and exact
resolved Python dependency versions are in [ARIA_EVIDENCE.json](ARIA_EVIDENCE.json).
Later report-only commits do not change the verified source implementation.

## Repairs

The halt now cancels an observed order by the correct verified broker/client
identity and reports refused cancellations as failures. Cancelled unfilled
desk exits are reconciled before same-tick protection repair. Protection sizing
uses broker holdings and reduce-only room; existing residual protection is kept
while working or uncertain exits reserve the rest. Slow cancellation gets a
bounded same-tick retry, with an urgent recovery message if still unconfirmed.

Signals require their actual bar date, with invalid, missing, future or old row
evidence withheld regardless of file timestamps. ML resume also requires the
same last bar. New generated timestamps use UTC offsets; invalid/future ML age
cannot crash chat context. Claude's horizon-age filtering is integrated.

The pipeline has one OS-level writer lock across scheduled, CLI and API entry
points. Loopback-inferred ownership also requires a valid loopback Host.
Refused IBKR proposals retain the IBKR route; unavailable or absent IBKR order
status remains UNKNOWN. Consultation credentials require HTTPS outside loopback
and never follow redirects. No human approval, kill switch, pre-trade gate,
reduce-only check, reservation or UNKNOWN hold was relaxed.

## Measured verification

| Check | Result |
| --- | --- |
| Frozen full suite, private Windows CPython 3.14.3 environment | **3,585 passed, 12 skipped, 0 failed**, 481.30 s; source fingerprint unchanged |
| Warnings | 2,475 retained; includes Pandas/asyncio dependency deprecations |
| Full GET-route sweep under isolation | **3 passed**, no server-error assertion failures |
| Follow-up protection/freshness/IBKR checks | **101 passed** in the fresh environment |
| Earlier affected groups | 224, 106, 39 and 83 passing checks; chronological failures retained separately |
| Frontend | Patched production build and all four offline verifiers pass |
| Actual Chromium | Actual patched React build, synthetic read-only APIs; route/error/recovery evidence retained privately; no real broker or owner session |
| Fatal Ruff; protected-history fence | PASS |
| Six-module mypy | PASS after supplying missing `types-requests` stubs; initial missing-stub failure retained |
| pip check / fresh app dependency audit | PASS / **0 known advisories** |
| npm audit | **0 advisories** after patching Vite to 8.2.4 and follow-redirects to 1.16.1 |
| Bandit | **FAIL, exit 1: 42 retained findings**, 4 high / 38 medium; not suppressed or called a clean scan |
| Reachable-history Gitleaks 8.30.1 | Exit 1, one assessed synthetic logging-redaction fixture; no unresolved credential; no suppression added |
| Independent Claude review | **APPROVE WITH LIMITATIONS** on exact `6c30df5`; [verdict](../../collab/CLAUDE_ARIA_RELEASE_REVIEW_20261026.md) |

The source Bandit assessment inherited from `ARIA_SECURITY_READINESS.md` remains
scoped, not a blanket exemption. The secret finding is a test-generated vendor
URL for verifying log redaction, not an owner credential. All raw scans are
retained. The patched dependency versions address the reported
[follow-redirects](https://github.com/advisories/GHSA-q3jh-wrc6-jgq8) and
[Vite](https://github.com/advisories/GHSA-587h-7wmj-h5pm) advisories.

Every final skip and intermediate failed test ID is in `ARIA_EVIDENCE.json`.
Skips include absent production ledgers/reference data, POSIX-only protocols,
the separate GET sweep and the opt-in cold profile. None counts as acceptance.
No production ledger was copied to make a test pass. The full suite includes
unit, integration, contract, security, regression, failure/restart/recovery and
isolated backend/frontend/proxy acceptance tests; it does not certify a venue.

## Reproduce safely

In a fresh isolated checkout of the exact source, create a separate venv using
the intended, supported interpreter and install the existing manifests:

```powershell
python -m venv venv
venv\Scripts\python.exe -m pip install --upgrade pip setuptools
venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-ci.txt
npm --prefix frontend ci --ignore-scripts
venv\Scripts\python.exe scripts/release_verify.py --product aria --checks inventory dependencies frontend full --output <new-directory-outside-checkout-and-data>
```

The verification runner records source fingerprints, resolved versions, commands,
exit codes and every JUnit failure/skip. It installs nothing, starts no production
server and places no order. Evidence directories must be new and outside source
and `data/`. Existing raw evidence is under the owner's private session evidence
directory `20261026/aria`; it is deliberately not published with source.

For the exact tested Windows environment, use
`docs/release/2026-10-26/ARIA_WINDOWS_REQUIREMENTS.txt` instead of resolving the
floating manifests again. That verification lock is not a deployment approval
or a cross-platform lock. The exact tested interpreter was 3.14.3.
A prior private 3.14.8 installation is
missing DLLs and failed to execute; normal managed-runtime acquisition also
reported no matching download. Neither failure is a passing runtime gate. The
new private package audit excludes optional heavy deployment packages and does
not certify CPython/OpenSSL/Expat or the deployed host. The deployed environment
was not upgraded or replaced.

## Open defect and acceptance register

| Gate / limitation | State and closure evidence |
| --- | --- |
| Deployed runtime and optional dependency security | BLOCKED: approve and verify a patched actual target interpreter, complete optional dependency audit and target-stack regression |
| Historical legacy/UNKNOWN reconciliation | BLOCKED: original broker/account identities and execution/statement evidence; never clear or resend on absence/404 |
| New paper-venue acceptance | BLOCKED: separately approved account, instrument, size, time and exact order/cancel operations; actual lost-ack/partial/cancel/restart/fees evidence |
| IBKR gateway/permanent identities | BLOCKED: approved paper gateway session and original/permanent identity evidence |
| Fees and partial-exit realised P&L | OPEN: partial fills and net fee accounting still need implementation/reconciliation; fixture gross P&L is not broker net truth |
| Unlinked reflex UNKNOWN | BLOCKED: original queue binding or an explicitly reviewed owner recovery procedure |
| Signal availability policy | OPEN owner decision: one undated/illiquid ticker holds the universe-wide 30 h gate; weekends/holidays also fail closed. No threshold/calendar relaxation was made |
| Protection edge cases | OPEN: oversized/partially filled protections and long venue cancellations; no claim of continuous protection during an unconfirmed cancellation |
| Monitoring | OPEN: urgent cancellation errors currently share the ordinary tick summary; dedicated alert priority and a shared per-tick wait budget remain review findings |
| Hosted deployment, TLS, supervision, uptime | BLOCKED: approved target and actual cutover/soak evidence; local fixtures are not hosted acceptance |
| S2.2/S2.3 measurement integration | OPEN: preserve/review the original uncommitted source-level work before integration |
| Historical data / model benchmark | No new research model trained. No validated point-in-time, independent, cost-adjusted edge or profitability claim |
| Owner release approval | NOT GRANTED |

## Deployment, recovery and rollback — preparation only

1. Obtain explicit owner cutover approval after the open security/data/state
   gates are reviewed. Keep live execution disabled. Do not run an external
   paper order merely because this software suite is green.
2. Record the running source/interpreter/dependency identities and take validated
   state/audit/database backups before the approved single-instance stop.
   Preserve original broker IDs, queue claims and unresolved orders.
3. Provision the approved separate environment and staged build. Review state
   migration and compatibility before touching the live stores. Start only the
   approved instance, initially with autonomy disarmed, and verify authenticated
   health/readiness, mode, risk/kill controls, identity and recovery.
4. For rollback, stop the candidate and preserve its **current** state/audit plus
   broker evidence. Restore only an approved compatible code/environment/build
   snapshot. Never roll a stale queue/position snapshot over later external
   effects, erase an UNKNOWN claim, or replay a legacy order to unblock a lane.
5. Unreadable or uncertain state stays held until reconciled by the owner.
   Retain quarantine/original evidence and verify one writer after recovery.

These are reviewable instructions, not permission to merge, deploy, trade or
replace a production model. This candidate is not declared 100% complete.
