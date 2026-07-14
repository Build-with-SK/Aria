# ARIA COGNITIVE BRAIN — COMPLETE BUILD PROMPT

## THE VISION

You are building a genuine cognitive AI brain — not a chatbot wrapper, not an API replacement. This brain runs continuously as a background daemon, perceives market conditions on its own, thinks in multi-step reasoning loops, remembers what it has done and learned, and autonomously proposes trades. The local LLM (Ollama) is only the language center — one component inside a larger cognitive architecture.

Think of it like a human trader's brain:
- **Perception**: reads all incoming signals the moment they're available
- **Working memory**: holds the current market picture in mind
- **Long-term memory**: recalls similar past situations and what happened
- **Reasoning**: thinks through possibilities in multiple steps before deciding
- **Action**: proposes trades through the approval queue
- **Reflection**: after a trade closes, records what it learned

The UI shows the brain's internal monologue in real time — you can watch it think.

---

## ENVIRONMENT

```
OS            : Windows 11
GPU           : NVIDIA GeForce RTX 4060 Laptop GPU, 8188 MiB VRAM
Python venv   : C:\Users\sound\Documents\trading-intelligence-system\venv\Scripts\python.exe
PyTorch       : 2.1.0+cpu (replace with CUDA build before fine-tuning)
Ollama        : v0.31.1 at localhost:11434
Ollama models : qwen2.5-coder:7b (4.7GB), gemma3:4b (3.3GB)
Project root  : C:\Users\sound\Documents\trading-intelligence-system
Backend       : FastAPI port 8000, backend/main.py
Frontend      : React+Vite port 3000, frontend/src/
```

---

## EXISTING CODEBASE

```
backend/main.py                  FastAPI — add endpoints here only
frontend/src/App.jsx             Add /brain route
frontend/src/components/Sidebar  Add BRAIN nav
frontend/src/pages/Chat.jsx      Add cloud/local toggle
src/execution/approval_queue.py  Brain pushes trades here
src/execution/broker_base.py     OrderRequest, AssetClass etc.
data/signals.json                Live signals — {ticker: {composite_score, action, confidence,
                                   bullish_prob, bearish_prob, trend_score, momentum_score,
                                   volatility_score, regime, stop_loss, take_profit,
                                   invalidation, position_size_pct, drivers, risks, explanation}}
data/ml_predictions.json         {ticker: {overall_bullish, overall_signal, horizons:{1,5,20}}}
data/macro_data.json             {regime, macro_score, vix, dxy, treasury_10y}
data/backtest_results.json       {ticker: {metrics:{sharpe_ratio, win_rate, ...}}}
data/trading_intelligence.db     SQLite — signal history
src/brain/__init__.py            EXISTS (empty)
```

---

## ARCHITECTURE OVERVIEW

```
┌─────────────────────────────────────────────────────┐
│                  ARIA COGNITIVE BRAIN                │
│                                                     │
│  ┌──────────┐   ┌─────────────┐   ┌─────────────┐  │
│  │PERCEPTION│──▶│   WORKING   │──▶│  REASONING  │  │
│  │  LAYER   │   │   MEMORY    │   │    LOOP     │  │
│  └──────────┘   └─────────────┘   └──────┬──────┘  │
│       ▲                ▲                 │         │
│       │         ┌──────┴──────┐          ▼         │
│  market data    │  LONG-TERM  │    ┌──────────┐    │
│  signals.json   │   MEMORY    │    │ PLANNER  │    │
│  macro_data     │  (ChromaDB) │    └────┬─────┘    │
│                 └─────────────┘         │          │
│                        ▲                ▼          │
│                        │         ┌──────────┐      │
│                   ┌────┴────┐    │ EXECUTOR │──▶ approval_queue
│                   │LEARNER  │◀───└──────────┘      │
│                   └─────────┘                      │
└─────────────────────────────────────────────────────┘
           ▼
   brain_daemon.py  (APScheduler — runs every 15 min)
           ▼
   /api/brain/*  (FastAPI endpoints — UI reads thought stream)
```

---

## MODULE 1 — PERCEPTION LAYER

### `src/brain/cognitive/perception.py`

```python
class MarketPerception:
    """
    Reads all available data and produces a structured snapshot of
    what the market looks like RIGHT NOW. Pure data, no opinions.
    """
    
    def perceive(self) -> PerceptionSnapshot:
        """
        Returns PerceptionSnapshot dataclass with:
          signals: dict[str, SignalData]     — all tickers from signals.json
          macro: MacroState                  — from macro_data.json
          ml: dict[str, MLPrediction]        — from ml_predictions.json
          top_opportunities: list[str]       — top 10 tickers by |composite_score|
          regime_shift: bool                 — True if regime changed since last run
          alerts: list[str]                  — tickers with score crossing ±30 threshold
          timestamp: datetime
        """
```

`PerceptionSnapshot`, `SignalData`, `MacroState`, `MLPrediction` — define as dataclasses. Map fields directly from the JSON schemas listed in the codebase section above.

`alerts` are tickers where `abs(composite_score) > 30` AND `confidence == "High"` — these get priority in the reasoning loop.

---

## MODULE 2 — WORKING MEMORY

### `src/brain/cognitive/working_memory.py`

Working memory holds what the brain is currently thinking about — a scratchpad that lives only for the duration of one reasoning cycle.

```python
@dataclass
class WorkingMemory:
    cycle_id: str           # uuid — unique per reasoning cycle
    started_at: datetime
    focus_tickers: list     # tickers the brain is actively considering
    current_hypothesis: str # what the brain currently thinks is happening
    reasoning_steps: list   # [{step, thought, conclusion}] — the internal monologue
    candidate_trades: list  # trades being considered before committing
    context_summary: str    # compressed version of what the brain knows this cycle
    warnings: list          # reasons to be cautious this cycle
```

Methods:
- `add_thought(step: str, thought: str, conclusion: str)` — appends to reasoning_steps
- `summarize() -> str` — returns a compact text summary of the current working state
- `to_dict() -> dict` — for JSON serialization to the API

Working memory is reset at the start of every reasoning cycle. It is NOT persisted to disk directly — only its summary is stored in long-term memory after the cycle.

---

## MODULE 3 — LONG-TERM MEMORY

### `src/brain/cognitive/long_term_memory.py`

The brain's permanent memory. Uses ChromaDB (vector database) for semantic search — the brain can ask "have I seen a situation like this before?" and retrieve relevant past reasoning cycles.

```python
# Install: venv\Scripts\pip.exe install chromadb sentence-transformers
import chromadb
from sentence_transformers import SentenceTransformer

class LongTermMemory:
    """
    Stores and retrieves memories as vector embeddings.
    Each memory = one completed reasoning cycle.
    """
    
    DB_PATH = Path("data/brain_memory/chromadb")
    
    def __init__(self):
        self.client = chromadb.PersistentClient(path=str(self.DB_PATH))
        self.collection = self.client.get_or_create_collection("aria_memories")
        self.embedder = SentenceTransformer("all-MiniLM-L6-v2")  # 80MB, runs on CPU fine
    
    def remember(self, memory: BrainMemory):
        """Store a completed reasoning cycle."""
        # Embed the cycle's summary text
        # Store with metadata: {cycle_id, timestamp, tickers, action_taken, regime}
    
    def recall(self, query: str, n: int = 5) -> list[BrainMemory]:
        """Semantic search — 'what have I seen similar to this?'"""
        # Embed query, search collection, return top-n BrainMemory objects
    
    def recall_ticker(self, ticker: str, n: int = 10) -> list[BrainMemory]:
        """Get all past memories involving a specific ticker."""
    
    def record_outcome(self, trade_id: str, outcome: dict):
        """
        After a trade closes, update the memory that proposed it.
        outcome = {ticker, side, entry_price, exit_price, pnl, pnl_pct, duration_days}
        This is how the brain learns — it can recall "I proposed this trade, here's what happened."
        """
    
    def stats(self) -> dict:
        return {"total_memories": self.collection.count(), "db_path": str(self.DB_PATH)}
```

`BrainMemory` dataclass:
```python
@dataclass
class BrainMemory:
    id: str
    timestamp: datetime
    cycle_summary: str       # what the brain observed and concluded
    tickers_considered: list
    regime: str
    action_taken: str        # "PROPOSED_TRADE" | "HELD" | "MONITORING"
    trade_proposed: dict     # the trade proposal if action_taken == "PROPOSED_TRADE"
    outcome: dict            # filled in later by record_outcome()
    tags: list               # ["high-vix", "earnings-season", "trend-reversal"] etc.
```

---

## MODULE 4 — REASONING LOOP (THE CORE)

### `src/brain/cognitive/reasoner.py`

This is the most important module. The brain thinks in explicit steps — not a single LLM call but a loop that builds understanding incrementally. Each step uses Ollama to produce a structured thought, then feeds that thought into the next step.

```python
OLLAMA_BASE = "http://localhost:11434"

class ReasoningLoop:
    """
    Multi-step chain-of-thought reasoning using a local LLM.
    
    Thinking steps per cycle:
    1. ORIENT    — "What is the overall market situation right now?"
    2. FOCUS     — "Which assets deserve attention and why?"
    3. RECALL    — "Have I seen this before? What happened?"
    4. ANALYSE   — "For each focused asset: what's the case for and against?"
    5. DECIDE    — "What should I do? Am I confident enough to propose a trade?"
    6. REFLECT   — "What did I miss? What could go wrong?"
    """
    
    def __init__(self, model: str = "qwen2.5-coder:7b"):
        self.model = model
    
    def run_cycle(
        self,
        perception: PerceptionSnapshot,
        memory:     LongTermMemory,
        wm:         WorkingMemory,
    ) -> ReasoningResult:
        """
        Runs the full 6-step thinking cycle.
        Returns ReasoningResult with the brain's conclusions and proposed actions.
        """
        # Step 1: ORIENT
        orientation = self._think(
            prompt=f"""You are ARIA, a trading intelligence brain.
CURRENT MARKET STATE:
{perception.macro}
SIGNAL SUMMARY: {len(perception.signals)} assets. 
Top alerts: {perception.alerts}
Regime: {perception.macro.regime}

Step 1 — ORIENTATION: In 3-4 sentences, describe the overall market character right now.
What is the dominant theme? What is the risk environment? Be specific with numbers.""",
            step="ORIENT"
        )
        wm.add_thought("ORIENT", orientation, self._extract_conclusion(orientation))
        wm.current_hypothesis = orientation
        
        # Step 2: FOCUS — decide which tickers to analyse deeply
        focus_prompt = f"""Given this market orientation:
{orientation}

Top bullish signals: {[s.ticker for s in perception.top_opportunities if s.composite_score > 0][:8]}
Top bearish signals: {[s.ticker for s in perception.top_opportunities if s.composite_score < 0][:8]}
High-priority alerts: {perception.alerts}

Step 2 — FOCUS: List exactly 3-5 tickers worth deep analysis this cycle. 
For each, one sentence why it's interesting. Format: TICKER: reason"""
        focus_response = self._think(focus_prompt, "FOCUS")
        focus_tickers = self._parse_tickers(focus_response)
        wm.focus_tickers = focus_tickers
        wm.add_thought("FOCUS", focus_response, f"Focusing on: {focus_tickers}")
        
        # Step 3: RECALL — search memory for similar situations
        recall_query = f"{perception.macro.regime} regime, VIX {perception.macro.vix}, tickers: {focus_tickers}"
        past_memories = memory.recall(recall_query, n=3)
        recall_context = self._format_memories(past_memories)
        wm.add_thought("RECALL", recall_context, f"Found {len(past_memories)} relevant past cycles")
        
        # Step 4: ANALYSE — deep dive on each focused ticker
        analyses = {}
        for ticker in focus_tickers[:4]:  # cap at 4 to control token usage
            sig = perception.signals.get(ticker)
            ml  = perception.ml.get(ticker)
            if not sig:
                continue
            analysis = self._think(f"""Analysing {ticker}:
Signal score: {sig.composite_score:+.1f} | Action: {sig.action} | Confidence: {sig.confidence}
Trend: {sig.trend_score:.0f} | Momentum: {sig.momentum_score:.0f} | Volatility: {sig.volatility_score:.0f}
ML: {ml.overall_signal if ml else 'N/A'} ({ml.overall_bullish:.0%} bullish if ml else '')
Drivers: {sig.drivers[:2]}
Risks: {sig.risks[:2]}
Stop: ${sig.stop_loss:.2f} | Target: ${sig.take_profit:.2f} | Current: ${sig.current_price:.2f}
Past context: {recall_context[:300]}

Step 4 — ANALYSE {ticker}: 
- Is the signal credible? Do technicals and ML agree?
- What's the key risk that could invalidate this?
- Is this a good setup relative to what I've seen before?
- Confidence: HIGH / MEDIUM / LOW. One sentence each.""", f"ANALYSE_{ticker}")
            analyses[ticker] = analysis
            wm.add_thought(f"ANALYSE_{ticker}", analysis, "")
        
        # Step 5: DECIDE — commit to action or hold
        decide_prompt = f"""Based on all analysis:
Orientation: {wm.current_hypothesis[:200]}
Analyses completed: {list(analyses.keys())}
Past memory context: {recall_context[:200]}

Step 5 — DECIDE: For each analysed ticker, state your decision:
Format strictly as:
TICKER | ACTION | CONVICTION(0-100) | REASON(one sentence)

ACTION must be one of: PROPOSE_BUY | PROPOSE_SELL | MONITOR | SKIP
Only propose if CONVICTION >= 65 AND you have a clear thesis.
If macro regime is bearish/crisis, require CONVICTION >= 80 for buys."""
        decision = self._think(decide_prompt, "DECIDE")
        trade_decisions = self._parse_decisions(decision)
        wm.add_thought("DECIDE", decision, f"Decisions: {trade_decisions}")
        
        # Step 6: REFLECT — sanity check
        reflect = self._think(f"""Decisions made: {decision}
Step 6 — REFLECT: In 2-3 sentences:
What is the biggest thing I might be wrong about?
Is there anything I haven't considered?
Am I being overconfident or underconfident?""", "REFLECT")
        wm.add_thought("REFLECT", reflect, "")
        
        return ReasoningResult(
            cycle_id=wm.cycle_id,
            thinking_steps=wm.reasoning_steps,
            focus_tickers=focus_tickers,
            trade_decisions=trade_decisions,
            orientation=orientation,
            reflection=reflect,
            full_monologue=wm.summarize(),
        )
    
    def _think(self, prompt: str, step: str) -> str:
        """Single Ollama call. Returns the model's response text."""
        import urllib.request, json
        payload = json.dumps({
            "model": self.model,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.4, "top_p": 0.9, "num_predict": 400}
        }).encode()
        req = urllib.request.Request(
            f"{OLLAMA_BASE}/api/chat", data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["message"]["content"]
    
    def _parse_tickers(self, text: str) -> list:
        """Extract ticker symbols from FOCUS step output."""
        # regex: 2-5 uppercase letters or uppercase+digits, possibly with -, =, ^
        import re
        return re.findall(r'\b([A-Z][A-Z0-9\-\=\^]{1,8})\b', text)[:5]
    
    def _parse_decisions(self, text: str) -> list[TradeDecision]:
        """Parse DECIDE step output into TradeDecision objects."""
        # Parse lines matching: TICKER | ACTION | CONVICTION | REASON
    
    def _format_memories(self, memories: list) -> str:
        if not memories:
            return "No relevant past experience found."
        return "\n".join(f"- [{m.timestamp.date()}] {m.cycle_summary[:100]}" for m in memories)
    
    def _extract_conclusion(self, text: str) -> str:
        return text.split('\n')[-1][:100]
```

`TradeDecision` dataclass:
```python
@dataclass
class TradeDecision:
    ticker: str
    action: str      # "PROPOSE_BUY" | "PROPOSE_SELL" | "MONITOR" | "SKIP"
    conviction: int  # 0-100
    reason: str
```

`ReasoningResult` dataclass:
```python
@dataclass
class ReasoningResult:
    cycle_id: str
    thinking_steps: list   # [{step, thought, conclusion}]
    focus_tickers: list
    trade_decisions: list[TradeDecision]
    orientation: str
    reflection: str
    full_monologue: str
```

---

## MODULE 5 — PLANNER

### `src/brain/cognitive/planner.py`

Converts `ReasoningResult` into concrete `OrderRequest` objects. Bridges the brain's language-level decisions into the execution system.

```python
class TradePlanner:
    def plan(
        self,
        decisions: list[TradeDecision],
        perception: PerceptionSnapshot,
        account_equity: float = 100_000
    ) -> list[PlannedTrade]:
        """
        For each PROPOSE_BUY / PROPOSE_SELL decision:
        - Validates the ticker is in signals.json
        - Calculates position size using Kelly-inspired formula
        - Sets stop_loss and take_profit from signal data
        - Returns PlannedTrade objects ready for the executor
        
        Kelly position sizing:
          edge     = bullish_prob - 0.5
          kelly    = edge / max(realised_vol, 0.01)
          fraction = min(kelly * 0.25, 0.05)   # quarter-Kelly, max 5%
          notional = account_equity * fraction
          qty      = notional / current_price
        
        Rejects trades if:
          - current_price is 0 or None
          - notional < $50 (too small to execute)
          - qty < 0.001 for crypto or < 1 for equities
          - ticker already has a pending trade in the approval queue
        """

@dataclass
class PlannedTrade:
    ticker: str
    side: str          # "buy" | "sell"
    qty: float
    asset_class: str   # "equity" | "crypto"
    stop_loss: float
    take_profit: float
    signal_score: float
    conviction: int
    thesis: str        # the brain's reasoning, for display in the UI
    broker: str        # "alpaca"
```

---

## MODULE 6 — EXECUTOR

### `src/brain/cognitive/executor.py`

Pushes planned trades into the existing approval queue. The user still approves every trade — the brain proposes, the human decides.

```python
class BrainExecutor:
    def execute_plan(self, planned_trades: list[PlannedTrade]) -> list[str]:
        """
        Pushes each PlannedTrade into the ApprovalQueue.
        Returns list of trade_ids that were queued.
        Skips any ticker that already has a pending trade.
        """
        from src.execution.approval_queue import ApprovalQueue
        from src.execution.broker_base import AssetClass, OrderRequest, OrderSide, OrderType
        queue = ApprovalQueue()
        existing = {t.ticker for t in queue.get_all(limit=500) if t.status == "pending"}
        queued = []
        for trade in planned_trades:
            if trade.ticker in existing:
                continue
            req = OrderRequest(
                ticker=trade.ticker,
                side=OrderSide(trade.side),
                qty=trade.qty,
                order_type=OrderType.MARKET,
                asset_class=AssetClass(trade.asset_class),
                stop_loss=trade.stop_loss,
                take_profit=trade.take_profit,
                signal_score=trade.signal_score,
                thesis_summary=trade.thesis,
            )
            result = queue.push(req, confidence=str(trade.conviction)+"%",
                                bull_case=trade.thesis, broker=trade.broker,
                                current_price=0)
            queued.append(result.id)
        return queued
```

---

## MODULE 7 — LEARNER

### `src/brain/cognitive/learner.py`

The brain learns from outcomes. After a trade is executed and later closed, the learner records the result back into long-term memory so future reasoning can reference it.

```python
class BrainLearner:
    def __init__(self, memory: LongTermMemory):
        self.memory = memory
    
    def check_outcomes(self):
        """
        Reads the approval queue for executed trades.
        For executed trades older than 1 day: attempts to fetch the current price
        via yfinance, calculate P&L, and call memory.record_outcome().
        Run this at the start of every brain cycle.
        """
    
    def generate_lesson(self, memory: BrainMemory) -> str:
        """
        Given a memory with an outcome, produce a one-sentence lesson:
        e.g. "In Goldilocks regime with VIX<16, buying AAPL on score>40 resulted in +3.2% in 5 days."
        These lessons are injected into future RECALL steps.
        """
```

---

## MODULE 8 — BRAIN DAEMON

### `src/brain/brain_daemon.py`

The daemon is the heartbeat — it runs continuously, orchestrating all modules in sequence every 15 minutes. It is started by the FastAPI server on startup and can be paused/resumed via API.

```python
from apscheduler.schedulers.background import BackgroundScheduler
# Install: venv\Scripts\pip.exe install apscheduler

import uuid
from pathlib import Path
import json
from datetime import datetime

ROOT = Path(__file__).parent.parent.parent

class BrainDaemon:
    """
    Orchestrates one full cognitive cycle every 15 minutes:
    LEARN → PERCEIVE → REASON → PLAN → EXECUTE → REMEMBER → SLEEP
    """
    
    STATE_FILE = ROOT / "data" / "brain_state.json"
    
    def __init__(self, model: str = "qwen2.5-coder:7b"):
        self.model     = model
        self.scheduler = BackgroundScheduler()
        self.memory    = LongTermMemory()
        self.running   = False
        self.last_cycle: dict = {}
        self.cycle_count = 0
        self._load_state()
    
    def start(self):
        self.running = True
        self.scheduler.add_job(self._run_cycle, 'interval', minutes=15,
                               id='brain_cycle', replace_existing=True)
        self.scheduler.start()
        # Run one cycle immediately on start
        self.scheduler.add_job(self._run_cycle, 'date')
    
    def stop(self):
        self.running = False
        self.scheduler.shutdown(wait=False)
    
    def set_interval(self, minutes: int):
        self.scheduler.reschedule_job('brain_cycle', trigger='interval', minutes=minutes)
    
    def _run_cycle(self):
        cycle_id = str(uuid.uuid4())[:8]
        wm = WorkingMemory(cycle_id=cycle_id, started_at=datetime.now(),
                           focus_tickers=[], current_hypothesis="",
                           reasoning_steps=[], candidate_trades=[],
                           context_summary="", warnings=[])
        try:
            # 1. LEARN — check if past trades have closed, record outcomes
            BrainLearner(self.memory).check_outcomes()
            
            # 2. PERCEIVE
            perception = MarketPerception().perceive()
            
            # 3. REASON
            reasoner = ReasoningLoop(model=self.model)
            result   = reasoner.run_cycle(perception, self.memory, wm)
            
            # 4. PLAN
            planner = TradePlanner()
            propose = [d for d in result.trade_decisions if d.action.startswith("PROPOSE")]
            planned = planner.plan(propose, perception)
            
            # 5. EXECUTE (push to approval queue)
            queued_ids = BrainExecutor().execute_plan(planned)
            
            # 6. REMEMBER — store this cycle in long-term memory
            brain_mem = BrainMemory(
                id=cycle_id,
                timestamp=datetime.now(),
                cycle_summary=result.full_monologue[:500],
                tickers_considered=result.focus_tickers,
                regime=perception.macro.regime,
                action_taken="PROPOSED_TRADE" if queued_ids else "MONITORING",
                trade_proposed={"ids": queued_ids},
                outcome={},
                tags=self._extract_tags(perception, result),
            )
            self.memory.remember(brain_mem)
            
            # 7. Save state for API to read
            self.last_cycle = {
                "cycle_id":       cycle_id,
                "timestamp":      datetime.now().isoformat(),
                "cycle_count":    self.cycle_count,
                "regime":         perception.macro.regime,
                "focus_tickers":  result.focus_tickers,
                "thinking_steps": result.thinking_steps,
                "decisions":      [d.__dict__ for d in result.trade_decisions],
                "trades_queued":  len(queued_ids),
                "orientation":    result.orientation,
                "reflection":     result.reflection,
                "memory_count":   self.memory.stats()["total_memories"],
            }
            self.cycle_count += 1
            self._save_state()
            
        except Exception as e:
            self.last_cycle = {"error": str(e), "cycle_id": cycle_id,
                               "timestamp": datetime.now().isoformat()}
            self._save_state()
    
    def _extract_tags(self, perception, result) -> list:
        tags = [perception.macro.regime]
        if perception.macro.vix and float(perception.macro.vix) > 25:
            tags.append("high-vix")
        if perception.macro.vix and float(perception.macro.vix) < 15:
            tags.append("low-vix")
        return tags
    
    def _save_state(self):
        self.STATE_FILE.parent.mkdir(exist_ok=True)
        self.STATE_FILE.write_text(json.dumps(self.last_cycle, indent=2, default=str))
    
    def _load_state(self):
        if self.STATE_FILE.exists():
            self.last_cycle = json.loads(self.STATE_FILE.read_text())
            self.cycle_count = self.last_cycle.get("cycle_count", 0)


# Global daemon instance (singleton)
_brain: BrainDaemon | None = None

def get_brain(model: str = "qwen2.5-coder:7b") -> BrainDaemon:
    global _brain
    if _brain is None:
        _brain = BrainDaemon(model=model)
    return _brain
```

---

## MODULE 9 — BACKEND ENDPOINTS

Add all of these to `backend/main.py`. Import from brain modules at the top of each function (lazy import pattern — copy what's already done for the execution engine).

```python
# Brain startup — add inside the existing @app.on_event("startup")
# After the order manager line, add:
#   import threading
#   threading.Thread(target=lambda: _start_brain_if_ollama(), daemon=True).start()

def _start_brain_if_ollama():
    """Start the brain daemon only if Ollama is available."""
    import time
    time.sleep(5)  # wait for server to be fully up
    if _ollama_available():
        from src.brain.brain_daemon import get_brain
        get_brain().start()

# ── Brain endpoints ──────────────────────────────────────────────────────────

@app.get("/api/brain/status", tags=["Brain"])
def brain_status():
    """Full status: Ollama, models, daemon state, memory stats."""

@app.get("/api/brain/last-cycle", tags=["Brain"])
def brain_last_cycle():
    """Returns the full thinking log from the most recent cycle."""
    # Read data/brain_state.json

@app.post("/api/brain/run-now", tags=["Brain"])
def brain_run_now(background_tasks: BackgroundTasks):
    """Trigger an immediate reasoning cycle (doesn't wait for scheduler)."""

@app.post("/api/brain/start", tags=["Brain"])
def brain_start(model: str = "qwen2.5-coder:7b"):
    """Start the brain daemon."""

@app.post("/api/brain/stop", tags=["Brain"])
def brain_stop():
    """Pause the brain daemon."""

@app.patch("/api/brain/interval", tags=["Brain"])
def brain_set_interval(minutes: int = 15):
    """Change the cycle interval."""

@app.get("/api/brain/memories", tags=["Brain"])
def brain_memories(n: int = 20):
    """Recent memories from long-term store."""

@app.get("/api/brain/recall", tags=["Brain"])
def brain_recall(q: str):
    """Semantic search over brain memories."""

@app.post("/api/chat/local", tags=["Brain"])
def chat_local(body: LocalChatRequest):
    """Chat with the local model directly (outside of the autonomous cycle)."""
    # Uses same Ollama call pattern but as a direct request-response, not autonomous

@app.post("/api/brain/pull-model", tags=["Brain"])
def pull_model(model: str = "llama3.1:8b"):
    """Pull a model via Ollama."""

@app.post("/api/brain/generate-training-data", tags=["Brain"])
def generate_training_data(background_tasks: BackgroundTasks):
    """Generate fine-tuning training data from signal history."""
```

---

## MODULE 10 — FRONTEND: BRAIN PAGE

### `frontend/src/pages/Brain.jsx`

Bloomberg terminal aesthetic. Dark background, `var(--mono)` font, `var(--orange)` accents, `var(--green)` for live/active states. No external CSS libraries. Use only inline styles.

**Layout (top to bottom):**

### Section 1 — Vital Signs Bar
```
◈ ARIA COGNITIVE BRAIN                    ● THINKING  |  Cycle #47  |  Last: 14 min ago
Regime: Goldilocks Expansion  |  VIX: 15.3  |  Memories: 142  |  Model: qwen2.5-coder:7b
```
Poll `/api/brain/status` and `/api/brain/last-cycle` every 10 seconds.
If brain is not running: show `○ OFFLINE` in red with a START button.

### Section 2 — Live Thought Stream
When a cycle is running (or just completed), show the reasoning steps as a terminal-style log:

```
[ORIENT]   ─────────────────────────────────────────────────
  The market is in a Goldilocks expansion regime. VIX at 15.3 signals low fear...
  → Conclusion: Risk-on environment. Equities and growth assets favoured.

[FOCUS]    ─────────────────────────────────────────────────
  Focusing on: AAPL, ETH-USD, NVDA
  AAPL: Score +40.9, ML bullish 68%, strong technical trend
  ETH-USD: Score -41.5, bearish divergence forming
  → Focusing on 3 tickers

[ANALYSE AAPL] ──────────────────────────────────────────────
  Signal credible — technicals and ML agree. Key risk: macro reversal.
  → MEDIUM conviction setup

[DECIDE]   ─────────────────────────────────────────────────
  AAPL | PROPOSE_BUY | 72 | Strong technical + ML alignment in Goldilocks regime
  ETH-USD | PROPOSE_SELL | 68 | Bearish divergence confirmed by ML
  → 2 trades proposed

[REFLECT]  ─────────────────────────────────────────────────
  Biggest risk: earnings surprise could invalidate technical picture.
  → Proceeding with proposed trades
```

Each step has a collapsible card. The step header line is always visible, full content expands on click.

### Section 3 — Control Panel
```
[▶ RUN NOW]  [⏸ PAUSE]  Interval: [15] min  Model: [qwen2.5-coder:7b ▾]
```
Buttons call the respective API endpoints.

### Section 4 — Memory Browser
A searchable list of past memories. Input calls `/api/brain/recall?q=...`. Each memory shown as:
```
[2026-07-03 14:22]  Goldilocks | AAPL, NVDA  |  PROPOSED_TRADE  |  ✓ +2.3%
[2026-07-03 09:10]  Expansion  | ETH-USD     |  MONITORING
```

### Section 5 — Fine-tune Panel
A collapsible panel with install instructions as a copy-paste code block, a "GENERATE TRAINING DATA" button, and sample count.

---

## MODULE 11 — CHAT PAGE UPDATE

### `frontend/src/pages/Chat.jsx` — add:

A mode toggle at the top of the input area:
```jsx
<div style={{display:'flex', gap:8, marginBottom:8}}>
  <button onClick={() => setMode('cloud')}
    style={{background: mode==='cloud' ? 'var(--orange)' : 'transparent', ...}}>
    ☁ CLOUD — Claude API
  </button>
  <button onClick={() => setMode('local')}
    style={{background: mode==='local' ? '#00aa44' : 'transparent', ...}}>
    ⚡ LOCAL — {localModel || 'no model'}
  </button>
</div>
```

- Cloud mode: POST `/api/chat`
- Local mode: POST `/api/chat/local` with `{messages, model: localModel}`
- If local and Ollama offline: show orange banner
- Each bubble footer shows `[CLOUD]` or `[LOCAL · modelname]`
- `localModel` state comes from `GET /api/brain/status` on mount

---

## INSTALL REQUIREMENTS

Add to `venv\Scripts\pip.exe install`:
```
chromadb
sentence-transformers
apscheduler
```

These are the only new packages needed for Phases 1–8. Fine-tuning (Unsloth) is separate and optional — document it but don't block the brain on it.

---

## CONSTRAINTS

1. **Windows paths** — `pathlib.Path` everywhere, never raw strings with `/`
2. **No mock data** — all endpoints read actual files from `data/`; return `{}` if missing
3. **Ollama HTTP** — use `urllib.request` only, no `httpx`/`requests` for Ollama calls
4. **Lazy imports** — import brain modules inside functions, not at the top of main.py
5. **Daemon is optional** — if Ollama is offline, brain endpoints return status "offline" gracefully, nothing crashes
6. **No new npm packages** — React pages use only what's in the existing package.json
7. **Existing endpoints untouched** — only add to main.py, never remove or rename
8. **`data/brain_memory/`** — create this directory in code if it doesn't exist; never assume it's there
9. **Cycle interval** — default 15 minutes, configurable via API
10. **The brain proposes, human approves** — executor only pushes to approval queue; it never calls `approve_and_execute`

---

## DELIVERABLES

```
src/brain/cognitive/perception.py       PerceptionSnapshot, MarketPerception
src/brain/cognitive/working_memory.py   WorkingMemory
src/brain/cognitive/long_term_memory.py LongTermMemory, BrainMemory (ChromaDB)
src/brain/cognitive/reasoner.py         ReasoningLoop, ReasoningResult, TradeDecision
src/brain/cognitive/planner.py          TradePlanner, PlannedTrade
src/brain/cognitive/executor.py         BrainExecutor
src/brain/cognitive/learner.py          BrainLearner
src/brain/brain_daemon.py               BrainDaemon, get_brain()
backend/main.py                         10 new /api/brain/* endpoints + /api/chat/local
frontend/src/pages/Brain.jsx            Full brain UI page
frontend/src/pages/Chat.jsx             Cloud/local toggle added
frontend/src/App.jsx                    /brain route
frontend/src/components/Sidebar.jsx     AI BRAIN nav item
data/brain_training_data.jsonl          (generated, not hand-written)
```

Build every file completely. Start with `src/brain/cognitive/`, then `brain_daemon.py`, then `backend/main.py`, then frontend. Test every import chain mentally before finalising.
