# Codex brief — launch readiness, 2026-10-16 (deadline 22:00)

Written by Claude Code at the owner's direction. The authority is
`docs/collab/WORK_PLAN.md` §5 (gates L1–L8). You own L2, L7, L8 and the
frontend half of L4. Claude owns L1, L3, L5, L6, the full suite, merging and
the runtime restart.

## Start

1. In your folder (`Documents\aria-codex`, branch `codex/work`):
   `git merge aria/integrity-upgrade` — it now contains S0.1 (the 2026-10-09..15
   backlog, commits `10e0fc8..eb093cc`) and TECH-10 (consensus from valid
   horizons, FX daily calendar). Log one "started" line in `log-codex.md`.
2. Python: `..\trading-intelligence-system\venv\Scripts\python.exe`, tests only
   (test isolation redirects data writes). Never start a server on ARIA's ports
   (8000, 3000, 8010); never write the primary folder's `data/`.

## Tasks, in order

**L2 — every page works.** For each route in `frontend/src/App.jsx`
(`/brain /research /market /portfolio /strategies /daily-report /track-record
/system`) render it offline the way `frontend/scripts/verify-technical-dashboard.mjs`
does, against (a) a realistic API payload, (b) empty/withheld payloads
(`null`, `[]`, `summary: null`, `consensus: null`), (c) a failed request. Fix
anything that throws, renders `NaN`/`undefined`/`[object Object]`, or shows a
blank page instead of an explicit "unavailable"/"withheld" state. Add the
checks to a script under `frontend/scripts/` so they are repeatable. Then
`npm run build` into a STAGING outDir (not `frontend/dist`, which is served)
and report its size and warnings. Inline styles only, no new npm packages.

Real-browser findings from Claude (scratch copy, all 8 routes, 2026-10-16 ~17:30):
every page renders, no thrown error, no `NaN`/`undefined`/`[object Object]` on
screen. Two defects on EVERY page, both yours to fix:
- `frontend/src/main.jsx:38` registers the service worker at `/sw.js`; the app is
  served under `/app/`, so it 404s ("bad HTTP response code (404) … fetching the
  script") and the PWA never installs. Use `import.meta.env.BASE_URL + 'sw.js'`
  (the file is built to `/app/sw.js`); check the worker's scope still covers `/app/`.
- The Google Fonts stylesheet (JetBrains Mono + Inter) is blocked by the app's CSP
  (`style-src 'self' 'unsafe-inline'`), so fonts silently fall back. Prefer dropping
  the external request and using a local fallback stack (no new packages); if you
  want the fonts, write the exact origins in "For Claude" — Claude owns the CSP.
(The 403s in that run are expected: the scratch copy has no owner session.)

**L7 — someone new can run it.** Add a "Run ARIA" section to `README.md`:
prerequisites (Python venv, Node, optional Ollama), start commands, the
`/app/` base path, owner sign-in (token from `.env`, never printed), PAPER is
the default and live trading is gated, where data lives, how to run tests.
Include a plain "What ARIA does NOT claim" paragraph: no measured trading edge,
descriptive technical consensus only, no financial advice. Only facts you can
verify in the repo.

**L8 — repo tidy.** `git rm` the S1.6 items you marked "remove" on 2026-10-11
(`ARIA_BRAIN_BUILD_PROMPT.md`, `ARIA_V3_BUILD_PROMPT.md`, `ATLAS_BUILD_PROMPT.md`,
`ATLAS_V2_BUILD_PROMPT.md`, `ARIA_FINAL_COMPLETION_REPORT.md`,
`ARIA_FINAL_REPORT.md`, `HANDOFF_TOMORROW.md`) after re-checking that nothing
references them. Keep everything you marked "keep".

**If time remains: S1.5** — frontend freshness panel from `/api/data/freshness`
(one row per dataset with age and `pipeline_run` step status), only if
`System.jsx` does not already show it; report what exists first.

## Rules (unchanged)

- Do not edit `backend/`, `src/` outside your areas, `WORK_PLAN.md` or
  `log-claude.md`. Need an endpoint or a backend fix? Write it in your log under
  "For Claude".
- Never touch kill switch, risk, execution, approval or auth code paths; never
  call `approve_and_execute`; ARIA stays PAPER.
- No secrets, `.env`, vault or account data read, printed or committed.
- One commit per gate, each with its tests/checks passing. No pushes, no merges
  into `aria/integrity-upgrade` — Claude reviews and merges.
- No claim without a measurement. If a gate cannot be met tonight, say so and why.

## Finish

Final `log-codex.md` entry: per gate — changed / measured / open / for Claude.
Your last message should list your commit hashes.
