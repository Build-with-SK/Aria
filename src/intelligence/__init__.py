"""
src/intelligence
================
Prediction and model intelligence: which prediction to trust, how much, under
what regime, from which model — and whether it may influence the book at all.

Built on the prediction ledger (src/core/ledger.py), never beside it. Every
module here reads ledger rows, or rows shaped like them, and every number it
reports carries the sample it rests on. Where the sample is too small the
answer is an explicit status (INSUFFICIENT_DATA, CALIBRATION_INSUFFICIENT_DATA,
ROUTING_INSUFFICIENT_EVIDENCE), not a number.

    semantics      what a number in the ledger IS: a stated or calibrated
                   probability, a score, a confidence, nothing, or unknown
    baseline       the Phase 1 data-integrity audit (counts only)
    dataset        the one canonical evaluation set, with every exclusion
                   classified and kept
    regrade        a same-series re-measurement of defective historical
                   grades — a view, never written to the ledger
    evidence       one snapshot of all of the below, built off the trade path
                   and read by it
    recorders      forward-only ledger recording for sources that left no
                   falsifiable trace (the ML ensemble)
    evaluation     per-model metrics, gated on sample size
    calibration    calibration status per model, strictly temporal
    regime         the macro-regime audit and a point-in-time market regime
    registry       model cards; status is evidence-derived or owner-set,
                   never promoted automatically
    routing        evidence-based model selection with a conservative default
    ensemble       whether models add independent information
    debate_eval    what the debate desk measurably adds
    conviction     a decomposed conviction that a prediction hands to the
                   portfolio-risk layer — never permission
    provenance     what every new prediction must carry
    pit            point-in-time feature snapshots, used by the adversarial
                   look-ahead tests
    walkforward    chronological train / validation / test windows, with every
                   trial recorded
    multiple_testing  Holm / BH corrections for anything selected among many

Nothing in this package may place, approve or execute an order, and nothing in
it may raise a limit. src/trading still may not import it (or anything that
reaches a language model): tests/test_trading_structure.py enforces that.
"""
