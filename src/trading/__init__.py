"""
src/trading — ARIA's systematic trading core.

    research / reasoning  (LLM, research engine, SENTINEL)   ── never touches a broker
              │
              ▼
    STRATEGY → Signal                                          signals.py, lifecycle.py
              │
              ▼
    RISK ENGINE → RiskDecision (size, or NO TRADE + reasons)    risk.py, limits.py, costs.py
              │
              ▼
    EXECUTION VALIDATOR → violations or clear                   validator.py, market.py
              │
              ▼
    ORDER ENGINE → idempotent, journalled, fail-closed          orders.py
              │
              ▼
    BROKER ADAPTER → gated by execution mode                    broker.py, access.py, modes.py

Every layer is deterministic, has no model in it, and is tested on its own.
The language model proposes research; it has no path to an order. That is
checked structurally by tests/test_trading_structure.py, which fails if any
module here imports the inference, brain or consultation packages.

WHAT IS WIRED TODAY: the layers above run end to end only inside
TradingPipeline (pipeline.py), and no production caller runs it — the tests
exercise it. Orders ARIA actually sends (approval queue, desk auto-executor,
reflex lane) go through src/execution/order_manager.OrderManager, which applies
its own session, reduce-only and portfolio-risk checks and reaches the broker
through access.py's mode gate. So the order journal in orders.py holds pipeline
orders only; see orders.JOURNAL_SCOPE.

MODES: RESEARCH (default, no orders) → PAPER → LIVE_AUTONOMOUS. LIVE needs an
environment switch set outside the application; this deployment does not set it.

This is a private system for the owner's own account. It has no features for
other people's money, signal selling or copy trading, and must not grow them
without a separate regulatory review — see docs/TRADING_ARCHITECTURE.md.
"""
