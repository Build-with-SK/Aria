# ARIA — Ticker Intelligence + Timeframe Tabs + Brain Visualization
## Build spec for a Claude Code session (run from `C:\Users\sound\Documents\trading-intelligence-system`)

Copy everything below into a fresh Claude Code session. Build in the order given,
small reviewable commits, verify each feature live in the browser before moving on.

---

## Context — what ARIA is and what already exists (REUSE, don't rebuild)

ARIA is a trading-intelligence app: FastAPI backend (`backend/main.py`, port 8000)
+ React/Vite frontend (`frontend/`, port 3000, Bloomberg-terminal look). Windows +
`venv\Scripts\python.exe`. **Conventions (follow exactly):** inline React styles only
(tokens `var(--mono)`, `var(--orange)`, `var(--green)`, `var(--red)`, `var(--muted)`,
`.bb-card`, `.bb-card-header`); **no new npm packages** (recharts IS already available);
backend endpoints are **additive only** with lazy imports; Ollama via `urllib`.

**Backend endpoints that already exist — build the UI on these:**
- `GET /api/universe/explore?q=SYM&market=NSE` → resolves ANY global ticker live
  (US/NSE/BSE/LSE/crypto), returns `{resolved, dossier{price,returns,fundamentals,
  weekly_closes_5y}, related{competitors,suppliers,sector_peers}, note}` + technical consensus.
- `GET /api/universe/quote/{symbol}` → fast quote card (price, exchange, currency).
- `GET /api/universe/dossier/{symbol}` → fundamentals + returns + 5y weekly closes.
- `GET /api/universe/news/{symbol}` → latest news + sentiment + political exposure.
- `GET /api/universe/peers/{symbol}` → sector peers.
- `GET /api/technical/summary?symbol=SYM&timeframes=5m,15m,1h,1d,1wk` → investing.com-style
  multi-timeframe consensus (Strong Buy…Strong Sell) with per-indicator buy/sell/neutral.
- `GET /api/technical/recommendations` / `POST /api/technical/snapshot` /
  `GET /api/technical/performance` → the recommendations scan + forward hit-rate.
- `GET /api/history/{ticker}` → OHLC history (use for the candlestick).
- `GET /api/signals/{ticker}`, `/api/ml/{ticker}` → signal + ML detail.
- Political data lives in `data/political/political_signals.json` (per-ticker
  `political_activity_score`, and congressional/insider disclosure records);
  module `src/political/`. If no per-ticker political endpoint is mounted, add one
  (additive): `GET /api/political/{ticker}` returning that ticker's disclosure records
  (who bought/sold/held, when, amount) from the political data.
- Existing frontend pages: `Recommendations.jsx` (new), `Signals.jsx`, `Desk.jsx`,
  `Thinking.jsx` (LIVE MIND), `Brain.jsx` (AI BRAIN). Sidebar in `components/Sidebar.jsx`.

The technical-summary timeframe keys are defined in `src/data/technical_summary.py`
`TIMEFRAMES` = 5m/15m/30m/1h/1d/1wk. To add 1m/2m, add entries there
(`"1m": ("1m","5d")`, `"2m": ("2m","5d")`) — yfinance supports 1m/2m for ~the last
week only, so those tabs may be empty for some symbols; handle gracefully.

---

## FEATURE 1 — Timeframe tabs on the technical summary

On the Recommendations page's "CHECK ANY STOCK" panel (and the ticker detail page,
Feature 2), replace the fixed timeframe list with **clickable tabs**: `1m 5m 15m 30m
1h 1d 1wk`. Clicking a tab shows that timeframe's full breakdown — the overall label,
the Moving-Averages summary + its individual MAs, and the Oscillators summary + each
oscillator's buy/sell/neutral — exactly like investing.com's per-timeframe table
(`/api/technical/summary` already returns `moving_averages` and `oscillators` dicts
per call; call it per selected timeframe, or once with all timeframes and switch
client-side). Default tab: `1d`. Show a small "consensus across all timeframes" strip
above the tabs. Style tabs as the app's pill/tab look; active tab uses `var(--orange)`.

Also add `1m` and `2m` to `TIMEFRAMES` in `technical_summary.py` (they’ll be sparse but
should not error).

---

## FEATURE 2 — Universal ticker linking (the big one)

**Every ticker symbol shown anywhere in the app becomes an interactive link.**

### 2a. A reusable `<TickerLink symbol="AAPL" />` component (`frontend/src/components/TickerLink.jsx`)
- Renders the symbol styled as a subtle link (orange on hover).
- **On hover** → a small floating card (tooltip/popover) with brief live data:
  exchange, currency, open, previous close, **last traded price** + day change %.
  Source: `GET /api/universe/quote/{symbol}` (add open/prev-close/last to that endpoint
  if missing — it has the data via yfinance `fast_info`). Debounce the fetch (~250ms)
  and cache per symbol for the session so hovering a list doesn't spam the API.
- **On click** → navigate to `/ticker/:symbol` (the detail page, 2b).
- Retro-fit it across the app: wrap the symbol cell in `Recommendations.jsx`,
  `Signals.jsx`, `Desk.jsx` (slate, positions, fills), and anywhere a ticker renders.

### 2b. The ticker detail page (`frontend/src/pages/TickerDetail.jsx`, route `/ticker/:symbol`)
A full research page for one symbol, sections top to bottom (each a `.bb-card`):
1. **Header** — name, exchange, currency, live price + day change, and the overall
   technical consensus badge. (from `/api/universe/explore` + `/api/universe/quote`)
2. **Snapshot / classification** — sector, industry, and a clear **BULLISH / BEARISH /
   NEUTRAL** verdict (derive from the signal composite score `/api/signals/{ticker}`
   and/or the technical consensus; show which drove it).
3. **Company financials** — P/E, ROE, revenue growth, margins, debt/equity, market cap,
   52-wk range, returns 1M/6M/1Y/5Y. (from the dossier in `/api/universe/explore`)
4. **Technical indicators summary** — the Feature-1 timeframe-tabbed table, embedded.
5. **Candlestick chart** — OHLC candles from `/api/history/{ticker}`. Build it with
   **recharts** (already available): a `ComposedChart` with a custom `Bar`/shape drawing
   the wick+body per candle (green up / red down), or an SVG candlestick if cleaner. Add
   a simple timeframe/range switch (1M/6M/1Y). No new libraries.
6. **Latest news** — headlines with source + date + sentiment tag, from
   `/api/universe/news/{symbol}`.
7. **Political disclosures** — a table of politicians/insiders who **bought / sold /
   held** this stock, with date and amount, from the political data (add
   `GET /api/political/{ticker}` if needed). Label each row Buy/Sell/Hold with colour.
   Include the "research only, 10% weight, legal public disclosures" note.

Optionally add one **aggregator endpoint** `GET /api/ticker/{symbol}/detail` that
composes explore + quote + technical summary + news + political into one payload so the
page does a single fetch (nicer UX). Keep the individual endpoints too.

Handle unknown/again-resolvable symbols (SAIL → SAIL.NS) via the existing resolver;
show a clean "resolving…" then error state if nothing resolves.

---

## FEATURE 3 — "How ARIA thinks" brain visualization (reel-inspired)

ARIA already has `Thinking.jsx` (LIVE MIND) and `Brain.jsx` (AI BRAIN) — **upgrade these
into a genuinely impressive, better-designed visualization of ARIA's reasoning**, better
than the reel the user will describe. The data already exists:
- The desk debate transcripts (`GET /api/desk/debates`, `/api/desk/debate/{id}`) contain
  the bull vs bear rounds, each analyst's evidence, the judge's deterministic verdict math,
  and (when enabled) Fable's teacher lessons — a real, inspectable chain of reasoning.
- The brain daemon's reasoning steps (ORIENT→FOCUS→RECALL→ANALYSE→DECIDE→REFLECT) are in
  the brain status/last-cycle.
Build a visualization that shows, live and beautifully: the analysts forming opinions →
the bull/bear debate rounds → the judge weighing them → the verdict, with the evidence
and numbers surfacing as it "thinks". Animated, dark, neon, node-graph or timeline style —
use CSS/SVG animation (no new npm). This is the "watch the AI brain work" experience.

**The reel:** the user will describe (or screenshot) the Instagram reel's AI — its UI,
how it visualizes thinking, and what it can do. Treat that description as the design
target for this feature and match/exceed it. Until then, build the strongest version from
the debate/brain data above.

---

## Constraints & verification
- Inline styles, existing tokens, **no new npm packages**, additive endpoints, lazy imports.
- After each feature: run the frontend, load the page in the browser, confirm it renders
  with **real data** (e.g. `/ticker/SAIL` shows SAIL.NS price, financials, candles, news,
  political rows; hovering a ticker shows the quote card; timeframe tabs switch).
- Keep `pytest tests -q` green; add tests for any new pure backend logic (e.g. the
  detail aggregator, the political-per-ticker reader).
- Small commits per feature with clear messages.

## Suggested build order
1. TickerLink component + hover quote card + wire into Recommendations/Signals/Desk.
2. TickerDetail page skeleton + route + header/financials/news (reuse existing endpoints).
3. Timeframe tabs (Feature 1) → embed in TickerDetail + Recommendations.
4. Candlestick chart (recharts custom shape) from /api/history.
5. Political-per-ticker endpoint + section.
6. Brain visualization upgrade (Feature 3) — once the reel is described.

---

## FEATURE 3B — THE LIVING CORE (voice/talk-reactive brain centerpiece)

**Reference image:** a user-provided screenshot of a glowing particle core — a bright
orange-yellow fusion center surrounded by thousands of pink/magenta and teal/cyan
particles in a rotating galaxy/sphere with faint spiral arms, on pure black. Match and
exceed it. Do NOT alter that reference file.

**Where:** hero of the AI BRAIN page (`Brain.jsx`) — full-bleed dark canvas, app mono HUD
text overlaid (e.g. "LIVING BRAIN · vault + git memory").

**Visual (Canvas 2D or raw WebGL — NO new npm packages; three.js is NOT installed):**
central radial core glow (orange→yellow→white hot, soft bloom); ~3,000–5,000 particles
projected from 3D coords on a rough sphere/disc, slow orbital rotation, depth-sorted
(far = dimmer/smaller); two-tone warm pink + cool teal palette, brighter near the core;
faint spiral arms + a few elliptical-orbit drifters; additive blending; vignette; optional
CRT scanline.

**Idle "alive":** slow rotation + gentle breathing pulse (core radius/brightness ease up/down
~3–4s, a resting heartbeat); particles shimmer.

**TALK-REACTIVE beating (key ask):** core beats while ARIA talks back.
- Controller: `coreState = 'idle'|'thinking'|'speaking'` + `intensity` 0–1.
- User sends → `thinking`: cloud tightens inward, swirls faster, core dims (concentrating).
- Response streams back → `speaking`: core PULSES on each streamed chunk/token (sharp bloom +
  particle outward push per beat), sustained brighter glow through the reply; amplitude ∝
  chunk cadence so it beats in time with the words.
- If audio TTS used (browser `speechSynthesis`, no npm): also drive `intensity` from a Web
  Audio `AnalyserNode` RMS so it beats to the actual voice; fall back to chunk-beat otherwise.
- Reply end → ease to `idle` over ~1s.
- Wire into the existing chat send + streaming handler (small context/store so any page drives
  the core; canvas reads state each frame).

**Data flavor (optional, from the "vault brain / git history" theme):** map particles to real
memory — vault notes (`/api/vault/status`), long-term memories, git-commit "birth days" for a
time-lapse toggle; recent-memory particles glow warmer.

**Performance:** `requestAnimationFrame`, precomputed particle buffers, auto-throttle (drop
particle count + disable bloom if FPS dips, e.g. on the 2014 Mac mini), pause when tab hidden.

**Verification:** Brain page renders + breathes at idle; send a chat message → core goes
thinking → BEATS in sync with the streaming reply → settles; ~60fps desktop, degrades
gracefully on low-power hardware.
