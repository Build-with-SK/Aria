# Quantitative validation

Reused research/backtest engines remain in `src/backtesting/`, `src/v5/walkforward.py`,
`src/intelligence/walkforward.py`, `src/evolution/` and trading scorecard/lifecycle.
This branch does not promote or retune an existing strategy.

Added `src/backtesting/experiments.py`: exact dataset hashes, versioned experiment
manifests (revision, strategy/version, parameter/cost JSON, seed, assumptions),
chronological overlap-purged splits with embargo, explicit net P/L after spread,
commission/slippage/financing and seeded moving-block resampling. Missing ruin
model returns no invented risk-of-ruin probability.

Added research definitions for ten families; MacroDriven explicitly abstains
because no verified PIT macro event model is configured. The other deterministic
triggers are educational research recipes, not optimised or validated strategies.
Regime suitability, completed candles, warm-up and identity/basis checks apply.
Counterevidence includes stretched RSI and alternative scenarios; independent
quantitative/provider verification remains required before any account action.

No new full contract-specific fill simulator, stop-gap/partial-fill model,
survivorship-safe universe or corporate-action replay was built. Existing indicator
and replay tests are retained, but these additions have **UNVERIFIED** validation
status. A definition or unit test cannot create OUT_OF_SAMPLE_VALIDATED,
PAPER_TESTED or BROKER_DEMO_TESTED evidence. No return target was fitted.
