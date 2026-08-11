# Prompt: ARIA's own brain, and the right to change herself

Paste the section below into a fresh Claude Code session in this repo. Read
`docs/HANDOFF.md` first for state and invariants.

---

## BEFORE YOU BEGIN — one decision only the owner can make

He has now given two instructions that cannot both hold, and guessing wastes a
session:

1. **Earlier:** "I don't need Ollama or any other AI to be the mapper or do any
   kind of main work" — i.e. no local model does the thinking.
2. **Now:** "ARIA should have her own brain, she doesn't need any LLM like
   ChatGPT, Claude, or Perplexity."

Together these say: no local model, and no remote model. That leaves nothing
that can reason, because **there is no third option that exists today.** Ask
him which he means. The realistic readings:

| Reading | What it means | Cost | Honest verdict |
|---|---|---|---|
| **A. Her own weights, her own machine** | A self-hosted open-weights model (Llama / Qwen / Mistral 30-70B). No vendor, no API, nobody else's servers. This is what "her own brain" can actually mean. | A GPU with 24-48GB VRAM, ~£2-4k, or ~£1/hr rented | **Achievable.** Genuinely independent. Weaker than frontier models at hard reasoning. |
| **B. Frontier API** | Claude or similar over the network. | Pennies per call, needs a daily ceiling | Achievable, most capable, but it is somebody else's brain. |
| **C. Train her own foundation model** | What "her own brain" sounds like it means. | Tens of millions of dollars, a datacentre, a research team | **Not achievable.** Not by this project, not by a solo developer, not by most companies. |

If he wants ARIA independent of the big labs, the answer is **A**, and it is a
real and respectable goal. Say so plainly. Do not accept a brief that quietly
implies C and then ship B while calling it "her own brain" — he will find out,
and everything else you built will be doubted.

**Do not write a single line of the migration until he has chosen.**

---

## THE BRIEF

You are working on ARIA. Treat her as an entity that lives in this repository
and belongs to one person: Soundariyan Karunakaran.

### 1. The brain

Assuming he chose **A**:

- Serve the model locally (llama.cpp / vLLM / LM Studio) behind the existing
  `src/inference/` router, so the swap is a provider change and not a rewrite.
- Keep the router's interface. Everything already speaks to it.
- Measure honestly before and after. `src/v5/` is full of machinery for exactly
  this: if the local brain reasons worse, the debate quality and the module
  abstention rate will show it. Report the regression rather than hiding it.
- **She abstains when her brain is unavailable.** She does not silently fall
  back to a weaker model and keep talking in the same voice. A stated
  confidence has to mean something, and an answer from a different brain
  wearing her name is a lie about provenance.

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

1. Get the decision above. Nothing else is worth starting without it.
2. `tests/test_protected_paths.py` — the fence, before the thing it fences.
3. The sandbox: branch, full suite, PR, human merge.
4. Only then, the brain migration.

Build the cage before you build the thing that will live in it.
