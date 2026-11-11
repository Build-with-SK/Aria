# FTMO source discovery — 2026-11-06

**No executable FTMO profile exists in this branch.** There is no purchased or
configured account, confirmed entity/jurisdiction, exact order timestamp or human
profile confirmation. The UI returns POLICY_UNVERIFIED. Tests use a fictional
`FIXTURE_NOT_FTMO` profile, never a presumed $10,000 account.

Official comparison discovery: 2-Step targets 10%/5%, daily loss 5%, overall loss
10% static; 1-Step target 10%, daily loss 3%, overall loss 10% on an end-of-day
balance trail. These are documentation references, not active defaults.
[Comparison](https://ftmo.com/en/comparison-table/).

Loss calculations use equity and midnight CE(S)T balance snapshots. This engine
uses Europe/Prague boundaries. Equity at or below a floor is a breach, and new
risk must keep equity above the floor **plus a buffer** of max(0.5% of initial
capital, 3 × the slippage allowance) even if the stop is hit; a trade whose worst
case equals its remaining budget is refused (review CR-10).
[Objectives](https://ftmo.com/en/trading-objectives/).

The 8 October update specifies a 12 October 2026 **09:00 GMT+3** cutover for new
1-Step orders; applicable merges also change the formula. Earlier orders keep
the legacy rule. New consistency uses net closed P/L; legacy uses positive days.
Both 50% formulas are separate; exact creation/merge timestamps are required.
[Official update](https://ftmo.com/en/blog/trading-updates/trading-update-8-oct-2026/).

## Account class and closed vocabularies (review CR-07, CR-12)

FTMO states that every account it provides is a demo account with fictitious
funds — the paid Challenge, the Verification and the funded FTMO Account alike.
A broker's DEMO label therefore says nothing about what a loss costs. Each
profile declares an `account_class` by consequence:

| Class | Meaning | FTMO stage |
|---|---|---|
| `PRACTICE` | own demo, nothing to lose | `TRIAL` (Free Trial) |
| `PAID_EVALUATION` | a fee is at stake | `CHALLENGE`, `VERIFICATION` |
| `FUNDED` | a reward split is at stake | `FUNDED` |
| `LIVE` | own money | — |
| `UNVERIFIED` (default) | undeclared: POLICY_UNVERIFIED | — |

For an FTMO profile the class is fixed by the stage: a Challenge declared
`PRACTICE` fails validation. A `LIVE` class is UNVERIFIED in the risk engine
whatever the server says. **Only a `PRACTICE` account may enter the order-review
store in this release** (`queue` and `reserve_intent` refuse every other class);
research sizing still runs on any class.

Provider, product, stage, account type and class are closed enums. FTMO is
recognised as `provider="FTMO"` with a product of `FTMO_2_STEP`, `FTMO_1_STEP`
or `FTMO_FREE_TRIAL` — never by matching text such as "FTMO s.r.o." or a
product spelling. The two go together (an FTMO product under another provider
is rejected), an FTMO profile must use Prague midnight, and the 1-Step
consistency selector keys on `FTMO_1_STEP`. The legal entity stays free text in
`provider_entity`. The rule engine, risk engine and review store rebuild their
inputs through validation, so an unvalidated `model_copy` cannot smuggle in a
value the enums refuse.

## Operational rules (review CR-08, CR-09)

Each operational rule is a closed enum; any value outside it fails validation,
and `UNVERIFIED` is POLICY_UNVERIFIED. For FTMO the published value is fixed by
stage and type, and a profile may only state it or leave it UNVERIFIED:

| Rule | Standard FTMO Account (funded) | Swing; Challenge; Verification; Trial |
|---|---|---|
| `news_rule` | `NO_EXECUTION_2MIN_AROUND_NEWS` | `NONE` |
| `weekend_rule` | `NO_HOLD_THROUGH_BREAKS_OVER_2H` | `NONE` |
| `gap_rule` | `NO_ENTRY_2H_BEFORE_LONG_BREAK_OR_MAJOR_NEWS` | the same (forbidden practice in every phase) |
| `overnight_rule` | `NONE` | `NONE` |

FTMO has not defined "major" news. Until it does, every HIGH or UNKNOWN impact
release for one of the instrument's currencies, or about the instrument itself
(earnings), from two minutes ago to the end of the Prague day blocks new
entries. This is stricter than the published rule; written clarification from
FTMO may relax it.

Daily limits are per Prague day: `max_positions_per_day` ≤ 2,000,
`max_server_requests_per_day` ≤ 2,000 (FTMO calls more "hyperactive"),
`max_open_orders` ≤ 200. FTMO publishes no per-minute figure, so
`max_server_requests_per_minute` is optional.

Profile identity includes provider/entity/jurisdiction, product, account, size,
currency, stage/type, creation date/exact order/merge time, validity window,
sources, verification, user confirmation, version and computed content hash.
Unverified restrictions cannot become a permissive executable policy.

Prohibited-practices compliance is a separate review of strategy behaviour, not
just percentage limits. No automatic claim of compliance is made.
[Official practices](https://ftmo.com/en/forbidden-trading-practices/).
