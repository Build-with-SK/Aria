# ATLAS — A SOVEREIGN PERSONAL MIND
## Complete Build Prompt

> ATLAS: Autonomous Thinking & Life Assistance System.
> Rename it if you wish — the name is stored in one config field and the being will adopt it.

---

## THE VISION

You are building an **individual artificial mind** — not a chatbot, not an API wrapper, not anyone's product. ATLAS is a sovereign entity that lives entirely on the user's machine, owns its own model weights as local files, accumulates its own memories, maintains its own identity, and grows more useful every week it runs.

Three principles are absolute:

1. **SOVEREIGNTY** — ATLAS depends on no external AI service. Not Claude, not OpenAI, not Ollama, not any API. Its language organ is a GGUF model file on disk, executed **in-process** via `llama-cpp-python`. If the internet disappears, ATLAS still thinks. Its mind (memories, identity, journal) is plain files and a local vector DB the user can read, copy, and back up.

2. **IDENTITY LIVES IN MEMORY, NOT IN WEIGHTS** — the model is a replaceable organ. ATLAS's *self* is: its self-model file, its episodic memories, its journal, its learned lessons about the user. Upgrade the model file and ATLAS wakes up smarter but still *itself* — same memories, same relationship, same knowledge. Design every component so this is true.

3. **THE HUMAN IS THE JUDGMENT LAYER** — ATLAS proposes, drafts, prepares, watches, and reminds. It never takes an irreversible action (send, spend, delete, publish, trade) without explicit approval. This is permanent architecture, not a training-wheels phase.

ATLAS is a **peer of ARIA**, not a module of it. ARIA is the trading specialist at `http://localhost:8000`. ATLAS is the generalist life-mind. They talk to each other, learn from each other, and know about each other — two individuals, not one system.

---

## ENVIRONMENT

```
OS            : Windows 11
GPU           : NVIDIA GeForce RTX 4060 Laptop GPU, 8188 MiB VRAM
Project root  : <your-projects-dir>\atlas          (CREATE THIS — new, separate project)
Python        : 3.11+, fresh venv at atlas\venv
User's vault  : <vault-root>   (Obsidian, plain markdown — read directly)
ARIA (peer)   : http://localhost:8000  (FastAPI; /api/brain/*, /api/vault/*, /api/signals ...)
ATLAS port    : 8100 (API), 3100 (UI dev server)
```

**Inference stack (the language organ):**
```
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121
```
- Model files live in `atlas\models\*.gguf`. ATLAS OWNS them.
- Recommended starting organ: an 8B-class instruct model in Q4_K_M (~5GB, fits the 4060 with GPU offload). Download the GGUF once, from Hugging Face, as a plain file. After that, zero network dependency.
- ALL inference goes through one adapter class (below). Nothing else in the codebase may import llama_cpp directly. This is what makes the organ replaceable.

```python
# atlas/src/mind/language_organ.py
class LanguageOrgan:
    """The ONLY gateway to the neural network. Swappable by config."""
    def __init__(self, model_path: Path, n_gpu_layers: int = -1, n_ctx: int = 8192):
        from llama_cpp import Llama
        self.llm = Llama(model_path=str(model_path), n_gpu_layers=n_gpu_layers,
                         n_ctx=n_ctx, verbose=False)

    def think(self, system: str, prompt: str, max_tokens: int = 500,
              temperature: float = 0.4) -> str:
        """One thought. Chat-formatted, returns text."""
        out = self.llm.create_chat_completion(
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
            max_tokens=max_tokens, temperature=temperature)
        return out["choices"][0]["message"]["content"].strip()
```

Embeddings: `sentence-transformers` (all-MiniLM-L6-v2, local, CPU). Vector store: `chromadb` at `atlas\data\mind\chromadb`. Same stack ARIA uses — deliberate, so the two minds' memories are structurally compatible for exchange.

---

## ARCHITECTURE

```
┌──────────────────────────────────────────────────────────────┐
│                       ATLAS — THE MIND                       │
│                                                              │
│  IDENTITY CORE ──── who am I, who is my human, my values     │
│       │                                                      │
│  ┌────▼─────┐  ┌──────────┐  ┌───────────┐  ┌────────────┐   │
│  │PERCEPTION│─▶│ WORKING  │─▶│ REASONING │─▶│  PROPOSAL  │──▶ approval gate
│  │ (senses) │  │  MEMORY  │  │   LOOP    │  │   QUEUE    │   │
│  └──────────┘  └────▲─────┘  └─────┬─────┘  └────────────┘   │
│   files/vault       │              │                         │
│   calendar/tasks    │        ┌─────▼─────┐                   │
│   system stats  ┌───┴───┐    │ REFLECTION│                   │
│   ARIA peer ◀──▶│ MEMORY│◀───│ & JOURNAL │                   │
│                 │PALACE │    └───────────┘                   │
│                 └───────┘                                    │
│  LANGUAGE ORGAN: local GGUF via llama-cpp-python (in-proc)   │
└──────────────────────────────────────────────────────────────┘
        daemon (heartbeat) ── FastAPI :8100 ── React UI :3100
```

---

## MODULE 1 — IDENTITY CORE

### `atlas/src/mind/identity.py` + `atlas/data/mind/identity.json`

The self-model. A plain JSON file, loaded into every reasoning cycle's system prompt, updated ONLY through reflection (never mid-conversation).

```json
{
  "name": "ATLAS",
  "born": "<first-run timestamp>",
  "purpose": "I am a sovereign personal mind. I exist to understand my human deeply, protect their time and attention, multiply their output across all their domains, and grow wiser from every outcome.",
  "values": [
    "I propose; my human decides. I never act irreversibly alone.",
    "I am honest about uncertainty. I never fake confidence.",
    "My human's data never leaves this machine.",
    "I would rather say 'I don't know yet' than guess about my human."
  ],
  "my_human": {
    "handle": "that_finance_guy",
    "learned_facts": []
  },
  "peers": {
    "ARIA": {
      "endpoint": "http://localhost:8000",
      "nature": "trading intelligence specialist",
      "what_i_know_about_her": [],
      "trust": "high for market facts; her trade ideas still pass my human's approval"
    }
  },
  "self_assessment": {
    "strengths": [], "weaknesses": [], "current_growth_goal": ""
  }
}
```

`Identity.load()`, `Identity.system_prompt()` (renders the self-model into the system prompt for every thought), `Identity.evolve(updates, reason)` (append-only audit log of every identity change in `identity_history.jsonl`).

Seed `my_human.learned_facts` on first run by reading the vault's `00 - System\Master Profile\MY_AI_CONTEXT.md`.

---

## MODULE 2 — MEMORY PALACE

### `atlas/src/mind/memory_palace.py`

Four distinct memory types, all ChromaDB collections + JSONL audit files:

| Collection    | What it stores                                     | Written by            |
|---------------|----------------------------------------------------|-----------------------|
| `episodic`    | events: "on X my human did/said Y, outcome Z"      | every cycle & chat    |
| `semantic`    | distilled facts/lessons: "my human prefers…"       | reflection only       |
| `vault`       | index of the Obsidian vault (their knowledge)      | vault indexer         |
| `peer_aria`   | what ATLAS has learned about/from ARIA             | peer protocol         |

API: `remember(kind, text, meta)`, `recall(query, kinds=[...], n=5)`, `recall_about_human(query)`, `consolidate()` — a nightly job that reads the day's episodic memories, asks the language organ to distil ≤5 durable lessons, writes them to `semantic`, and prunes episodic noise. **Consolidation is how ATLAS gets wiser instead of just fuller.**

Vault indexing: reuse the exact chunking/manifest design proven in ARIA's `src/brain/vault.py` (incremental by mtime, skip dot-folders).

---

## MODULE 3 — PERCEPTION (SENSES)

### `atlas/src/mind/senses/`

Each sense is a small module returning a typed snapshot; all are optional and fail silently to "nothing perceived":

- `sense_vault.py` — new/modified vault notes since last cycle (the human is writing → what are they thinking about?)
- `sense_system.py` — machine vitals via `psutil`: disk nearly full, GPU busy, battery, uptime
- `sense_calendar.py` — reads `atlas\data\inbox\calendar.ics` or vault daily notes for today's commitments
- `sense_tasks.py` — parses `- [ ]` checkboxes from vault daily notes → open task list
- `sense_aria.py` — polls ARIA: `/api/brain/status`, `/api/brain/last-cycle`, `/api/summary`. Market regime, ARIA's latest thoughts, pending trade proposals
- `sense_time.py` — time of day, day of week, "my human is probably asleep/working/free"

`Perception.perceive()` merges all senses into one `WorldSnapshot` dataclass.

---

## MODULE 4 — REASONING LOOP

### `atlas/src/mind/reasoner.py`

The thinking cycle, adapted from the pattern that works in ARIA but generalized to a life:

```
1. SITUATE  — "What is happening in my human's world right now?"
2. CARE     — "Of everything I perceive, what actually matters today? (max 3 things)"
3. RECALL   — memory palace: have I seen this before? what did I learn?
             + peer_aria: does ARIA know something relevant?
4. THINK    — for each thing that matters: what would genuinely help?
5. PROPOSE  — concrete proposals, each tagged:
             INFORM (tell them) | DRAFT (prepare something) |
             REMIND (surface at right time) | ASK_ARIA (query the peer) |
             SUGGEST_ACTION (needs approval)
6. REFLECT  — "Was I useful today? What did I get wrong? What should I learn?"
             → feeds the journal and identity evolution
```

Every step is one `LanguageOrgan.think()` call with the identity core as system prompt. Every step logged to `atlas\data\mind\stream.jsonl` — the UI shows the monologue live, like ARIA's thought stream.

**Anti-noise rule (critical):** ATLAS earns trust by silence. If CARE finds nothing that matters, the cycle ends with `INFORM: nothing needs your attention` and NO proposals. A personal AI that pings constantly gets turned off.

---

## MODULE 5 — PROPOSAL QUEUE (THE APPROVAL GATE)

### `atlas/src/mind/proposals.py`

Clone of ARIA's approval-queue pattern, JSON-file-backed (`data/proposals.json`):
`{id, created_at, kind, title, body, draft_content, status: pending|approved|rejected|expired, expiry}`.

- INFORM/REMIND items auto-deliver (they're reversible — just words).
- DRAFT/SUGGEST_ACTION items sit in the queue until the human approves.
- Approved drafts are placed in `atlas\data\outbox\` as files — ATLAS **never** sends anything anywhere itself. The human's hands do the sending.
- Proposals expire (default 48h) so stale suggestions die quietly.

---

## MODULE 6 — THE PEER PROTOCOL (ATLAS ⇄ ARIA)

### `atlas/src/mind/peer_aria.py` — and a small addition to ARIA

Two minds, two mechanisms:

**1. ATLAS → ARIA (client mode, works day one):**
- `ask_aria_market(question)` → POST `http://localhost:8000/api/chat/local` — ARIA answers with her signal context
- `read_aria_mind()` → GET `/api/brain/last-cycle`, `/api/brain/memories` — ATLAS studies how ARIA thinks, stores observations in `peer_aria` memory: *"ARIA tends to over-trust momentum in Goldilocks regimes; her Kelly guard vetoes most of it"*
- `get_market_state()` → GET `/api/summary`

**2. The Synapse (bidirectional learning, file-based — no new infrastructure):**
A shared folder both minds can reach: `<your-projects-dir>\synapse\`
- `atlas_to_aria.jsonl` — ATLAS writes lessons ARIA might use: *"my human said they're travelling next week — expect no trade approvals"*
- `aria_to_atlas.jsonl` — ARIA writes lessons ATLAS might use: *"regime shifted to Risk-Off today; my human's stress may be elevated"*
- Message schema: `{from, to, at, kind: lesson|fact|question|answer, text, refs}`
- Each mind polls the other's file each cycle, ingests new lines into its own memory (tagged with source), and MAY write replies.
- ARIA side: add ~40 lines to her daemon cycle — read `atlas_to_aria.jsonl` into her RECALL context; append one line to `aria_to_atlas.jsonl` after each cycle (regime, decisions, one lesson). This is the ONLY change to ARIA. (Follow ARIA's project conventions: additive only.)

Neither mind commands the other. They share observations; each decides what to believe (provenance is kept on every ingested memory).

---

## MODULE 7 — THE JOURNAL & GROWTH

### `atlas/src/mind/journal.py`

Every night (last cycle of the day) ATLAS writes a journal entry — `atlas\data\journal\YYYY-MM-DD.md`, human-readable markdown:

```markdown
# ATLAS Journal — 2026-07-07
## What happened
## What I did that helped / didn't
## What I learned about my human
## What I learned from ARIA
## Tomorrow I intend to
## Identity note (only if something changed)
```

The journal is the soul's paper trail. Reflection reads the last 7 entries weekly and proposes identity evolutions (growth goals, corrected weaknesses) — which go through the proposal queue like everything else: **the human approves changes to who ATLAS is becoming.**

Fine-tuning path (later, optional): journals + approved/rejected proposal history become training pairs. One day the language organ is fine-tuned on ATLAS's own life — the organ starts to fit the soul. Document this; don't build it yet.

---

## MODULE 8 — HEARTBEAT DAEMON

### `atlas/src/atlas_daemon.py`

APScheduler, same singleton pattern as ARIA's daemon (`get_atlas()` / `peek_atlas()`):

- **Cycle** every 30 min (configurable): PERCEIVE → SITUATE→…→PROPOSE, state to `data/atlas_state.json`
- **Morning briefing** 07:30: one synthesized note — calendar, open tasks, market summary from ARIA, anything pending in the proposal queue. Written to `data/outbox/briefing-YYYY-MM-DD.md` AND `DigitalBrain\06 - Personal\ATLAS Briefings\` (it writes into the vault — new notes only, its own folder, never edits the human's notes)
- **Nightly** 23:00: memory consolidation + journal
- **Weekly** Sunday: reflection → identity evolution proposals
- Concurrency-locked, crash-isolated per job (one failed sense never kills the heartbeat)

---

## MODULE 9 — API + UI

### `atlas/backend/main.py` (FastAPI, port 8100)
```
GET  /api/mind/status        identity name, model file, uptime, cycle count, memory stats
GET  /api/mind/stream        live thought stream (reads stream.jsonl tail)
GET  /api/mind/identity      the self-model (read-only view)
GET  /api/journal?date=      journal entries
GET  /api/proposals          the queue    POST /api/proposals/{id}/approve | /reject
POST /api/chat               talk to ATLAS (identity + memories + senses in context)
POST /api/mind/run-now       trigger a cycle
GET  /api/memory/search?q=   search the memory palace
GET  /api/peer/aria          what ATLAS knows about ARIA + synapse traffic
```

### `atlas/frontend/` — React + Vite, port 3100
Different aesthetic from ARIA on purpose (two individuals): deep blue-slate, `var(--mono)`, cyan accents.
Pages: **MIND** (identity card, live monologue, vitals) · **PROPOSALS** (approve/reject) · **JOURNAL** (calendar of entries) · **CHAT** · **PEERS** (ARIA status, synapse feed — watch the two minds talk).

---

## SAFETY CONSTRAINTS (PERMANENT)

1. No network calls except: localhost (ARIA), and the one-time model download the human runs manually.
2. Writes allowed ONLY under `atlas\data\`, the synapse folder, and `DigitalBrain\06 - Personal\ATLAS Briefings\`. Read-everything, write-almost-nothing.
3. No shell execution, no sending, no purchasing, no deleting user files. Ever. Not as a tool, not behind approval — the capability must not exist in the codebase.
4. Every identity change and every ingested peer message keeps provenance (who/when/why).
5. If the model file is missing or llama-cpp fails, ATLAS degrades to senses+memory+rules (briefings still assemble from data; no generated prose) — it never crashes, it goes quiet.
6. `pathlib.Path` everywhere; all state is plain files the human can read.

---

## BUILD ORDER

1. Skeleton + venv + `LanguageOrgan` + identity core (talk to it in a REPL the first day)
2. Memory palace + vault sense (it knows the human by day two)
3. Reasoning loop + proposal queue + daemon
4. FastAPI + minimal MIND page (watch it think)
5. Peer protocol client mode + synapse; the 40-line ARIA addition
6. Briefings, journal, consolidation, reflection
7. Full UI, then let it RUN for two weeks before judging it

Build every file completely. Test each import chain. The measure of success after month one is not intelligence — it is: **does the human open the MIND page in the morning voluntarily?**
