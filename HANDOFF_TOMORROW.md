# ARIA — session handoff

**Updated 2026-08-03. Paste this whole file as the first message next session.**

---

## Where things stand

208 tests pass. Production build is clean. All twelve nav destinations render with
no console errors, on desktop and at 375px. **Everything is committed** — the tree
is clean and the git history is safe to push.

---

## ✅ The blocker is cleared

The six third-party academic documents are **gone from all of git history**.

- Backup taken first: `Documents\aria_backup_pre_rewrite_20260803` (14,250 files, 4.09 GB,
  full 62-commit history intact). Delete it once you are satisfied.
- `git filter-repo` rewrote 70 commits. `git rev-list --all --objects | grep -iE "COMM068|NW_CW|Network_security"`
  returns nothing, and no `.pdf`/`.docx` exists anywhere in history.
- `.git` went from **507 MB → 1.5 MB**.
- Tests and build re-verified after the rewrite.

The repo still has no remote. It is now safe to add one and push.

---

## A feature that had been lost, and is back

`src/data/fx_monitor.py` — the **GBP/INR remittance monitor** — had been deleted along
with `/api/fx/gbpinr`, its targets endpoint and its background thread. The currency work
removed it as collateral; `/api/fx/rates` is display conversion and does not replace it.
It is a headline feature in the README and your own targets (₹132 high / ₹125 low) and
rate history back to 27 July were still sitting in `data/fx_state.json`.

Restored and verified live (GBP/INR 128.23, advice and alerting working).

The half of that change that was *correct* is kept: `_start_quant_lab` used to be
launched from a line after `while True:` inside the FX loop, so it was unreachable and
the quant researcher never actually started. It is now its own daemon thread.

**Rule worth keeping:** CLAUDE.md says only ADD endpoints, never remove. A removed
endpoint is the signal that a feature is being dropped — `git diff backend/main.py | grep "^-.*@app\."`
is a cheap check before any commit.

---

## Built this session

1. **Command palette** (`Ctrl/⌘+K`, or the rail's search button). Matches page names,
   paths, and pre-restructure names — someone looking for "nexus" finds Research. Type a
   ticker to go straight to `/research?symbol=`. The symbol leads only when nothing in the
   app is named after the query, since most aliases are short enough to look like tickers.

2. **Glossary ~40 → ~85 terms**, wired into the tables that were still bare: Markets/Signals,
   Portfolio, Quant Lab, the Desk's positions and analyst scoreboard, and all four V5 tables.

3. **Mobile fixes** — found by measuring at 375px, not by eye:
   - Tab rows did not wrap or scroll. Since the restructure put merged pages *behind* tabs,
     that made whole pages unreachable: Track Record showed 3 of its 5 tabs.
   - `table { overflow-x: auto }` existed and did nothing — the panel around each table is a
     flex/grid child whose default `min-width: auto` let it grow to the table's full width,
     leaving the table nothing to scroll. Columns past the fold were clipped with no scrollbar.
   - The Signals filter row and the core's talk row overflowed; **SEND** sat off the edge, so
     ARIA could not be messaged from a phone at all.

---

## Next — suggested order

1. **Add a remote and push.** Nothing blocks it now.
2. **Mobile pass on the remaining page internals.** The shell, tables, tab rows and control
   rows are fixed. Not yet checked at 375px: the V5 module table and the Desk debate theatre
   *with data loaded* — both only render after an analysis runs, so an empty-state audit
   cannot see them. Run a V5 analysis, then measure.
3. **Charts on mobile.** Recharts containers were given `min-width: 0` but were not audited
   for legibility at 375px — axis labels are the usual casualty.
4. **Loose ends**
   - `data/memory/long_term_memory.json` is tracked: one NVDA decision from 20 June, no
     sensitive content. Kept deliberately as demo data — it shows the output format well.
   - `data/desk_config.json` still has `auto_execute: true` locally. Gitignored, so fresh
     clones are safe. Left alone deliberately — your call.
   - README now describes the twelve destinations, V5 and Track Record.

---

## Things that will bite you if you forget them

| Gotcha | Why |
|---|---|
| `.gitignore` has **no trailing comments** | `data/v5/  # note` makes the whole line literal and silently ignores nothing |
| Legacy backup dirs are gitignored now | They were untracked-but-on-disk, so `git add -A` would have silently re-added every one |
| London quotes in **pence** (`GBp`) | Treating it as GBP is a 100× error. Always go through `src/data/currency.py` |
| yfinance `dividendYield` units changed between versions | Use `marketdata.dividend_yield_pct()`, which derives from `dividendRate/price` |
| `^TNX` is already a percentage | Do not divide by 10 |
| `max(lo, min(hi, NaN))` returns `hi` in JS/Python | A division by zero became a maximally bullish score once. `_util.clamp` now propagates NaN |
| Preview pane hidden ⇒ no compositing | CSS transitions freeze at their start value; screenshots fail outright. Measure with `transition: none` |
| `min-width: auto` on flex/grid children | The single most common cause of mobile overflow here. It silently defeats `overflow-x: auto` on anything inside |
| Backend on :8000 needs a restart to pick up changes | It runs without `--reload` unless started from `.claude/launch.json` |

---

## How to run

```bash
START_ARIA.bat
```
Backend on :8000, frontend on :3000. Tests: `venv\Scripts\python.exe -m pytest tests/ -q`.
Frontend build: `cd frontend && node node_modules/vite/bin/vite.js build`.
