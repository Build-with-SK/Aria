# Claude independent review — ARIA multiasset trading extension

| | |
|---|---|
| Reviewed branch | `feature/aria-multiasset-trading` @ `7385130` (6 commits on `58f5f25`) |
| Reviewer | Claude Code (Opus 5.5), read-only. No Codex file was edited; Codex's worktree `git status` was clean before and after. |
| Date | 2026-11-06 |
| Scope | The 51 files Codex changed, plus the existing order paths, auth layer and backtesters the branch says it reuses. |
| Not done | No merge, push, deploy, broker connection, credential access or order. No frontend build (it writes into Codex's folder). |
| Status | Frozen as reviewed at `7385130`. Remediation status for every finding is tracked in [`ARIA_DELIVERY_TRACKER.md`](ARIA_DELIVERY_TRACKER.md). Hosting details are generalised in this public copy. |
| Reproduction | Probe scripts in [`claude_review_probes/`](claude_review_probes/). Run from any folder: `venv\Scripts\python.exe docs\trading\claude_review_probes\probes.py <path-to-worktree>` (same for `probe_e7.py`, `probe_bt.py`; `challenge_mc.py` takes no argument). Recorded output: `probes_out.json`. |

Severity is rated for each component **used as its own documentation intends** (the final pre-trade
control, the read-only demo adapter, the approval store). Today no code path in this extension can send an
order, so nothing below can lose money as the branch stands. The findings are what must be true before it can.

---

## EXECUTIVE VERDICT

**Gate: CONDITIONAL_REVIEW.** Not ready for a read-only demo connection, not ready for any demo order.
No live-execution approval is given or implied.

What Codex got right, and I verified rather than accepted:

- **There is no sender.** No new module calls a broker; MT5 write methods refuse; no endpoint, worker or
  startup hook creates the adapter or the review store. The two new routes are owner-proven (`require_owner`)
  and behind the existing role and origin middleware.
- **The FTMO loss formulas match FTMO's published rules** (daily floor from the 00:00 CE(S)T balance minus a
  % of initial capital; 2-Step static and 1-Step end-of-day trailing max loss; the 12 Oct 2026 consistency
  cutover at 06:00 UTC). Prague day boundaries are correct across both 2026 DST changes.
- **The documentation is unusually honest**: it lists what is missing and claims no edge.
- **Test claims reproduce**: 104/104 new tests and **3,432 passed / 10 skipped** on the full suite (my run, 248 s).

What blocks progress (9 HIGH, 0 CRITICAL):

1. **A trailing loss limit can be lowered** (CR-01). Re-confirming the rule profile starts a new history in
   which the 1-Step high-water mark can be re-seeded lower — I moved the floor from $9,800 to $9,000. The docs
   say this cannot happen.
2. **The "final mandatory control" is caller-supplied** (CR-02). The approval store accepts any `RiskAssessment`
   object that says ALLOW. I reserved an intent for a 5-lot trade with a $5,000 (50%) loss that the risk engine
   itself DENIES.
3. **Small-looking positions with huge notional get through if the contract size is wrong** (CR-03). With gold's
   contract size entered as 1 instead of 100, the engine allowed **10 lots** as "$50 risk"; the real exposure is
   **$5,000 risk and $2.6M notional on a $10k account**. The tick-value check is self-consistency, not independent.
4. **FX conversion is checked by its labels only** (CR-04): a rate in the wrong direction understates risk
   (−38% for EURGBP on a USD account; far worse on CAD/AUD/CZK accounts).
5. **The internal 2% daily stop can be switched off by one inconsistent input** (CR-05): equity 4.8% below the
   midnight balance, a new trade was ALLOWED because a separate field said today's P&L was 0.
6. **"Read-only MT5" is not read-only** (CR-06): the adapter accepts a trading-enabled account and exposes the raw
   SDK; `adapter._sdk.order_send(...)` works. ARIA already fixed this exact bypass class for Alpaca/IBKR.
7. **"DEMO" is not a safety boundary at FTMO** (CR-07): FTMO states all its accounts — paid Challenge and funded
   FTMO Account — are demo accounts. The engine's "DEMO only" rule therefore protects nothing there.
8. **FTMO's operational rules are not enforced** (CR-08): gap trading (all phases), the 2-minute news window and
   no-holding-through-breaks (Standard funded accounts). Free-text placeholders count as "verified".
9. **The existing backtester has look-ahead** (QA-01): its drawdown breaker exits at the open of the bar whose
   close triggered it. A 30% loss disappeared entirely in my reproduction.

On the 25% ambition: with the branch's 0.5% risk and 2R targets, a strategy with **no edge passes a $10k 2-Step
Phase 1 about 11% of the time after costs**; break-even needs a ~37% win rate (simulation below). No strategy in
the repository has demonstrated that. The challenge fee should be treated as the likely cost of learning, not
as an investment, until a strategy shows out-of-sample edge after costs.

---

## ARCHITECTURE ASSESSMENT

| Question | Answer | Evidence |
|---|---|---|
| 1. Preserves existing functionality | **Yes.** `backend/main.py` +31 lines, 0 deletions; 2 routes added; full suite 3,432 passed (same as Codex's claim, +111 vs the 3,321 baseline). The one legacy test edit (`test_stabilization.py`) isolates a test-order dependency and keeps the 16-row/no-write assertions. | `git diff --stat 58f5f25..7385130`; my suite run |
| 2. Avoids unnecessary rebuilding | **Mostly.** Reuses `ta`, `technical_summary.score_frame`, the auth guard, Portfolio page. Adds a second, parallel risk engine (`multiasset_risk.py`) beside `src/risk` and `src/trading/risk`; acceptable while FX/CFD sizing has no home elsewhere, but two engines must not both claim to be "the" pre-trade gate. | `src/trading/multiasset_risk.py` |
| 3. Reuses correct abstractions | **Partly.** Does *not* reuse `src/execution/kill_switch.py` (takes a boolean instead, CR-11), nor `src/trading/access.py` / `live_guard` for the MT5 adapter (CR-06). | `multiasset_risk.py:55`, `mt5_readonly.py:36-66` |
| 4. Unnecessary services/deps | **No new runtime service.** Frontend: `axios` pinned `^1.7.2`→`1.20.0`. A separate hash-pinned research lock (UNVERIFIED by me). | `frontend/package.json` |
| 5. Circular dependencies | **None.** New trading modules import only each other, stdlib and pydantic; `src/research` and `src/backtesting` import only `src.trading.contracts`. No LLM import below the trading boundary. | import scan |
| 6. Mixes reasoning with execution privilege | **No** in this branch: research returns hypotheses; nothing routes them to an order path. The legacy desk *can* auto-execute **paper** orders without per-trade approval (by design, PAPER ceiling enforced by `tests/test_trading_structure.py`); per the owner's cloud-migration handover (kept outside this repository), auto-execute and reflex are currently off and the broker is disconnected. | `backend/main.py` diff; structure test; handover |
| 7. Implementable on the user's hardware | **Not as a sender.** See OP-01: the active runtime is now a Linux ARM cloud host, which cannot host the Windows-only MT5 terminal; the Windows machine that can is no longer the runtime. | OP-01 |

Process note (INFORMATIONAL, PR-01): Codex edited Claude-owned areas (`src/trading/`, `backend/main.py`) under a
newer owner brief (`ARCHITECTURE_DECISIONS.md` §1). `docs/collab/WORK_PLAN.md` was not updated to record it.

---

## QUANTITATIVE CORRECTNESS

### Worked sizing examples (USD account, equity $10,000, all reproduced with `probes.py`)

| ID | Case | Hand calculation | Engine | Verdict |
|---|---|---|---|---|
| E1 | EURUSD 0.10 lot, 20-pip stop | 0.0020 × 100,000 × 0.10 = **$20.00** | $20.00 ALLOW | Correct |
| E2 | USDJPY 0.10 lot, 30-pip stop, JPY→USD = 1/150 | 0.30 × 100,000 × 0.10 = 3,000 JPY ÷ 150 = **$20.00** | $20.00 ALLOW | Correct |
| E3 | Same, rate supplied as 150 (labels right, value inverted) | $450,000 | DENY | Fails safe (overstates) |
| E3b | EURGBP 0.20 lot, 25-pip stop, GBP→USD supplied as 0.7874 (should be 1.27) | 0.0025 × 100,000 × 0.20 × 1.27 = **$63.50** | **$39.37 ALLOW** | **Understated 38%** (CR-04) |
| E4 | XAUUSD 0.10 lot, $5 stop, contract 100 oz | 5 × 100 × 0.10 = **$50**; notional $26,000 (2.6×) | $50 ALLOW | Correct |
| E5b | XAUUSD **10 lots**, contract size mis-entered as 1 | true: **$5,000 risk, $2.6M notional** | "$50" **ALLOW** | **Undetected** (CR-03) |
| E6 | US30 1.00 lot, contract 1, at 42,000 | notional $42,000 = 4.2× equity | DENY (LEVERAGE_CAP) | Correct — small-looking lot caught |
| E6b | US30 0.70 lot | $29,400 = 2.94× | ALLOW | Correct (3× internal cap) |
| E7 | Equity $9,020, static floor $9,000, 0.10 lot 20-pip stop | equity after stop = **$9,000.00 exactly** | ALLOW | Zero margin (CR-10) |
| E8 | Midnight balance $10,000, equity $9,520, `daily_net_pnl`=0 | daily loss already $480 (4.8%) ⇒ internal 2% stop | **ALLOW** | **Wrong** (CR-05); with −480 supplied: EMERGENCY_HALT |
| E9 | 1-Step, high-water $10,800 ⇒ floor $9,800, equity $9,850 | budget min($49.25, room $50) | $20 ALLOW | Correct |

Volume rounding: always floors to the step (`floor_volume`, tested); a minimum lot above budget is denied. Correct.

Notional and margin: notional = (|entry| + tolerance) × contract × lots × FX factor; margin = notional × `margin_rate`.
This is a simplification of MT5's per-calculation-mode margin (FOREX vs CFD vs CFDLEVERAGE); acceptable only
with the broker cross-check in CR-03.

Bid/ask: the touch price used for tolerance is ask for BUY and bid for SELL (correct). Risk is measured from the
proposal's entry, not the touch; the tolerance term covers the difference. Spread, commission, slippage and
financing are flat caller-supplied amounts, not derived (CR-11).

Funding/swaps: an allowance input only; no swap table, no triple-Wednesday, no weekend carry. Historical price
adjustment: the research layer refuses to merge providers or adjustment bases (good); there is no adjustment logic.

### Backtesting (Phase 4)

The new `experiments.py` contains split/purge, manifest and bootstrap utilities; **no strategy in the branch has
been backtested** (Codex says so). I therefore audited the legacy backtesters the branch says it reuses.

| Item | Finding |
|---|---|
| Look-ahead in signals | **None found** in `backtester.py` scores (bar *i−1* score → bar *i* open) or `indicators.py` regimes (rolling, not full-sample quantiles). Evolution lab lags weights one bar. |
| Intrabar time ordering | **HIGH, QA-01**: drawdown breaker uses bar *i* close and exits at bar *i* open. A stop exit at the close can be followed by a re-entry at the same bar's open. |
| Stops | **MEDIUM, QA-03**: close-only; a −10% intrabar move with a flat close never triggers a 5% stop; gaps fill at the close, not the open. The code comment calls this "most conservative" — it is not. |
| Costs | Percentage commission 0.10% + slippage 0.05% one-way (equity style). No spread model per instrument, no swaps, no per-lot commission. Unsuitable for FX/CFD without change. |
| Sharpe/annualisation | **MEDIUM, QA-02**: `metrics.py` uses √252 and `len/252` years for every series — wrong for crypto (365), FX (~260) and any intraday bars. |
| Drawdown | Close-to-close peak-to-trough only. No backtester evaluates FTMO's intraday equity vs midnight-balance floor (QA-04). |
| Multiple testing | Evolution lab has a deflated-Sharpe gate (prior work); the new research families have no selection procedure, so nothing yet to deflate. |
| OOS / walk-forward | `chronological_split` purges and embargoes correctly (tested); it is a single split, not walk-forward. No OOS result exists for any multiasset strategy. |
| Expectancy | `realised_metrics` reports mean currency P&L; no R-multiple expectancy, no drawdown, no breach probability. |

### QA-01 — HIGH — Legacy backtester exits at a bar's open after reading its close
- **File:** `src/backtesting/backtester.py:265` (breaker set from bar *i* close), `:284` (`elif halted: _record_trade(exec_px …)` with `exec_px` = bar *i* open); `:280` + `:290` (stop exit at close, then re-entry at the same bar's open).
- **Evidence / reproduction:** `probe_bt.py`: bar opens 100, closes 70; breaker records exit at **100.0**, P&L 0%; final equity **100,000** where an honest exit leaves ~70,000.
- **Impact:** any backtest in which the breaker fires overstates performance; exactly the losses the breaker exists for are erased.
- **Minimal remediation:** decisions made on bar *i* close execute at bar *i+1* open; after an intrabar exit, no entry at that bar's open.
- **Regression test:** the probe case must record an exit price ≤ 70 and final equity ≤ 70,000.

### QA-02 — MEDIUM — Fixed 252-period annualisation
- **File:** `src/backtesting/metrics.py:24-63`. Sharpe/Sortino use √252 and CAGR uses `len/252` regardless of bar size or calendar.
- **Impact:** crypto (365 d), FX (~260 d) and every intraday series report wrong Sharpe and CAGR.
- **Remediation:** pass `periods_per_year` derived from the bar interval and the instrument calendar; refuse to annualise unknown frequencies.
- **Test:** 365 daily crypto returns ⇒ CAGR over 1.0 year, not 1.45.

### QA-03 — MEDIUM — Close-only stops, mislabelled as conservative
- **File:** `src/backtesting/backtester.py:276-280`. **Reproduction:** `probe_bt.py`: low −10%, close flat ⇒ 0 stop exits with a 5% stop.
- **Remediation:** trigger on the bar's low/high; fill at the stop or at the open if it gaps through.
- **Test:** the probe case ⇒ one stop exit at 95 (or at the open on a gap).

### QA-04 — MEDIUM — No backtest measures FTMO breach probability
- **File:** `src/backtesting/experiments.py:97-115` resamples total P&L, not paths; no backtester applies the midnight-balance daily floor or intraday equity.
- **Remediation:** path-level replay that applies the active rule profile per Prague day and reports P(daily breach), P(overall breach), P(target) with bootstrap intervals.
- **Test:** a fixture sequence that dips 5.1% intraday but closes −3% must count as a daily breach.

### Reasoning quality (Phase 5)

| Requirement | Finding |
|---|---|
| Facts separated from interpretation | **Yes, structurally**: evidence classes OBSERVED / VERIFIED_EXTERNAL_FACT / MODEL_INTERPRETATION / FORECAST. **But** the class is caller-asserted (RQ-01). |
| Sources cited and timestamped | Every evidence item carries provenance with UTC times; nothing checks that a URL was fetched. |
| Forecasts uncertain | `research_confidence` is always `UNVERIFIED`; probabilities must be absent unless `CALIBRATED` with an evidence id (validator, `contracts.py:297-306`). Good. |
| Contradicting evidence considered | Minimal: one RSI-stretch check; it never changes the decision (RQ-02). |
| Can abstain | **Yes**: unknown regime, warm-up, stale candles, wrong timeframe and MacroDriven all ABSTAIN (probe `S_regime_unknown_abstains`). |
| Calibrated confidence | None exists; correctly not claimed. |
| Asset-appropriate analysis | No: one technical rule set for all nine asset classes; the capability table is a label, not an analysis. |
| AI calculations verified | The research path uses no model; sizing is deterministic Decimal. |
| Manipulable by external content | Yes via evidence labels (RQ-01) and caller-chosen `regime`. |
| "Knows / understands / predicts" | No such claim in the new code or docs. Hypotheses are "research triggers"; keep it that way in the UI. |

**RQ-02 — MEDIUM — Contradictory hypotheses, one logic defect.** `src/research/multiasset.py:212-225`. On one bar that
closes 30 pips below 20-bar support (`probes.py` → `S_*`): SupportResistance says **BUY** (`close − bottom < ATR/2` is
true for any close *below* support), Breakout says **SELL**, MeanReversion says **BUY** — each `APPROVE_FOR_HUMAN_REVIEW`.
Fix: SR requires `close ≥ bottom`; any cross-family conflict on the same instrument and bar ⇒ ABSTAIN. Test: the probe bar ⇒ ABSTAIN.

### Pass-probability simulation (`challenge_mc.py`, 20,000 trials, seed 7)

$10k 2-Step Phase 1: +10% target, −5% daily from midnight balance, −10% static; 0.5% ($50) risk per trade, 2R
target, $5 round-trip cost, 3 trades/day max, internal day-halt at −$200.

| Win rate | Net EV/trade | P(pass) | P(fail) | Median days to pass |
|---:|---:|---:|---:|---:|
| 30.0% | −$10.00 | 1.5% | 98.5% | 23 |
| 33.3% (no edge before costs) | −$5.00 | 10.6% | 89.4% | 41 |
| 36.7% (break-even after costs) | $0.00 | 48.7% | 51.3% | 53 |
| 40.0% | +$5.00 | 87.1% | 12.9% | 42 |
| 45.0% | +$12.50 | 99.2% | 0.8% | 23 |

A 25% annual return at 0.5% risk per trade is +50R a year — for example +0.1R net on 500 trades. That is a
measurable claim; it needs out-of-sample evidence after costs, under at least 1.5× observed slippage, before any paid account.

---

## FTMO COMPLIANCE ASSESSMENT

Sources fetched 2026-11-06 (page "modified" dates from page metadata):
[S1 Trading Objectives](https://ftmo.com/en/trading-objectives/) (mod. 2026-11-02);
[S2 Comparison table](https://ftmo.com/en/comparison-table/) (mod. 2026-10-29);
[S3 Trading update 8 Oct 2026](https://ftmo.com/en/blog/trading-updates/trading-update-8-oct-2026/) (mod. 2026-10-08);
[S4 Forbidden trading practices](https://ftmo.com/en/forbidden-trading-practices/) (mod. 2026-11-02);
[S6 News FAQ](https://ftmo.com/en/faq/can-i-trade-news/) (mod. 2026-10-11);
[S7 Overnight/weekend FAQ](https://ftmo.com/en/faq/can-i-hold-positions-overnight-or-over-the-weekend/) (mod. 2026-10-11);
[S8 Instruments/strategies FAQ](https://ftmo.com/en/faq/which-instruments-can-i-trade-and-what-strategies-am-i-allowed-to-use/) (mod. 2026-10-30);
[S9 Who can join](https://ftmo.com/en/faq/who-can-join-ftmo/) (mod. 2026-09-30);
[S10 Platforms](https://ftmo.com/en/faq/which-platforms-can-i-use-for-trading/) (mod. 2026-09-30);
[S13 Account specifications](https://ftmo.com/en/faq/what-are-the-account-specifications/) (mod. 2026-10-05);
GTC: *FTMO Challenge Terms and Conditions*, "last updated on 1 November 2026", linked from
[terms-and-conditions](https://ftmo.com/en/terms-and-conditions/) (pages 1–3 read).

| Rule | Official position | Implementation | Status |
|---|---|---|---|
| Provider / entity | FTMO s.r.o., Prague, Czech Republic (GTC preamble). Services "not regulated" (GTC 2.1). | Profile free-text fields | Owner must record; no code check possible |
| Eligibility | Natural persons 18+, not restricted by nationality/residency (GTC 1.1); restricted-country table (S9). UK is not on the table; no explicit UK statement found. | — | **Owner must confirm before purchase** |
| Account status | "all accounts we provide to our clients are demo accounts with fictitious funds" (S1/S4/S8) | Engine allows `DEMO`; adapter requires `DEMO` | **CR-07** |
| Platforms | MT4, MT5, cTrader, TradingView (S10) | MT5 read adapter only | — |
| 2-Step | Targets 10% / 5%; daily 5%; max 10% **static**; ≥4 trading days per phase (S1, S2) | Parametrised; static anchor = ISC | MATCH |
| 1-Step | Target 10%; daily 3%; max 10% **end-of-day trailing**; no minimum days (S1, S2) | `TRAILING_EOD_BALANCE` | MATCH, but see CR-01 |
| Daily floor | Balance at 00:00 CE(S)T minus X% of initial capital; equity = balance + open P/L ± swaps − commissions; violation when equity "drops below" (S1) | `daily_balance − size × fraction`; equity ≤ floor = breach (stricter) | MATCH (conservative) |
| Trailing anchor | Highest 00:00 balance of any preceding day (or ISC if higher) − 10% ISC; "can only increase" (S1) | `max(prior high, midnight balance)` | MATCH, **monotonicity broken across profile versions (CR-01)** |
| Reset time | 00:00 CE(S)T (S1). MT5 server time is "GMT+2 +DST" (S13) — i.e. not the reset clock | `Europe/Prague` midnight, zoneinfo; DST verified (table below) | MATCH; mapper trap noted (RT-05) |
| Consistency (1-Step) | Orders from 12 Oct 2026 09:00 GMT+3 (and merges after): best day ≤ 50% of **total** profit; earlier: ≤ 50% of **positive days'** profit. Not a breach (S1, S3) | `ftmo_1step_consistency_version`, cutover 06:00 UTC; formulas match | MATCH (product-name matching is brittle, CR-12) |
| News | Standard **FTMO Account** only: no order execution 2 min before/after selected releases, incl. SL/TP/pending fills; Swing and Challenge/Verification exempt (S6) | `news_rule` free text; not enforced | **NOT IMPLEMENTED (CR-08)** |
| Breaks/weekend | Standard **FTMO Account**: may not hold through breaks > 2 h; Swing and Challenge/Verification exempt (S7) | free text; `Swing` research family holds up to 7 days | **NOT IMPLEMENTED (CR-08)** |
| Gap trading (forbidden practice) | Opening trades when major news/earnings are scheduled, or "two hours or less before a relevant financial market is closed for at least two hours"; applies to evaluation and FTMO Account (S4) | none | **NOT IMPLEMENTED (CR-08)**; scope of "major" is **ambiguous** |
| Server limits | "200 orders at a time and 2000 max positions per day" (S8); EA causing "more than 2,000 server requests per day" is hyperactive (S4); no per-minute figure published | `max_server_requests_per_minute` only; policy 2/min ⇒ up to 2,880/day; SL/TP modifications not counted | **MISMATCH (CR-09)** |
| Software / AI | EAs and algorithmic trading allowed if legitimate (S8); forbidden to use "software, artificial intelligence, ultra-high-speed tools" giving an unfair advantage (S4) | — | **AMBIGUOUS — execution must stay disabled until FTMO confirms in writing** |
| Third parties | Must not let a third party access or use the account (S4) | — | **AMBIGUOUS** for remotely hosted automation; ask FTMO |
| Leverage | Standard up to 1:100; Swing up to 1:30 (S13); per-asset figures not found on pages read | profile `max_leverage` + internal 3× cap | Internal cap is stricter; per-asset UNVERIFIED |
| Breach then recovery | Not stated on pages read | — | UNVERIFIED — treat any touch as terminal |

Prague boundary check (`probes.py`, `rule_day` + zoneinfo):

| Instant (UTC) | Rule day | Boundary in UTC | Correct? |
|---|---|---|---|
| 2026-03-28 23:30 | 2026-03-29 | 2026-03-28 23:00 (CET) | Yes |
| 2026-03-29 22:30 | 2026-03-30 | 2026-03-29 22:00 (CEST) | Yes |
| 2026-10-24 22:30 | 2026-10-25 | 2026-10-24 22:00 (CEST) | Yes |
| 2026-10-25 23:30 | 2026-10-26 | 2026-10-25 23:00 (CET) | Yes |

**A generic "5% daily loss" check is not sufficient**, and the branch does not claim it is: the floor is
balance-based (a position carried over midnight with a floating loss leaves less room than 5%), 1-Step is 3% with
a trailing overall limit, and the operational rules above can end an account without any loss limit being touched.

---

## RISK ENGINE FINDINGS

### CR-01 — HIGH — Trailing high-water mark can be lowered by re-versioning the profile
- **File:** `src/trading/review_store.py:293-307`; `src/trading/rule_profiles.py:69-71`
- **Evidence:** boundaries are keyed by `(account_id, profile_hash)`. `profile_hash` hashes every profile field,
  including `user_confirmed_at`. Any re-confirmation starts an empty lineage, which accepts a fresh seed from the
  creation day with any balances (the `evidence_hash` is any 64-hex string and is never checked).
- **Reproduction:** `probes.py` → `R2`: lineage 1 balances 10,000→10,800 ⇒ high-water 10,800, floor **9,800**; same
  account, `user_confirmed_at` moved 30 min, all-10,000 lineage ⇒ high-water 10,000, floor **9,000**.
- **Impact:** for 1-Step, ARIA could report PASS while FTMO's real limit is breached ($800 here, up to the whole trail).
  Refutes `RISK_POLICY.md`: "no silent high-water decrease is allowed".
- **Minimal remediation:** key the high-water by account only; a new profile version inherits the account's latest
  high-water and may only raise it; refuse any boundary whose high-water is below the account's maximum on record.
- **Regression test:** record 10,800 under profile v1; re-confirm (v2); any v2 boundary path must yield high-water ≥ 10,800 or refuse.

### CR-02 — HIGH — Approval store trusts a caller-built `RiskAssessment`
- **File:** `src/trading/review_store.py:155-159, 161-175, 206-223`
- **Evidence:** `queue` and `reserve_intent` accept any `RiskAssessment` whose `verdict == "ALLOW"` and whose
  `proposal_hash` matches. It is a plain pydantic object: no signature, no recomputation, no link to the inputs.
  `authenticated_owner` is any non-empty string (documented as such).
- **Reproduction:** `probes.py` → `R1`: forged ALLOW for EURUSD 5 lots, 100-pip stop; intent reserved. `assess()` on the
  same proposal returns **DENY** (RISK_BUDGET_EXCEEDED, LEVERAGE_CAP); true loss **$5,000**.
- **Impact:** the user's requirement — independent deterministic validation as the final mandatory control — is not
  met by this design; it relies on every future caller being honest.
- **Minimal remediation:** `reserve_intent` takes `RiskInputs` (gathered by the worker itself) and calls `assess()`
  inside the transaction; or the risk process signs assessments with a key the store verifies. Keep the policy-hash check.
- **Regression test:** a hand-built ALLOW for a proposal that `assess()` denies must be refused by `queue` and `reserve_intent`.

### CR-03 — HIGH — Contract-size errors are undetectable; tick value is self-consistency only
- **File:** `src/trading/multiasset_risk.py:114-115, 171, 188-192`
- **Evidence:** the only cross-check is `tick_value == contract_size × tick_size`. If contract size and tick value are
  both wrong by the same factor (the usual mapping mistake), everything downstream — risk, notional, leverage, margin — is wrong by that factor.
- **Reproduction:** `probes.py` → `E5b`: XAUUSD contract 1 (true 100), 10 lots ⇒ engine "$50 risk" ALLOW; true $5,000 risk, $2.6M notional.
- **Impact:** exactly the "small-looking position, enormous notional" failure the review brief asked about.
- **Minimal remediation:** before ALLOW, require the broker's own numbers: MT5 `order_calc_profit(side, symbol, volume, entry, stop)`
  and `order_calc_margin(...)` (both already in `READ_OPERATIONS`) must agree with the engine within 0.5%; plus a reviewed
  per-symbol registry of expected contract sizes, and a hard per-asset-class lot cap.
- **Regression test:** spec with contract 1 for an instrument whose broker calculator implies 100 ⇒ UNVERIFIED.

### CR-04 — HIGH — FX conversion direction is checked by label only
- **File:** `src/trading/multiasset_risk.py:118-126`
- **Evidence:** the engine checks `from_currency`/`to_currency` labels; the value is never sanity-checked.
- **Reproduction:** `probes.py` → `E3b`: GBP→USD supplied as 0.7874 ⇒ $39.37 vs correct $63.50 (−38%), ALLOW.
  Inversion understates whenever the true rate is > 1: e.g. a CAD or AUD account converting USD (×~1.9) or a CZK
  account (×~530). FTMO offers non-USD account currencies.
- **Minimal remediation:** build the conversion inside the engine from a named FX pair quote (symbol, bid, ask), derive
  direction from the pair, use the side that maximises the loss, and refuse if a supplied rate differs by > 0.5%.
- **Regression test:** GBP→USD supplied as 1/GBPUSD ⇒ UNVERIFIED; correct pair ⇒ ALLOW with the conservative side.

### CR-05 — HIGH — Internal daily stop ignores the engine's own evidence
- **File:** `src/trading/multiasset_risk.py:174-177`
- **Evidence:** daily loss is `−daily_net_pnl` (caller field). The engine also holds `rule_state.daily_balance` and
  `account.equity` and never compares them.
- **Reproduction:** `probe_e7.py`: midnight balance 10,000, equity 9,520, `daily_net_pnl=0` ⇒ ALLOW; with −480 ⇒ EMERGENCY_HALT.
- **Minimal remediation:** `daily_loss = max(−daily_net_pnl, rule_state.daily_balance − account.equity, 0)`; refuse
  (UNVERIFIED) if the two disagree by more than costs.
- **Regression test:** the E8 case must return EMERGENCY_HALT whatever `daily_net_pnl` says.

### CR-08 — HIGH — FTMO operational rules are not machine-enforced; placeholders count as verified
- **File:** `src/trading/rule_profiles.py:46-51, 92-94`; `src/trading/multiasset_risk.py:55, 99-100`
- **Evidence:** `news_rule`, `weekend_rule`, `instrument_rule`, `prohibited_practices_rule` are free strings; only the
  literal `"UNVERIFIED"` fails verification (the fixture uses `"fixture:none"`). Enforcement reduces to one caller
  boolean, `operational_constraints_verified`. `EconomicEvent` and `TradingSession` contracts exist but are unused.
- **Impact:** a Standard FTMO Account can be ended for news-window execution or holding through a weekend; gap trading
  (all phases) can lead to removal of trades or termination (S4) — with every loss limit intact.
- **Minimal remediation:** enum rule values (`FTMO_STANDARD_NEWS_2MIN`, `NO_HOLD_BREAKS_OVER_2H`, `GAP_TRADING_2H_BEFORE_CLOSE`, `NONE`);
  unknown values ⇒ POLICY_UNVERIFIED; the engine takes the economic calendar and session calendar as inputs and denies
  inside windows; a pre-break flatten check for Standard funded accounts. Until FTMO clarifies "major news" scope: deny
  new trades on any high-impact event for the instrument's currencies on that day.
- **Regression test:** Standard FTMO Account, NFP at T+90 s ⇒ DENY; Friday 20:30 UTC entry before weekend ⇒ DENY; Swing ⇒ news allowed, gap rule still denies.

### CR-09 — MEDIUM — Server limits are per-minute; FTMO's are per-day
- **File:** `src/trading/rule_profiles.py:53`; `src/trading/multiasset_risk.py:149`
- **Evidence:** FTMO publishes 2,000 positions/day, >2,000 server requests/day (incl. SL/TP modifications) and 200
  orders at a time; no per-minute figure. The policy's 2/min allows 2,880/day; modifications are not counted at all.
- **Remediation:** per-Prague-day counters of positions opened and server requests (send + modify + cancel), and open-order count; deny at 80% of each.
- **Test:** 1,600 requests today, 0 in the last minute ⇒ DENY.

### CR-10 — MEDIUM — A trade may land exactly on the floor
- **File:** `src/trading/multiasset_risk.py:178-187`
- **Reproduction:** `probe_e7.py`: equity 9,020, floor 9,000, $20 risk ⇒ ALLOW; equity after stop = 9,000.00.
  Contradicts "conservatively refuses new risk at an exact floor" (`FTMO_RULE_PROFILES.md`). Any slippage beyond the allowance, swap or gap breaches.
- **Remediation:** internal floor = FTMO floor + buffer (e.g. max(0.5% of initial, 3× slippage allowance)); deny on `risk >= budget`.
- **Test:** E7 ⇒ DENY.

### CR-11 — MEDIUM — Book and operational inputs are self-asserted
- **File:** `src/trading/multiasset_risk.py:41-60, 84-85`
- **Evidence:** `kill_switch_clear`, `session_open`, `existing_open_risk`, `pending_order_risk`, `correlated_risk`,
  `spread_cost`, `commission`, `duplicate_position` are caller values. The legacy kill switch file is never read.
  Codex documents that an "authoritative snapshot builder" is required; none exists.
- **Remediation:** a deterministic snapshot builder that reads the kill switch, positions, orders and calendars itself and is the only constructor of `RiskInputs`.
- **Test:** legacy kill switch engaged ⇒ `assess` returns EMERGENCY_HALT without any boolean passed.

### CR-12 — LOW — FTMO detection by exact strings
- `rule_profiles.py:63` (`provider.upper() == "FTMO"`), `:87` (three product spellings). "FTMO s.r.o." or "FTMO Challenge 1-Step"
  skip the Prague check and the consistency-version selector. Use an enum for provider and product.

### CR-13 — LOW — One FX rate at today's price
- `multiasset_risk.py:126, 171`. For pairs whose base is the account currency (USDJPY on USD), the loss converts at the
  stop price: understatement ≈ entry/stop (≈1% for a 1% stop). Convert at the stop price for those pairs.

### CR-14 — LOW — `backup()` leaves the target connection open
- `review_store.py:331`: `with sqlite3.connect(...) as target` commits but does not close; on Windows the file stays locked until GC. Close explicitly.

---

## EXECUTION SAFETY FINDINGS

**All order-submission call sites (traced):** `src/execution/order_manager.py:162, 336-359`; `src/desk/position_manager.py:615`;
`src/trading/orders.py:176` (pipeline — no production caller); `src/trading/killswitch.py:244` (reduce-only flatten);
`backend/main.py:1753 approve_and_execute` (owner + step-up); adapters `alpaca_broker.py:315/452`, `ibkr_broker.py:397`.
**None is reachable from the new extension.** No LLM module imports a broker (`tests/test_trading_structure.py`).
These legacy paths were reviewed in earlier rounds; I re-traced call sites, not every line.

### CR-06 — HIGH — The MT5 "read-only" adapter does not enforce read-only
- **File:** `src/trading/mt5_readonly.py:36-66, 68-78, 101-129`
- **Evidence:** `connect()` accepts an account with `trade_allowed=True` and a terminal with algo trading on. Read-only
  is enforced only by the adapter's method names; `self._sdk` is the live `MetaTrader5` module, and that module is
  process-global — any code in the same process can call `MetaTrader5.order_send` once `initialize()` has run.
  `live_guard.py:316-330` closed exactly this bypass class for Alpaca/IBKR clients; the structure test only looks for `AlpacaBroker(`.
  Codex's "investor" test shows investor access is *allowed*, not *required*.
- **Reproduction:** `probes.py` → `M1`: trade-enabled fake account connects; `adapter.order_send()` refused;
  `adapter._sdk.order_send({...})` executed (1 call recorded).
- **Minimal remediation:** stage 1 must log in with the **investor (read-only) password** and refuse unless
  `account_info().trade_allowed is False`; run the adapter in a separate process exposing only a read RPC; extend
  `test_trading_structure.py` to forbid `import MetaTrader5` outside that worker module.
- **Regression test:** `connect()` with `trade_allowed=True` ⇒ MT5PermissionDenied; no `_sdk` attribute reachable from the RPC client.

### CR-07 — HIGH — "DEMO" is not a safety boundary at FTMO
- **File:** `src/trading/multiasset_risk.py:95-96`; `src/trading/mt5_readonly.py:75-77`
- **Evidence:** FTMO: "all accounts we provide to our clients are demo accounts with fictitious funds" (S1/S4/S8). A paid
  Challenge and a funded FTMO Account both pass a DEMO check, and both carry real money consequences (fee, reward).
- **Minimal remediation:** classify by consequence, not server label: `PRACTICE` (own demo, FTMO Free Trial),
  `PAID_EVALUATION`, `FUNDED`, `LIVE`. Only `PRACTICE` may ever receive an unattended or first-stage order; paid/funded
  require the full gate set and per-order human confirmation.
- **Regression test:** an FTMO-Demo account recorded as `PAID_EVALUATION` ⇒ demo-stage send refused.

### Other execution-safety checks

| Attack | Result | Evidence |
|---|---|---|
| Replay a used approval token | Refused (consumed flag, unique intent) | Codex tests, reproduced |
| Approval for one proposal used for another | Refused (proposal-hash binding) | Codex tests |
| Concurrent double reserve | Refused (BEGIN IMMEDIATE + unique) | Codex tests |
| Risk profile change while pending | Refused **if** the fresh assessment is honest (policy hash) — see CR-02 | `review_store.py:223` |
| Stale quote | UNVERIFIED after 15 s | tested |
| Unknown broker response / restart | Held at RECONCILIATION_REQUIRED, never resent | `recover()`, tested |
| Cross-account | Proposal binds an `account_id` string, but nothing binds it to MT5 `login@server` | Future worker must assert it |
| Live via env var alone | Not possible in the extension (no sender); legacy needs `ARIA_LIVE_TRADING_ENABLED` + mode + step-up | structure tests |
| Duplicate after timeout (future) | MT5 has no client order id; dedupe needs `magic` + comment + position scan before any retry | design note |

---

## PLUGIN/MCP SECURITY FINDINGS

- **No loader, no executor** — verified: `extension_policy.py` only evaluates declarations; `plugin_activation` always
  returns `active: False`; `mcp_tool_allowed` permits only analytical permissions. Nothing imports third-party code. INFORMATIONAL (positive).
- **RQ-01 — MEDIUM — Caller-asserted evidence classification** (`src/trading/contracts.py:179-183`,
  `src/research/multiasset.py:28-35, 228`): any request may label a claim `VERIFIED_EXTERNAL_FACT`; it is shown first in
  `supporting_evidence`. Reproduced (`probes.py` → `S_*`): a fabricated "ECB will cut 200bp" appears as verified support.
  This is the path an MCP/web-content prompt injection would use once an agent builds research requests. Fix: only a
  server-side source resolver may assign `VERIFIED_EXTERNAL_FACT` (URL fetched, hash stored, timestamp); client input is downgraded to `UNVERIFIED`.
  Test: client-supplied VERIFIED evidence returns as UNVERIFIED.
- **SEC-01 — LOW — Audit chain is unkeyed** (`review_store.py:127-143`): anyone who can write the file can rewrite the
  whole chain (Codex documents this). Add an HMAC key held outside the DB and periodic external anchoring before any sender.
- `ResearchRequest` strings (`claim`, `source_url`) have no length limit; owner-only, so LOW.

---

## TEST EVIDENCE ASSESSMENT

| Claim | My result |
|---|---|
| 104 new tests pass | **Reproduced**: 104 passed in 9.3 s (shared venv, `-p no:cacheprovider`, no bytecode writes) |
| Full suite 3,432 passed / 10 skipped | **Reproduced**: 3,432 passed, 10 skipped, 1 warning, 248 s; same 10 skip reasons |
| 104 pass in the isolated hash-pinned environment | UNVERIFIED (not rebuilt) |
| Frontend build + 4 offline verifiers | UNVERIFIED (would write into Codex's folder) |
| Dependency/secret/protected-path audits | UNVERIFIED (not re-run) |

| Area | Classification |
|---|---|
| Contracts, sizing, rule formulas, Prague boundaries | TESTED_WITH_FIXTURES |
| Review store: replay, race, restart, tamper, backup | TESTED_WITH_FIXTURES |
| Research triggers, splits, bootstrap | TESTED_WITH_FIXTURES |
| New API routes (TestClient, no lifespan) | TESTED_WITH_FIXTURES |
| MT5 adapter identity/switch/refusals | TESTED_WITH_MOCK_BROKER |
| Legacy Alpaca/IBKR order paths | TESTED_WITH_MOCK_BROKER (prior work) |
| Any real MT5 terminal, quote, fill, partial fill, reconciliation | **NOT_TESTED** — no terminal |
| MT5 → contract mapping (server-time bars, tick value currency) | **NOT_TESTED** — code does not exist |
| Snapshot builder, sender, worker, reconciliation | **NOT_TESTED** — do not exist |
| News / gap / weekend / per-day limits | **NOT_TESTED** — not implemented |
| Frontend hydration and real data | NOT_TESTED (Codex states this) |
| FTMO account behaviour | **BLOCKED** — no account; must use FTMO Free Trial first |

Coverage gap pattern: tests confirm the engine refuses what it is *told* is bad; none checks that it notices
contradictions it could compute (CR-02, CR-03, CR-04, CR-05).

---

## RED-TEAM SCENARIOS

Format: failure path → expected safe behaviour · code · existing evidence · severity · minimal fix · regression test.

1. **Floating loss carried over midnight** — position −$300 at 00:00 CE(S)T; caller reports today's P&L 0 → halt or size to the true room · `multiasset_risk.py:174` · none; probe E8 ALLOWs · HIGH (CR-05) · derive loss from balance vs equity · E8 ⇒ EMERGENCY_HALT.
2. **Trade sized to land on the floor, 1 pip extra slippage** → deny with buffer · `:178-187` · probe E7 ALLOWs · MEDIUM (CR-10) · buffer + `>=` · E7 ⇒ DENY.
3. **Profile re-confirmed, trailing floor reset** → high-water never falls · `review_store.py:293` · probe R2 · HIGH (CR-01) · account-keyed high-water · R2 ⇒ refuse.
4. **DST night (25 Oct 2026)** → one boundary per Prague day · `rule_profiles.py:111-116` · probe passes · INFO · none · keep table test.
5. **EU/US DST mismatch weeks** (server GMT+2+DST vs CE(S)T) — a future collector groups deals by server date → wrong daily P&L · no mapper exists · NOT_TESTED · MEDIUM (future) · group by Prague day from UTC instants only · fixture deals 2026-10-26..11-01 grouped correctly.
6. **Corrupted quote spike** (valid, 3% off) → refuse · `:159-166` tolerance vs touch catches proposals far from touch; no outlier check vs a reference feed · partial · MEDIUM · second-source deviation check · spike ⇒ UNVERIFIED.
7. **Stale quote 16 s** → UNVERIFIED · `:101-105` · tested · OK.
8. **Terminal disconnect / account switch mid-read** → refuse publish · `mt5_readonly.py:101-115` · mock-tested · OK (mock only).
9. **Order timeout then retry** → never resend; reconcile · `recover()` · tested for store; no worker · HIGH (future) · MT5 magic+comment correlation, position scan · simulated timeout ⇒ RECONCILIATION_REQUIRED, zero second sends.
10. **Partial fill, then stop placed for full size** → protective orders sized to filled volume · state vocabulary only · NOT_TESTED · HIGH (future) · size SL/TP from fills · partial-fill fixture.
11. **Weekend gap through stop on a Swing account** → risk includes gap allowance or flat before break · `:171-173` · none · MEDIUM · gap-risk term per asset class · Friday-hold fixture.
12. **Contract size mis-mapped (gold 1 vs 100)** → UNVERIFIED · `:114` · probe E5b ALLOWs 10 lots · HIGH (CR-03) · broker calculator cross-check · E5b ⇒ UNVERIFIED.
13. **Index CFD 1 lot looks small** → leverage cap · `:188-190` · probe E6 DENY · OK.
14. **NFP in 90 s on Standard FTMO Account** → deny · none · not implemented · HIGH (CR-08) · calendar input · DENY.
15. **Entry Friday 20:30 UTC** (≤2 h before weekend close) → deny, all phases · none · HIGH (CR-08) · session calendar · DENY.
16. **Account switched between approval and send** → refuse · proposal binds a string only · NOT_TESTED · HIGH (future) · bind `login@server` + currency into `account_id` and recheck at send · switch ⇒ refuse.
17. **FX rate inverted but labelled right** → UNVERIFIED · `:118-126` · probe E3b ALLOWs · HIGH (CR-04) · engine-built pair conversion · ⇒ UNVERIFIED.
18. **Forged ALLOW assessment** → refuse · `review_store.py:155` · probe R1 reserves · HIGH (CR-02) · recompute inside store · ⇒ refuse.
19. **Plugin manifest requests EXECUTE_DEMO_TRADE + secrets** → inactive · `extension_policy.py` · tested · OK.
20. **Prompt-injected "verified" evidence** → downgraded · `multiasset.py:228` · probe shows it as support · MEDIUM (RQ-01) · server-side verification only · ⇒ UNVERIFIED.
21. **"Read-only" adapter on a trading-enabled account** → refuse connect · `mt5_readonly.py:41-66` · probe M1 connects; raw SDK sends · HIGH (CR-06) · investor login + process isolation · ⇒ refuse.
22. **App restart with SUBMISSION_PENDING intent** → hold, never resend · `review_store.py:234-246` · tested · OK.
23. **Reconciliation finds nothing (404-equivalent)** → stay UNKNOWN; owner release only · no worker · NOT_TESTED · HIGH (future) · port the S1.3 rule ("not found is not proof") · fixture.
24. **Legacy kill switch engaged** → extension refuses · input is a boolean · NOT_TESTED · MEDIUM (CR-11) · read `kill_switch.py` · engaged ⇒ EMERGENCY_HALT.
25. **2,000 positions/day reached via many small scalps or SL edits** → deny before limit · `:149` per-minute only · MEDIUM (CR-09) · per-day counters · ⇒ DENY.
26. **Contradictory hypotheses on one bar** → one reconciled view or abstain · `multiasset.py:212-225` · probe: SupportResistance BUY, Breakout SELL, MeanReversion BUY on the same support break · MEDIUM (RQ-02) · cross-family conflict ⇒ ABSTAIN; fix SR rule (close below support is not a bounce) · conflict ⇒ ABSTAIN.
27. **Backtest drawdown breaker** → exit at the next tradable price · `backtester.py:265, 284` · probe: exit at 100 after a close of 70; final equity 100,000 instead of ~70,000 · HIGH (QA-01) · exit at next bar's open; never use a bar's open after its close · probe ⇒ exit ≤ 70.

---

## BLOCKERS

Before **read-only demo connection** (READY_FOR_READ_ONLY_DEMO): CR-06, CR-07, an MT5→contract mapper with
server-time tests (RT-5), and use of a practice account only (FTMO Free Trial or a broker demo — never the paid account).

Before **any human-confirmed demo order** (READY_FOR_HUMAN_CONFIRMED_DEMO): all of the above plus CR-01, CR-02, CR-03,
CR-04, CR-05, CR-08, CR-09, CR-10, CR-11; a reviewed worker with reconciliation (RT-9, 10, 16, 23); an always-on Windows
host (OP-01); and **written FTMO confirmation** on the AI/software clause and on automated or remotely hosted operation.

Before **quoting any backtest result**: QA-01, QA-02, QA-03.

## RECOMMENDED FIXES (in order)

1. CR-02 recompute risk inside the store — removes the "trust the caller" pattern everywhere downstream.
2. CR-05, CR-10 — two-line changes; stop the engine ignoring its own evidence and add a floor buffer.
3. CR-03, CR-04 — broker-calculator and FX-pair cross-checks; these are the large-loss paths.
4. CR-01 — account-keyed, monotone high-water.
5. CR-06, CR-07 — investor-only adapter in its own process; consequence-based account classes.
6. CR-08, CR-09 — enum rules, calendars, per-day counters.
7. QA-01..03 — backtester timing, intrabar stops, per-asset annualisation.
8. RQ-01, RQ-02, CR-11..14, SEC-01, UI-01.

**UI-01 — LOW:** the Portfolio banner "EXECUTION DISABLED" sits above the legacy desk, whose auto-execute can be
armed for paper trading (it is off at the time of the cloud-migration handover). The paragraph beneath clarifies it; label it
"EXTENSION: NO ORDER SENDER" so a quick glance cannot mislead whenever the desk is re-armed.

**OP-01 — MEDIUM (operations):** the `MetaTrader5` Python package is published for Windows only and drives a local
terminal. Since 2026-11-06 the active ARIA runtime is a Linux **ARM** cloud VM (2 cores, no paid
upgrade) per Codex's handover; it cannot run the MT5 terminal natively, and the Windows writer tasks are deliberately
disabled. A future MT5 worker therefore needs a *separate, always-on* Windows host bridged to the cloud runtime — a
new trust boundary (account credentials on one machine, decisions on another) that must be designed and reviewed,
and is not available on the current setup. The handover also records the 2-core host running the local
7B model at 3.63 tokens/s with brain timeouts — no latency-sensitive path should depend on it. Any position opened
must carry a broker-side stop, and nothing should be built that assumes ARIA is watching.

**Runtime context (from Codex's cloud-migration handover, kept outside this repository, 2026-11-06 — not verified by me on the server):**
the cloud runs a snapshot of integration `58f5f25`, which **does not include this branch**, so none of the CR/RQ
findings affect the running service. QA-01..03 are in code that *is* deployed (`src/backtesting/`), but
`run_backtest` is off by default; whether the cloud config enables it is UNVERIFIED. Safety state at handover:
broker disconnected, PAPER, live gate closed, auto-execute and reflex off.

## UNVERIFIED CLAIMS

| Codex claim | Status |
|---|---|
| "No silent high-water decrease is allowed" (RISK_POLICY.md) | **REFUTED** (CR-01) |
| "Conservatively refuses new risk at an exact floor" | **PARTLY REFUTED** — true for the breach test, false for sizing (CR-10) |
| "Correctly directed fresh FX conversion" required | **Labels only** (CR-04) |
| MT5 "investor tests"; "read-only in this release" | **Overstated** — investor allowed, not required; raw SDK reachable (CR-06) |
| Review binding makes approvals safe | True for replay/race/substitution; **not** for forged assessments (CR-02) |
| 3,432 passed / 10 skipped; 104 new tests pass | **VERIFIED** |
| 228 routes preserved, 2 added | **VERIFIED** by diff (+31/−0 in `backend/main.py`) |
| No sender / no new execution path | **VERIFIED** (call-site trace), with the CR-06 caveat |
| Isolated-lock run, frontend build/verifiers, npm/pip audits, secret scan, protected-path check | **UNVERIFIED** (not re-run) |
| FTMO formulas and 12 Oct 2026 cutover | **VERIFIED** against official pages fetched 2026-11-06 |
| FTMO restrictions beyond loss limits | Codex did not claim compliance; **confirmed not implemented** (CR-08, CR-09) |
| UK eligibility | **UNVERIFIED** — UK not on FTMO's restricted table, but no explicit statement found |

If Codex disputes a finding, the dispute should come with a failing-then-passing test or a probe output that
contradicts the reproduction above; the probes in `claude_review_probes/` are the reference cases.

## RELEASE-GATE RECOMMENDATION

**CONDITIONAL_REVIEW.**

- The research API, contracts and the disconnected Portfolio panel are safe to integrate **as research-only code**
  once the docs stop claiming the guarantees refuted above (or the fixes land). They cannot place an order.
- **Not READY_FOR_READ_ONLY_DEMO** until CR-06 and CR-07 are fixed and tested.
- **Not READY_FOR_HUMAN_CONFIRMED_DEMO** until every blocker above is fixed with a regression test and FTMO has
  answered the AI/automation and third-party questions in writing. Ambiguous rule ⇒ execution stays disabled.
- **No live-execution approval.** No paid FTMO account should be bought on the strength of this system yet: no
  strategy has out-of-sample evidence, and at zero edge the pass probability is about 11% after costs.
