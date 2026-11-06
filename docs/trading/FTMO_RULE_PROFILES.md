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
uses Europe/Prague boundaries and conservatively refuses new risk at an exact
floor. [Objectives](https://ftmo.com/en/trading-objectives/).

The 8 October update specifies a 12 October 2026 **09:00 GMT+3** cutover for new
1-Step orders; applicable merges also change the formula. Earlier orders keep
the legacy rule. New consistency uses net closed P/L; legacy uses positive days.
Both 50% formulas are separate; exact creation/merge timestamps are required.
[Official update](https://ftmo.com/en/blog/trading-updates/trading-update-8-oct-2026/).

Profile identity includes provider/entity/jurisdiction, product, account, size,
currency, stage/type, creation date/exact order/merge time, validity window,
sources, verification, user confirmation, version and computed content hash.
Unverified restrictions cannot become a permissive executable policy.

Prohibited-practices compliance is a separate review of strategy behaviour, not
just percentage limits. No automatic claim of compliance is made.
[Official practices](https://ftmo.com/en/forbidden-trading-practices/).
