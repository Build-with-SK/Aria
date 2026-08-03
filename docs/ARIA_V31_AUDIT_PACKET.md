# ARIA v3.1 — AUDIT PACKET
### System report + external audit prompts (Claude & ChatGPT) + fix-execution prompt
*Generated 2026-07-22 after the v3.1 build (commits `aee28ae` → `9feb140`, 16 commits).*

---

# PART 1 — WHAT WAS BUILT: FILE-BY-FILE REPORT

## 1A. New files (created in v3.1)

| File | Responsibility | How to verify it works |
|---|---|---|
| `src/desk/position_manager.py` | **The exit engine.** Owns every open paper position: hard stop, target (or 50% scale-out), 1R-breakeven + ATR trailing stop, thesis invalidation, time stop, deterministic hold-vs-close exit debate. Adopts untracked broker positions (legacy triage), flattens gross exposure over cap worst-first, heals missing OCO brackets, detects broker-side closes. Exits require a CONFIRMED fill — no fill, no record. Equity exits defer while the US market is closed. | `pytest tests/test_exit_rules.py` (31 tests); `POST /api/desk/tick-now` then `GET /api/desk/status` → `last_tick` |
| `src/desk/notify.py` | One brief push per fill in the exact contract format: `▲ BUY 12 AAPL @ 312.05 \| SL 303.71 \| sell at 325.97 or on signal flip` / `▼ SOLD ... \| P&L +$76.20 (+2.0%) \| reason: target hit`. ntfy + Pushover. | `pytest tests/test_notify_format.py` (6 tests) |
| `src/desk/scorecard.py` | Judge calibration: scores each analyst's debate view against realized trade outcomes; after 20 closed trades tilts judge weights toward predictive agents, bounded ±0.15 from defaults (0.5/0.3/0.2), renormalized, every change logged in `data/desk/scorecard.json`. | `pytest tests/test_scorecard.py` (5 tests) |
| `src/inference/router.py` | Tiered inference router (FAST/STANDARD/DEEP → ordered provider/model candidates). Retryable errors back off exponentially then fail over; fatal errors skip to the next candidate; per-provider circuit breaker (3 fails → open 60s → half-open probe). `local_only` degraded mode. `complete_with()` for explicit user-picked models. | `pytest tests/test_inference_router.py` (11 tests); `GET /api/inference/status` |
| `src/inference/errors.py` | Typed error classification: `RetryableError` (timeout/429/5xx/conn), `FatalError` (auth/bad request), `AllProvidersFailed`, `classify_http()`. | covered by router tests |
| `src/inference/providers/ollama.py`, `providers/anthropic.py`, `providers/__init__.py` | Provider adapters behind one `Provider.complete()` interface. Ollama via urllib (project convention); Anthropic via urllib with `ANTHROPIC_API_KEY`. | covered by router tests |
| `src/inference/ollama_discovery.py` | Queries Ollama `/api/tags`, parses name/param-size/quantization into SQLite (`data/model_registry.db`); tier heuristic ≤4B FAST, 5–14B STANDARD, ≥15B DEEP; unreachable → mark local models down, continue remote-only, never crash. Registry feeds the router extra local fallbacks. | `pytest tests/test_ollama_discovery.py` (6 tests); `POST /api/inference/discover` |
| `src/compression/engine.py` | Progressive context compression for long chat sessions: ≥70% util compacts old tool results to retrievable stubs; ≥80% LLM-summarizes turn batches (decisions/tickers/unresolved/user-instructions preserved, deterministic fallback); ≥90% ULID checkpoint-and-reset; ≥95% loud emergency truncate. Every action is a typed audit event. | `pytest tests/test_compression.py` (8 tests) |
| `src/compression/store.py` | SQLite store behind the engine: stubs, checkpoints, audit log; minimal ULID generator (no new deps). | covered by compression tests |
| `src/data/lse_data.py` | London Strategic Edge databank client (candles / macro series / bond yields / economic calendar / insider trades). Key-gated on `LSE_API_KEY`: without it, zero network calls, ARIA unchanged. Disk cache (30min prices, 6h slow data) protects the monthly byte allowance. Digests: `yield_snapshot()`, `upcoming_us_events()`, `insider_buys()`. | `pytest tests/test_lse_data.py` (5 tests); `GET /api/lse/status` |
| `configs/inference_tiers.json` | Tier → [provider, model] candidate lists for the router. | edit and watch `GET /api/inference/status` |
| `configs/compression.json` | Compression thresholds and budgets. | compression tests read the same defaults |
| `tests/` (6 files, 66 tests) | Pure-function tests for exit rules, message format, scorecard tilts, router failover/breakers, discovery parsing, compression cascade, LSE client gating. No network, no broker. | `venv\Scripts\python.exe -m pytest tests -q` |

## 1B. Modified files (and why)

| File | Change |
|---|---|
| `src/execution/order_manager.py` | **Bracket bug fix**: entries poll up to 30s for the CONFIRMED fill before placing protections (Alpaca acks `accepted` first — the old code silently skipped brackets). `place_protective_orders()` places ONE OCO (whole-cent prices, whole-share qtys for equities). `open_orders_by_ticker()`, `cancel_stale_orders()` (unfilled entries >1 day on unheld tickers only). |
| `src/execution/alpaca_broker.py` | `get_open_orders(nested=True)` exposes held OCO stop legs (prevents healing churn); `submit_oco_exit()` = take-profit limit + stop leg on the same reserved shares. |
| `src/execution/broker_base.py` | `get_open_orders()` default added to the broker interface. |
| `src/desk/config.py` | v3.1 defaults: `auto_execute: true` (paper gate still rules), `management_tick_minutes: 5`, budgets $20k/10 trades, exit-engine keys (`max_hold_days`, `time_stop_min_r`, `scale_out_at_target`, `max_gross_exposure_pct`). |
| `src/desk/desk_daemon.py` | Two APScheduler jobs: 5-min management tick 24/7 (first run immediate = startup reconciliation) + 30-min hunt cycle gated by `us_equities_open()` (09:30–16:00 ET Mon–Fri; crypto 24/7). Broker-down notify-once. |
| `src/desk/auto_executor.py` | After a confirmed entry: hands the position to the exit engine (`track_entry`), pushes the exact-format message. **Self-heal**: a failed connection check rebuilds the broker client once (a wedged session ≠ a down broker). |
| `src/desk/debate.py` | Judge reads calibrated weights from the scorecard; all LLM calls routed through the inference router (breakers/retries); consult budget cap unchanged as routing policy. |
| `src/desk/portfolio_manager.py` | Drops SELL verdicts on unheld crypto (Alpaca cannot short crypto — those proposals looped forever). |
| `src/desk/analysts/sentiment_agent.py` | Conviction capped at 60; abstains under 5 headlines — a keyword lexicon can no longer outvote the signal engine. |
| `src/desk/analysts/macro_agent.py` | Optional LSE evidence: live US10Y + 1-month drift, upcoming high-signal US events (CPI/FOMC/NFP/GDP/PCE). |
| `src/desk/analysts/fundamental_agent.py` | Optional LSE evidence: recent insider open-market purchases. |
| `src/desk/position_manager.py` (post-launch hardening) | `_sane_invalidation` + `_sane_levels`: wrong-side stops/targets/invalidations from stale signal data are dropped instead of triggering instant exits. Exits only record on real fills; deferred while market closed; never double-submitted. |
| `src/brain/brain_daemon.py` | **Research-only by default** — the brain no longer queues trades (the desk owns trading); `data/brain_config.json {"propose_trades": true}` re-enables. |
| `src/brain/cognitive/reasoner.py` | `_local`/`_frontier` migrated behind the router. |
| `src/cognitive/aria_core.py` | Conversation history runs through the compression cascade before each model call; failures no-op. |
| `backend/main.py` | Additive endpoints: `POST /api/desk/tick-now`, `GET /api/desk/positions`, `GET /api/desk/performance`, `POST /api/inference/discover`, `GET /api/inference/status`, `GET /api/lse/status`. Chat endpoints routed through the router. `load_dotenv(override=True)` so rotated keys win on reload. Ollama discovery on startup. |
| `frontend/src/pages/Desk.jsx` | REALIZED PERFORMANCE panel: realized P&L (day/week/all), win rate, avg R, profit factor, expectancy, per-agent hit rates, live judge weights. |
| `main.py` (TIS pipeline) | **Restored the rich `data/signals.json` export** the Phase 5/6 rewrite dropped (file had been frozen since 2026-05-31 — the root cause of every NO_TRADE). Non-string ticker guard (`ticker: ON` YAML-bool bug). Pipeline trade proposer off by default. |
| `configs/universe.yaml` | `"ON"` quoted (YAML 1.1 parses unquoted ON as boolean True). |

## 1C. Operational facts an auditor needs

- **Safety contract (do not weaken)**: autonomy is PAPER-ONLY. `ALPACA_PAPER` must be exactly `"true"` AND the broker object must report paper; a live account always hard-routes to the manual approval queue; `/api/desk/auto-execute` refuses (409) to arm on live. Hard risk rules and exit rules live in code; no LLM can override them.
- **Runtime**: FastAPI backend port 8000 (uvicorn), React frontend port 3000 (Vite), Ollama localhost:11434, Alpaca paper API. Windows + pathlib; venv at `venv\Scripts\python.exe`; Ollama calls via urllib only; backend endpoints are additive-only with lazy imports; frontend inline styles, no new npm packages.
- **State files**: `data/desk/positions.json` (tracked positions), `closed_trades.jsonl` (realized ledger), `executions.jsonl` (every action), `scorecard.json`, `day_state.json`, `debates/*.json` (transcripts incl. `exit-*`), `data/model_registry.db`, `data/compression_store.db`, `data/lse_cache/`.
- **Daily pipeline**: Windows scheduled task `TradingIntelligenceSystem` runs `main.py` daily 07:30 → rewrites `signals.json` (+ macro, ML). The desk is only as good as this file's freshness.
- **Proven live (paper)**: one full autonomous round trip executed Tue 2026-07-21 (buy 1 AAPL 326.54 → rule exit 326.58, +$0.04) — mechanics correct, levels were stale, hence the `_sane_levels` guard.

---

# PART 2 — AUDIT PROMPT FOR CLAUDE (Claude Code, with repo access)

Copy everything between the lines into a **fresh Claude Code session** started in `<repo-root>`:

---

You are auditing ARIA v3.1, an autonomous PAPER-trading desk (Windows, Python/FastAPI backend, React frontend, local Ollama + Anthropic API). You did not write this code. Be adversarial. Read `docs/ARIA_V31_AUDIT_PACKET.md` Part 1 first — it lists every file the v3.1 build created/modified and what each claims to do. Then verify the claims against the actual code.

**Scope — audit these, in order:**
1. **Safety contract**: prove or disprove that a LIVE account can never be auto-traded. Trace every code path that submits a broker order (`src/desk/auto_executor.py`, `src/desk/position_manager.py`, `src/execution/order_manager.py`) and check each is gated by BOTH the `ALPACA_PAPER` env gate and the broker paper flag. Flag any path that isn't.
2. **Exit engine correctness** (`src/desk/position_manager.py`): walk `evaluate_exit` for longs and shorts. Hunt for: rule-ordering bugs, trailing stops that can loosen, fraction/scale-out qty errors, race conditions between the 5-min tick and broker-side OCO fills, double-close paths, P&L computed from anything other than a confirmed fill.
3. **Order plumbing** (`src/execution/`): OCO lifecycle (place → trail → cancel/replace → fill detection via nested legs), the 30s fill poll, stale-order cancellation criteria, fractional/sub-penny handling. What happens on a crash between fill and bracket placement?
4. **Money math**: sizing (portfolio_manager → risk_officer caps/trims), day-budget accounting, gross-exposure triage, r_multiple and realized P&L formulas, the performance endpoint's win-rate/profit-factor/expectancy math (`backend/main.py` `desk_performance`).
5. **Resilience**: kill scenarios — Ollama down mid-debate, Alpaca 429s, backend restart mid-position, stale broker client, clock/timezone errors in `us_equities_open` (half-days? DST?), corrupted JSON state files. The system claims it survives all of these; check.
6. **Calibration honesty** (`src/desk/scorecard.py`, `src/desk/debate.py`): can the weight tilt exceed its ±0.15 bound after renormalization? Can a small sample flip weights? Is an agent scored fairly on SELL verdicts and shorts?
7. **BLOAT AND OVERENGINEERING PASS** — this matters as much as bugs: AI-written code tends to solve a 3-line problem with 20 lines. For every file in the Part 1 table, flag: dead code, redundant defensive try/excepts that swallow real errors, duplicated logic that belongs in one helper (e.g. the repeated `.env`-loading snippets, repeated open-order filtering, two notification implementations in `auto_executor._notify` vs `notify.py`), config keys nobody reads, abstractions with one caller, and anything that makes hot paths (the 5-min tick) slower than needed. Propose concrete deletions/merges with estimated line savings.
8. **Tests**: run `venv\Scripts\python.exe -m pytest tests -q`. Then identify what is NOT covered that should be (e.g. `_close` fill-confirmation flow, gross-exposure triage ordering, `us_equities_open` edge cases, router+discovery integration).

**Rules**: read code before judging it; do not modify anything; every finding needs file:line, a concrete failure scenario ("with inputs X, Y happens"), and a severity (CRITICAL / HIGH / MEDIUM / LOW / BLOAT). No style nits. If something is actually good, say so in one line and move on.

**Output format** (this exact structure, so a human can skim it):
```
## VERDICT (3 sentences max: is this safe to leave running on paper 24/7?)
## WHAT THE SYSTEM ACTUALLY DOES (10 bullets, plain English, no jargon)
## CRITICAL / HIGH findings (file:line — scenario — suggested fix, one paragraph each)
## MEDIUM / LOW findings (one line each)
## BLOAT REPORT (file — what to delete/merge — est. lines saved)
## TEST GAPS (ranked, top 5)
## SCORE: safety /10, correctness /10, simplicity /10
```

---

# PART 3 — AUDIT PROMPT FOR CHATGPT (no repo access)

ChatGPT can't read your disk. First zip the relevant source (or paste files when it asks). Recommended: upload a zip of `src/desk/`, `src/execution/`, `src/inference/`, `src/compression/`, `src/data/lse_data.py`, `backend/main.py`, `main.py`, `tests/`, `configs/`, plus `docs/ARIA_V31_AUDIT_PACKET.md`. Then paste this prompt:

---

You are a senior quant-desk code reviewer auditing "ARIA v3.1", an autonomous PAPER-ONLY trading system I've uploaded (Python/FastAPI + React; Alpaca paper broker; local Ollama LLM with Anthropic API fallback). The file `docs/ARIA_V31_AUDIT_PACKET.md` (Part 1) describes what every file claims to do — read it first, then verify the claims against the actual code I've provided. You did not write this code; assume it has bugs and bloat until proven otherwise.

Audit in this order:
1. **Safety**: can any code path place a trade on a LIVE (non-paper) account without human approval? Trace every `submit_order` call site and its gates.
2. **Exit-rule math** in `src/desk/position_manager.py` (`evaluate_exit`, `exit_debate_score`, `r_progress`, `_sane_levels`): check long/short symmetry, trailing-stop monotonicity (can the stop ever move the wrong way?), time-stop weekday counting, and whether any rule can fire spuriously or never.
3. **Broker plumbing** in `src/execution/`: the confirmed-fill-before-brackets flow, OCO exit orders, nested-leg detection, stale-order cancellation. What breaks if the process dies at each step?
4. **Statistics**: win rate, profit factor, expectancy, avg R in the `/api/desk/performance` endpoint (in `backend/main.py`) — are the formulas right, and are scale-out fractions and partial fills handled?
5. **Calibration** in `src/desk/scorecard.py`: is the ±0.15 weight bound actually enforced after renormalization? Any way a lucky streak dominates?
6. **BLOAT PASS (important)**: AI-generated code often uses 20 lines where 3 suffice. Flag per file: duplicated logic, unnecessary abstractions, dead config keys, try/except blocks that hide real failures, and anything slowing the 5-minute management tick. Give concrete merge/delete suggestions with estimated line savings.
7. **What's missing**: the top 5 untested or unhandled scenarios you'd fix first.

Rules: every finding must cite file + function (line numbers if visible), include a concrete failure scenario, and carry a severity (CRITICAL/HIGH/MEDIUM/LOW/BLOAT). No style nitpicks. Do not rewrite the system — review it.

Deliver exactly this structure:
```
## VERDICT (max 3 sentences: safe to run 24/7 on paper?)
## WHAT THIS SYSTEM DOES (10 plain-English bullets)
## CRITICAL / HIGH (one paragraph each: location — scenario — fix)
## MEDIUM / LOW (one line each)
## BLOAT REPORT (file — merge/delete suggestion — est. lines saved)
## TOP 5 GAPS
## SCORES: safety /10, correctness /10, simplicity /10
```

---

# PART 4 — FIX-EXECUTION PROMPT (run AFTER you have both audit reports)

Paste this into Claude Code in `<repo-root>`, followed by the two audit reports:

---

Below are two independent audit reports of ARIA v3.1 (one from Claude, one from ChatGPT). Triage and fix them with these rules:

1. **Triage first, fix second.** Build one merged table: finding, source (Claude/ChatGPT/both), severity, and your own verdict after reading the actual code — CONFIRMED / WRONG / WON'T-FIX (with one-line reason). Auditors without full context are sometimes wrong; verify every claim against the code before touching anything. Findings BOTH auditors independently raised get priority.
2. **Fix order**: CRITICAL safety → HIGH correctness → test gaps for anything you just fixed → BLOAT (only where the auditors' suggestion genuinely simplifies; never trade clarity or a safety check for line count).
3. **Hard constraints — never violate, no matter what an audit says**: the paper-only safety contract stays welded shut (env gate + broker paper flag, live always routes to human approval); hard risk rules and exit rules stay in code, never LLM-overridable; backend endpoints are additive-only; heavy imports stay lazy; Windows pathlib + venv python; urllib for Ollama; no new npm packages; every fill/exit stays explainable (debate_id links intact).
4. **Small reviewable commits**, one theme per commit, message says which audit finding it addresses. Run `venv\Scripts\python.exe -m pytest tests -q` after every commit — all existing tests must stay green; add a regression test for every CONFIRMED bug you fix.
5. For BLOAT fixes: prove behavior is unchanged (tests before + after). If a "simplification" would remove error handling that a live-paper session actually needs (broker flakiness, Ollama outages, corrupted state files are all REAL here — they happened), mark it WON'T-FIX instead.
6. **Finish with a report**: the triage table, what was fixed (commit per finding), what was rejected and why, and the new test count. If either auditor found a CRITICAL safety hole, explain the fix in plain English and how you verified the hole is closed against a running backend.

---
