# Architecture

```mermaid
flowchart LR
  UI[Existing Portfolio workspace] --> API[Existing FastAPI app / owner guards]
  API --> Status[Disconnected extension status]
  API --> Research[Typed supplied-candle research]
  Research --> TA[Existing ta and technical summary]
  Research --> Hypothesis[Hypothesis / ABSTAIN / human review]
  Contracts[Immutable Decimal and UTC contracts] --> Research
  Contracts --> Risk[Independent linear-contract risk]
  Rules[Versioned confirmed policy / Prague boundaries] --> Risk
  Risk --> Review[Explicit-path SQLite review store]
  Human[Future authenticated owner integration] -. not exposed .-> Review
  Review --> Intent[Durable reserved intent / reconciliation hold]
  MT5[Optional local Windows terminal] -. read-only demo identity .-> Adapter[MT5ReadOnlyAdapter]
  Intent --- Disabled[No sender exists in this extension]
```

Existing `src/trading/pipeline.py`, `src/execution/order_manager.py` and `src/desk/`
remain separate legacy paths. Their existing kill/mode/pretrade/reduce-only gates
are retained; no new proposal is routed into them automatically.

Contracts use Pydantic 2, matching FastAPI's installed schema stack. Provider
identity is explicit; FX lots are never passed into legacy share-sized orders.
Data observations carry provider, symbol, UTC timestamps, quality and permissions.
Technical indicator work uses the existing pandas/ta stack; exact money/risk is
Decimal. There is no model import below the deterministic trading boundary.

`ReviewStore` is additive schema version 1: proposals, approvals, intents, audit
and rule_boundaries. Foreign keys, BEGIN IMMEDIATE and synchronous FULL support
single-use approval and atomic intent persistence across connections. No default
store is created by imports, HTTP handlers or startup. No production migration.
