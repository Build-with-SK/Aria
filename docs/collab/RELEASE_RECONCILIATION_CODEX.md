# Release reconciliation — 2026-10-26

Status: engineering in progress; neither product is approved for release.

ARIA primary b5b47f2 has eight modified tracked data files and two untracked
runtime files. SENTINEL primary 7bccea4 has eleven modified tracked runtime
files and three untracked paths (including two collaboration notes). Contents
are preserved in place, not staged/copied into candidates. Remote refs were
fetched without publication. SENTINEL neural v1.1 advanced to 1ca128c; the
existing RC already contains schema 1dfbd9b, training 7f31d64, automation
0d63139, security proposal 9734001 and compatibility 9e0a143. Its two new
upstream commits require reconciliation. ARIA operational e4e4de1 contains final
integration edc2796; Claude review 5270df6 adds reports/probes only and is merged
into this isolated branch. Integration b5b47f2 is already an ancestor.

## Defect register

| ID | Priority | State | Acceptance |
| --- | --- | --- | --- |
| ARIA F-01 | HIGH | Reproduce | Halt cancels resting entry by verified identity; refused cancel is failed, never protective. |
| ARIA F-10 | HIGH | Reproduce | Old/missing/invalid/future signal row dates fail freshness regardless of file mtime. |
| ARIA F-02 | MEDIUM | Reproduce | Confirmed cancelled exit reconciled before same-tick bracket repair; UNKNOWN never guessed terminal. |
| ARIA F-09 | MEDIUM | Reproduce | Broker position size drives protection; undersized stops repaired safely. |
| ARIA F-06 | MEDIUM | Reproduce | Future ML timestamps report stale without source-context exception; new timestamps have UTC offsets. |
| SENTINEL RC | HIGH | Verify | Final suite, exact tool catalogs, JS/input egress, approvals/restart/cancellation and independent source review. |
| SENTINEL model | RELEASE GATE | Open | Completed controlled training and independent safety/quality evaluation; smoke is nonpromotable. |
| University runner | EXTERNAL | Unverified | Authorized host/resource/session identity and permitted Bubblewrap diagnostics. No bypass. |
| Desktop focus | EXTERNAL | Historical limited evidence | Attended dedicated fixture only; no forced foreground or system-setting changes. |

External venue acceptance, historical UNKNOWN reconciliation, IBKR, net fees,
hosted acceptance, optional dependency security and measured ARIA edge remain
open as specified by prior reports. Historical test counts are not fresh passes.


## Final local defect disposition — 2026-10-27
The table above is the initial reconciliation inventory, not current repair
status. ARIA F-01/F-02/F-03/F-04/F-05/F-06/F-07/F-08/F-09/F-10/F-15 and S-04
repairs are verified at 6c30df5 with3585 passed / 12 skipped. SENTINEL reviewed
network/approval/recovery/provenance repairs are verified at f3a0a8d with
2243 passed / 16 skipped. Both scoped Claude verdicts APPROVE WITH LIMITATIONS.
See the respective docs/release/2026-10-26 packet for remaining defects and
acceptance gates. SENTINEL dependency audit41 advisories/13 packages remains a
blocker; no-fix advisories are not silently waived. University/desktop sessions,
model qualification, external venue/state/fees/host acceptance and owner release
approval remain open. The local suites do not establish whole-project 100%.
