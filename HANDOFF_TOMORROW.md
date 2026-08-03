# ARIA — session handoff

**Written 2026-08-02, end of session. Paste this whole file as the first message tomorrow.**

---

## Where things stand

208 tests pass. Production build is clean. Twelve nav destinations all render with no
console errors. Everything below is committed to the working tree but **not yet committed
to git** — see the blocker first.

---

## 🚨 THE ONE BLOCKER — do this before anything else

Six academic documents belonging to a **third party** (a named individual, with their student
ID in the filename) were committed in the very first commit `686540e` and are still in git
history:

```
frontend/COMM068_Manoj_Sivakumar_6967280_Final.pdf
frontend/COMM068_Manoj_Sivakumar_6967280_Final_Edited.docx
frontend/COMM068_Manoj_Sivakumar_Revised_20_pages.pdf
frontend/NW_CW.docx
frontend/NW_CW_cleaned_20_pages.docx
frontend/Network_security.pdf
```

Already done: deleted from disk, untracked from HEAD, `*.pdf`/`*.docx` added to `.gitignore`.
**Still true: anyone who clones the repo can recover them from history.** They are also most
of the 507 MB `.git` directory.

The repo has **no git remote and has never been pushed**, so a history rewrite is clean and
safe right now. It was not run because it is destructive and irreversible — it needs an
explicit decision.

```bash
pip install git-filter-repo
git filter-repo --path frontend/COMM068_Manoj_Sivakumar_6967280_Final.pdf --path frontend/COMM068_Manoj_Sivakumar_6967280_Final_Edited.docx --path frontend/COMM068_Manoj_Sivakumar_Revised_20_pages.pdf --path frontend/NW_CW.docx --path frontend/NW_CW_cleaned_20_pages.docx --path frontend/Network_security.pdf --invert-paths
```

**Do not push to GitHub until this is done.**

---

## What was built across this session

### 1. ARIA V5 — the research platform (`src/v5/`)
41 independently-callable research modules across 7 families → ensemble synthesis →
meta-reasoning → risk gate → self-audit → learning loop. Every module returns bull/bear/neutral
summing to 100, a real confidence interval, sourced evidence, and its own declared weaknesses.
Most price/quant modules state a condition and ask the instrument's own history how often that
condition was followed by a positive return, so the interval is Wilson on a genuine observation
count.

Key semantics: **confidence = P(direction is correct)**, so it lives in [0.5, 1.0]. Penalties act
on the *edge* (distance from a coin flip), never on the probability. The trading bar is 55%.

### 2. Track Record (`/track-record`, `src/v5/track_record.py`)
Merges the four labelling loops (V5 predictions, desk closed trades, technical tracker, brain
training data) and adds **calibration** — Brier score, skill vs a coin flip, and ECE. Refuses to
report below 20 resolved calls or 5 per bucket. Exports resolved predictions as supervised JSONL.

### 3. Navigation restructure — 22 pages → 12
Merged pages became **tabs inside hub components** that render the original page components
unchanged, so any merge is reversible. Every pre-restructure path still redirects.

### 4. Live prices
`universe.quote` served a 15-minute-cached **daily close** — it could not move intraday, which is
why prices looked frozen. New `src/data/live_quote.py` + `/api/quote/live/{symbol}` uses yfinance
`fast_info` + 1-minute bars and returns `market_state` (OPEN/CLOSED derived from the age of the
last bar), tick timestamp, day range and volume. Header polls 10s open / 60s closed.

### 5. Local currency
Detected from timezone then locale, overridable. Two traps handled: prices here are **native, not
USD** (converting an NSE price as USD is ~88× wrong), and **London quotes in PENCE** — a £15.76
share was displaying as £1,576. Never converts a value whose native currency is unknown.

### 6. Research page
Explorer + Nexus merged into `/research` — one search, Overview and Deep tabs, URL-driven
(`?symbol=&view=`). Technical + Fundamental analysis panels built on one V5 call split by family,
plus the multi-timeframe indicator scan and ATR-based entry/stop/target.

### 7. Shipping readiness (today)
- **ErrorBoundary** — there was none; one throwing component blanked the whole app.
- **ConnectionBanner** — a stopped API used to fail differently on every page.
- **Responsive shell** — layout offsets were hard-coded 200px inline, leaving ~175px of content
  on a phone. Now CSS vars with an off-canvas rail under 780px.
- **Settings dialog** — currency + text size/contrast/motion consolidated behind one button.
- **Accessibility** — text size (zoom 1.0–1.55), high contrast (default `--muted` was ~2.5:1,
  below WCAG AA; high mode ~9:1), reduced motion, and a global `:focus-visible` ring (there was
  no visible keyboard focus at all).
- **Code splitting** — initial bundle **864 KB → 273 KB** (238 → 90 KB gzipped).
- **Glossary** (`components/Term.jsx`) — plain-English explanations on hover/focus for ~40 terms.
- **Staged progress** for the 6–8s V5 analysis.
- Name unified to "Autonomous Research & Investment Architect"; `/stress` retitled.

---

## Tomorrow — suggested order

### 1. Run the history rewrite (blocker above), then first commit + push
After the rewrite, verify: `git log --all --oneline | wc -l` and confirm the PDFs are gone with
`git rev-list --all --objects | grep -i COMM068` (should return nothing).

### 2. Finish the plain-English pass
`components/Term.jsx` has the glossary and is wired into Track Record, V5's ensemble panel and the
Research trade levels. **Still bare:** Markets/Signals table headers, Portfolio, Stress, Desk,
Quant Lab, and the V5 module table. Wrap the jargon there the same way — `<Term k="atr">ATR</Term>`.

### 3. Mobile pass on the pages themselves
The *shell* is responsive; the *pages* are not audited. Dense tables, the V5 module table and the
Desk debate theatre need checking at 375px. Note: **the preview pane does not composite when
hidden, so CSS transitions never advance** — set `element.style.transition = 'none'` before
measuring animated properties, or you will chase a phantom cascade bug (this cost real time today).

### 4. Consider a command palette (Ctrl/⌘+K)
Twelve destinations plus per-symbol research is exactly the shape that benefits: type a ticker →
jump straight to its dossier. This is the single biggest remaining UX win.

### 5. Loose ends
- `data/memory/long_term_memory.json` is tracked and holds ARIA's own decision reasoning. Fine as
  demo data, but make it a deliberate choice.
- `README.md` predates the restructure — it should describe the 12 destinations and the V5 chain.
- The desk's `auto_execute` now defaults to `false`, but **your local `data/desk_config.json` still
  says `true`** (gitignored, so fresh clones are safe). Left alone deliberately — your call.

---

## Things that will bite you if you forget them

| Gotcha | Why |
|---|---|
| `.gitignore` has **no trailing comments** | `data/v5/  # note` makes the whole line literal and silently ignores nothing |
| London quotes in **pence** (`GBp`) | Treating it as GBP is a 100× error. Always go through `src/data/currency.py` |
| yfinance `dividendYield` units changed between versions | Use `marketdata.dividend_yield_pct()`, which derives from `dividendRate/price` |
| `^TNX` is already a percentage | Do not divide by 10 |
| `max(lo, min(hi, NaN))` returns `hi` in JS/Python | A division by zero became a maximally bullish score once. `_util.clamp` now propagates NaN |
| Preview pane hidden ⇒ no compositing | CSS transitions freeze at their start value and look like a cascade bug |
| Backend on :8000 needs a restart to pick up changes | It runs without `--reload` |

---

## How to run

```bash
START_ARIA.bat
```
Backend on :8000, frontend on :3000. Tests: `venv\Scripts\python.exe -m pytest tests/ -q`.
Frontend build: `cd frontend && node node_modules/vite/bin/vite.js build`.
