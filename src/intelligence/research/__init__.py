"""
src/intelligence/research
=========================
The market intelligence & research engine: assemble independent, point-in-time
evidence about one instrument and explain what is happening, why it might be,
whether it is unusual, whether independent evidence agrees, and what would
invalidate the view — without one opaque confidence number.

    sources        the audited catalogue of every evidence source: input,
                   timestamp semantics, PIT status, evidence group
    components     one snapshot component: value + as_of + source + version +
                   freshness + provenance + PIT status
    pit_data       the data layer every query reads, cut at T (frames for
                   tests and replay, live vendors in production)
    snapshot       the canonical ResearchSnapshot
    attribution    absolute -> market / sector / factor / currency ->
                   UNEXPLAINED_RESIDUAL
    cross_asset    rolling correlation, beta, breakdown, divergence, lead/lag
    events         earnings, dividends, splits, releases: visible only once
                   knowable
    news           structured news evidence, clustered so syndication is one
                   item, not fifteen
    evidence_graph evidence items, their roots and groups, and how many are
                   really independent
    analogues      historical analogues, point in time, or
                   ANALOGUE_INSUFFICIENT_DATA
    thesis         the structured thesis and the quality scorecard
    engine         orchestration, caching, timings, prediction integration

Nothing here places, sizes or approves an order. A thesis is not a signal:
it reaches the trade path only as recorded context inside a conviction
(src/intelligence/conviction), which can never enlarge a position.
"""
