"""
src/v5 — ARIA V5: Institutional Multi-Strategy Quantitative Intelligence Platform.

Layer map (each stage may only be reached through the one above it):

    identity      the Two Absolute Laws + persona, injected into every LLM call
    contract      ModuleReport — the single output type every research module returns
    registry      individually-callable research modules, isolated failure
    modules/      the multi-strategy research engine itself
    ensemble      documented weighting, agreement map, variance-decayed confidence
    risk_gate     non-negotiable veto layer — sizing, drawdown, correlation, stress
    meta          meta-reasoning — counterargument, falsification, alternative path
    audit         the self-audit block required in every report
    learning      prediction log, outcome attribution, significance-gated reweighting
    pipeline      the orchestrator that runs the whole chain
    report        investment-committee rendering

Nothing here executes trades. The desk (src/desk) remains the only path to an
order, and it still requires human approval.
"""
from src.v5.contract import Evidence, ModuleReport, insufficient
from src.v5.identity import ARIA_V5_IDENTITY, system_prompt

__all__ = [
    "ModuleReport",
    "Evidence",
    "insufficient",
    "ARIA_V5_IDENTITY",
    "system_prompt",
]
