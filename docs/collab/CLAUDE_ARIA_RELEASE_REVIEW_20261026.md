**Verdict: APPROVE WITH LIMITATIONS.** I found no Critical, High or Medium regressions in 6c30df5 compared with 39de9f6. The M-1, M-2 and L-1 changes do what the description says, and no order, claim or risk guard is weaker. I found four Low issues. This was a read-only review: I ran nothing, so the pass counts you quoted (101 affected tests, npm audit, pip-audit, Bandit) are your figures, not mine.

## What checks out

- **M-1, holding while a durable exit is unseen** (`position_manager.py:900-907`). `at_broker` reads positions and orders fresh, skips the existing protective IDs, and then checks durable in-flight claims (`reduce_only.py:171-177`). An unseen reduce-only claim stops healing before anything is cancelled, and the reason is reported.
  - Before this change, healing could cancel the stops and then have `place_protective_orders` refuse the replacement (`order_manager.py:659-664`). That left the position unprotected. That path is now closed.
- **M-1, no needless replacement.** Stops and targets are now sized against the free quantity (`room.qty`) rather than the full position. A 4-share stop/target next to a visible 6-share exit counts as enough.
  - If visible exits cover the whole position, `room.ok` is false, so healing stops and nothing is cancelled. That is safer than before.
  - The replacement is still clipped by `place_protective_orders`'s own `at_broker` check (`order_manager.py:665`), so passing the full `pos["qty"]` at `:946` cannot over-cover.
- **A possible stale-claim deadlock is avoided.** The durable-claim check now runs before the `reconcile_unresolved()` call inside healing (`:942`). But `desk_daemon.py:367` reconciles every management tick, and the placement path already had the same refusal, so this doesn't add a new deadlock.
- **M-2:** The wait is bounded: 2 s, then 8 s more. If cancellation is still unconfirmed, nothing is sent. Correctly, this is not a claim of guaranteed protection.
- **L-1:** This only tightens the rule, and it fails closed. The real producer writes `data_as_of` on every row (`main.py:555`, `main.py:1148`). No threshold or calendar changed.
- **The IBKR test changes** (SUBMITTED → UNKNOWN) match the stricter status rule from the previous round.

## Low findings

| # | Location | Trigger | Minimal fix |
|---|---|---|---|
| L-a | `position_manager.py:939-941` | A cancellation is still unconfirmed after 10 s. The message says "urgent" but is only added to `summary["errors"]` next to routine errors. There's no log line and no alert. The venue may finish the cancellation after this returns, leaving the position unprotected until the next tick. | Add `logger.critical(...)` and send it through whatever alert path the owner watches, kept separate from routine tick errors. |
| L-b | `position_manager.py:904-907` | A visible full-size exit, such as a pending market sell, gives "already covered" each tick. It is logged as "bracket healing held" in `errors` every tick, which is noise that can hide L-a. | Report only the durable-claim and unreadable reasons as errors. Log "already covered" at info level. |
| L-c | `position_manager.py:935-938`, called in a loop at `:438` | The retry only waits longer; it doesn't send the cancel again. The 10 s limit applies per ticker, not per tick, so N slow tickers block the tick for about N×10 s. | Add a shared time budget for the tick, or keep the 8 s retry for one ticker per tick. |
| L-d | `world.py:139` with `main.py:1094-1099` | If one ticker's price frame is unreadable, `_last_bar_date` returns `None`. Under L-1 that one row now makes the whole signals lane stale. Before, `generated_at` was enough. This fails closed, so it affects availability only. | Leave unreadable tickers out of `signals.json` instead of writing `data_as_of: null`, or have the owner accept this. |

## Limits of the evidence

- **Two older behaviours remain; neither is a regression.**
  - Healing still treats any `limit` order as protection, so a non-protective limit exit is left out of the room calculation and then cancelled.
  - `resting_reduce_qty` counts an exit's full quantity rather than what's left after a partial fill. That makes the free room look smaller than it is, so a new stop can be smaller than it needs to be.
- **The new tests run against mocks.** They use the fake `_HaltedBook` broker, and the slow-cancel test replaces `_await_cancelled` with a `Mock`. They show the logic works; they show nothing about real venue cancellation timing.
- **The dependency changes** (Vite pinned to exactly 8.2.4 rather than `^`, Pillow raised to `>=13,<14`) are version changes only and introduce no source regression.
- **Blockers you already listed are unchanged:** Python 3.14.3 host certification, Bandit's 42 retained findings, paper venue, fees, historical UNKNOWN, IBKR, hosting and edge.