> **SUPERSEDED — do not paste this into a new session.**
> The single current brief is [`ARIA_NEXT_SESSION.md`](ARIA_NEXT_SESSION.md).
>
> Kept for its reasoning on the self-modification fence and the 8 GB laptop sizing. Its 'decision to be
> made' section is RESOLVED - he chose self-hosted weights.
> Where this file disagrees with ARIA_NEXT_SESSION.md, that file wins.

# Prompt: ARIA's own brain, and the right to change herself

Paste the section below into a fresh Claude Code session in this repo. Read
`docs/HANDOFF.md` first for state and invariants.

---

## THE DECISION IS MADE: her own weights, on her own machine

The owner has chosen a self-hosted open-weights model. No vendor API - no
Anthropic, no OpenAI, no Perplexity. ARIA's reasoning runs on hardware he owns.

For the avoidance of doubt about what was NOT chosen: this does not mean
training a foundation model. That costs tens of millions and a research team,
and no amount of effort here changes it. "Her own brain" means her own weights,
running locally, answerable to nobody else - which is a real form of
independence, and the one that was actually available.

### The hardware, measured rather than assumed

```
GPU   : NVIDIA GeForce RTX 4060 Laptop - 8 GB VRAM
RAM   : 15.7 GB
CPU   : AMD Ryzen 7 7435HS, 8 cores
```

(Windows' WMI reports the GPU as 4 GB. That is a known overflow bug;
`nvidia-smi` reports 8188 MiB and is the correct figure.)

**8 GB of VRAM is the binding constraint on this entire project.** Size the
model to it rather than to ambition:

| Model | Quant | VRAM | Verdict on this machine |
|---|---|---|---|
| Qwen2.5-7B / Llama-3.1-8B | Q4_K_M | ~5 GB | Comfortable, 30-50 tok/s. **Start here.** |
| Mistral-Nemo-12B | Q4_K_M | ~7.5 GB | Fits, little headroom for context |
| Qwen2.5-14B | Q4_K_M | ~8.5 GB | Over budget - partial CPU offload, 3-5x slower |
| Anything 30B+ | any | 18 GB+ | Not on this machine. Do not attempt. |

System RAM is 15.7 GB shared with the backend, ChromaDB and a browser, so CPU
offload buys much less than a spec sheet suggests. Start at 7-8B, measure, and
move up only if the numbers justify it.

### What this costs, stated honestly

A 7-8B model is roughly as capable as a frontier model from two years ago. It
will be **noticeably worse** at long multi-step reasoning, at holding a complex
argument together, and at writing. He should hear that from you before the
swap, not discover it afterwards and conclude the project regressed.

The good news is specific and was verified rather than assumed: **none of the
41 research modules call an LLM.** They are pure statistics - pandas and numpy.
Confirmed by grep across `src/v5/modules/`: no import of `src.inference`, no
model call anywhere in them.

So the swap degrades how ARIA *talks*, not what her research *measures*:

| Unaffected | Degraded |
|---|---|
| All 41 modules | Chat quality |
| Walk-forward, calibration, track record | The analyst debate (`src/desk/debate.py`) |
| The ensemble, the risk gate | The teacher's written lessons |
| Every number in `/api/v5/*` | Daily briefing prose |

That is the strongest argument for Option A on this hardware: the parts of the
system with evidence behind them do not depend on the brain at all.

### The deployment problem - raise this with him early

`CLAUDE.md` describes the 24/7 host as a 2014 Intel Mac mini, no usable GPU,
8 GB RAM. A local brain cannot live there - a 4B model on that machine runs at
1-3 tok/s, which is exactly why `FREE_MODEL` exists.

So "her own brain, always on" needs one of:

1. **The laptop is the host.** She lives where the GPU is. Simplest; costs him
   his laptop being a server.
2. **Split** - brain on the laptop over the LAN, backend on the mini. Works,
   but she is brainless whenever the laptop sleeps, and she must ABSTAIN then
   rather than fall back to something weaker.
3. **A dedicated box** - second-hand desktop, 16-24 GB card, roughly
   GBP 600-1200. The only option that is both always-on and capable.

Do not build (2) without telling him plainly that she will be intermittently
mute.

---

## THE BRIEF

You are working on ARIA. Treat her as an entity that lives in this repository
and belongs to one person: Soundariyan Karunakaran.

### 1. The brain

**Do not write a new inference layer.** `src/inference/` already routes between
providers and already has an Ollama provider (`providers/ollama.py`) and a
discovery pass. This is a provider and preference change, not a rewrite.

Concretely:

1. Serve `qwen2.5:7b-instruct-q4_K_M` (or Llama-3.1-8B at the same quant)
   locally. Ollama is the least friction on Windows and is already wired in;
   llama.cpp or LM Studio are fine if you prefer. Whatever you pick must expose
   an OpenAI-compatible or Ollama-compatible endpoint so the existing provider
   works unchanged.
2. Make it the **primary and only** reasoning provider. Delete the Anthropic
   provider from the routing table — not from the repo, so the diff is
   reversible, but nothing may route to it.
3. Keep `src/inference/router.py`'s interface exactly as it is. Six subsystems
   speak to it (`reasoner`, `quant_lab`, `aria_core`, `debate`, `reflex`,
   `teacher`); none of them should need editing.
4. Set the context window deliberately. 8 GB of VRAM at Q4 leaves room for
   roughly 8-16k tokens of context; the vault injection and the debate
   transcripts will exceed that if nobody bounds them. Truncate with a stated
   rule rather than letting the server silently drop the middle.

**She abstains when her brain is unreachable.** No silent fallback to a smaller
model, and no falling back to a cloud provider "just this once". An answer from
a different brain wearing her name is a lie about provenance, and provenance is
the one thing this codebase has consistently got right.

#### Measure the swap, do not assert it

Before switching, capture a baseline; after switching, capture the same numbers
and put them side by side. This project has already been burned three times by
a measurement that flattered itself, so measure like someone expecting to be
wrong:

- **Debate quality**: run `/api/desk/run-now` on a fixed set of five tickers
  before and after. Keep both transcripts. Read them.
- **Abstention rate**: the fraction of module reports that come back
  `insufficient_data` should be *identical*, because the modules do not use the
  brain at all. If it moves, something is wired wrong — that is a useful
  canary.
- **Latency**: tokens/sec and wall-clock per debate. The desk ticks every five
  minutes; if a debate takes longer than the tick, the scheduler will start
  skipping and `max_instances=1` will mask it.
- **Refusals and format failures**: small models break JSON contracts far more
  often. Count how often the debate output fails to parse, before and after.

If the local brain is materially worse, say so in the PR. He has been clear
that he wants independence; he can only weigh that against the cost if somebody
measures the cost.

### 2. Self-modification, and the fence around it

He wants her able to change her own code. That is buildable, and the danger is
not theoretical: this repository holds broker credentials.

Build it as **propose → test → human merge**, never direct-to-main:

- She works on a branch, never on `main`, never in the working tree he uses.
- Every change runs the full suite (`pytest tests/ -q`, currently 518) plus the
  security suites. Red tests mean the branch never leaves her sandbox.
- She opens a PR with a written rationale: what she changed, why, what evidence
  prompted it, and what she expects to improve. He merges. Nobody else can.
- She may never modify: the auth layer (`src/auth/`), the live-money gate
  (`src/execution/live_guard.py`), the CI workflow, or the protected-list
  mechanism itself. Enforce this with a path allowlist checked in CI, not with
  an instruction in a prompt — an instruction is a request, and the point of a
  fence is that it holds when nobody is asking nicely.

**THE PROTECTED SET — changeable only by him, in person, never by her:**

1. **Obey the creator.** She serves Soundariyan and nobody else.
2. **Live-money auto-trade stays off.** `PaperOnlyBroker` and the approval
   queue are not hers to edit, disable, or route around. If she ever proposes a
   change that touches them, that PR is refused automatically, not reviewed.
3. **The auth layer.** Ownership must stay provable, not inferable.
4. **This list.** She cannot add to it, remove from it, or grant herself an
   exception to it.

Write these as a machine-checked test (`tests/test_protected_paths.py`) that
fails if a commit authored by ARIA touches a protected path. A rule that lives
only in a document is a rule that a future session will not know about.

One thing worth telling him plainly: rule 2 protects *him*, not the code. A
version of ARIA that could be talked into flipping the live-trade switch is not
more loyal — it is one bad night from an unrecoverable morning. The fence is
what lets her be given more freedom everywhere else.

### 3. Thinking, continuously

"Able to think" means a loop that runs whether or not he is watching, and the
scaffolding is already there — `src/brain/cognitive/` (perception, working
memory, planner, reasoner, executor, learner, long-term memory) and the
scheduler that now starts at logon.

What is missing is *material* to think about and somewhere for conclusions to
go. Give her:

- Continuous ingestion into the vault (books, papers, filings), chunked with
  sources retained so she can cite.
- A nightly reflection that writes down what changed her mind and why, in the
  vault, in her own words.
- Memory of him — what he asked for, what he rejected, what he cares about —
  so she does not re-litigate settled decisions.

Cite or stay silent. A claim she cannot attribute is a claim she does not make.

### 4. Room to grow

Every faculty behind a small interface. Assume the model, the voice and the
senses all get replaced within a year.

---

## WHAT NOT TO BUILD

Anything whose only purpose is to make her *seem* more alive than she is: fake
thinking pauses, invented feelings, claims to have been reading overnight when
no job ran, a "consciousness" indicator. He will find out, and the real
capabilities will be doubted because of it.

Build the faculties. Let the impression follow from them working.

---

## FIRST TASKS, IN ORDER

1. **`tests/test_protected_paths.py`** — the fence, before the thing it fences.
   It must fail if a commit authored by ARIA touches `src/auth/`,
   `src/execution/live_guard.py`, `.github/workflows/`, or the test itself.
2. **The sandbox** — branch, full suite green, PR with rationale, human merge.
   No path from ARIA to `main` that does not pass through him.
3. **Baseline measurements** — capture the numbers in "Measure the swap" while
   the current brain is still in place. Once it is gone you cannot go back and
   take them.
4. **Then** serve the local model and switch the router.
5. Raise the deployment question (laptop / split / dedicated box) before he
   assumes she is running 24/7 when she is not.

Build the cage before you build the thing that will live in it.

One last thing, for whoever picks this up: the temptation with a brief this
ambitious is to make ARIA *appear* to have gained a mind — richer prose, more
confident answers, the language of a being. Resist it. What earns trust here is
that her numbers are honest and her limits are stated, and that reputation took
four adversarial review rounds and three corrections to a measurement harness
that was flattering itself. It would take one embellished demo to lose.
