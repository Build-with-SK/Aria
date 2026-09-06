"""
src.core — ARIA's spine.

Before this package existed, every subsystem in ARIA talked to every other one
by writing a JSON file into data/ and hoping somebody read it. The brain wrote
brain_state.json, the desk wrote slate.json, the research eye wrote
observations.jsonl, V5 wrote predictions.jsonl — and nothing subscribed to
anything. Intelligence was computed and then dropped on the floor.

Four modules fix that, and they are deliberately small:

    bus     — an append-only event log with in-process subscribers. The one
              place a component announces that something happened.
    ledger  — the prediction and decision record. Every claim ARIA makes with a
              horizon lands here and is graded when the horizon elapses.
    workers — the registry of continuous processes: who is running, when they
              last ran, what broke. A worker that dies is now visible.
    world   — the assembled belief state. What ARIA currently thinks is true
              about markets, risk, its portfolio and its own strategies, with
              a diff against the past so "what changed?" is answerable.

Nothing here fabricates data. Every value is read from a subsystem that already
computes it, or is absent and says so.
"""
