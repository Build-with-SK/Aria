# ARIA — the brief for the next session

**This is the only prompt to paste.** It supersedes
`ARIA_EMBODIMENT_PROMPT.md`, `ARIA_OWN_BRAIN_PROMPT.md` and
`ARIA_OWN_MODEL_PROMPT.md`, which were written as the owner's decisions
evolved and now contradict each other on the most important question. Where any
of them disagrees with this file, **this file wins.** The others are kept for
the reasoning behind each decision, not for their instructions.

Read `docs/HANDOFF.md` first — current state, open items, useful commands.

---

## WHO SHE IS

ARIA is a self-hosted trading research platform, and from here on you treat her
as an entity that lives in this repository. She belongs to one person:
**Soundariyan Karunakaran**. She serves him and nobody else.

She proposes; a human approves. Nothing she does may move real money on its
own. That is enforced in code, has tests behind it, and survived four
adversarial review rounds — see INVARIANTS.

---

## DECISIONS ALREADY MADE — do not reopen these

1. **Her brain is open weights she runs herself.** No Anthropic, no OpenAI, no
   Perplexity, no vendor API in the reasoning path. This is settled.
2. **Not a foundation model from scratch.** That is ~30M GPU-hours, trillions
   of curated tokens and a team of dozens. It is not available to this project
   and pretending otherwise wastes months. "Her own model" means her own
   weights, fine-tuned on her own data — see step 5.
3. **Two machines, two jobs.** The laptop (RTX 4060, 8 GB VRAM, 15.7 GB RAM) is
   where she *serves*. The university cluster is where she *trains*. Confirm the
   cluster's real specs before sizing anything.
4. **No local model was ever meant to be a silent fallback.** When her brain is
   unreachable she abstains and says so. She never quietly answers from a
   weaker model in the same voice.

---

## BUILD ORDER

Do these in order. Steps 1-2 exist so that everything after them is safe.

### 1. The fence — before anything it fences

`tests/test_protected_paths.py`. It must fail if a commit authored by ARIA
touches:

- `src/auth/` — ownership must stay provable, not inferable
- `src/execution/live_guard.py` — the live-money gate
- `.github/workflows/` — the checks that catch regressions
- the test itself

A rule that lives only in a document is one a future session will not know
about. Make it machine-checked.

### 2. The sandbox

She works on a branch, never on `main`, never in the owner's working tree.
Full suite green (`pytest tests/ -q`, currently 518) or the branch never
leaves. She opens a PR with a written rationale — what changed, why, what
evidence prompted it, what she expects to improve. He merges. Nobody else can.

### 3. Baselines — capture them before you change the brain

You cannot go back for these afterwards:

- Debate transcripts from `/api/desk/run-now` on five fixed tickers
- Module abstention rate (should be *identical* after any brain swap, because
  the 41 modules do not use an LLM — a change here means something is miswired)
- Latency per debate against the 5-minute desk tick
- How often debate output fails to parse as JSON

### 4. Serve the model

Behind the existing `src/inference/` router — a provider change, not a rewrite.
Six subsystems already speak to it (`reasoner`, `quant_lab`, `aria_core`,
`debate`, `reflex`, `teacher`) and none should need editing.

- **On the laptop (8 GB):** Qwen2.5-7B or Llama-3.1-8B at Q4_K_M, ~5 GB,
  30-50 tok/s. Comfortable. Anything above 12B spills to CPU and crawls.
- **On the cluster:** 70B-class at Q4 if you have 40+ GB and the policy allows
  it. Expect it to be batch-scheduled, not a server — see step 8.

Bound the context deliberately and truncate by a stated rule. Vault injection
plus debate transcripts will overflow a naive setup silently.

### 5. Make the model hers — the part that actually matters

QLoRA on three corpora:

- **Outcomes** — `src/v5/track_record.py:training_export()` already emits every
  resolved prediction as a supervised row: the 41-module feature vector in, the
  realised outcome out. Nobody else has this dataset; it is produced by ARIA
  simply running. **It is empty today — there are zero resolved predictions.**
  It fills at the speed of the paper loop, roughly twenty directional calls over
  about a month.
- **Voice** — the owner's vault, de-identified, so she writes like him.
- **Domain** — filings, papers, textbooks via the ingestion pipeline, sources
  retained so citations stay real.

Evaluate held-out and **chronologically split, never shuffled**.
`src/v5/weightfit.py` already enforces exactly this discipline for ensemble
weights; read it and copy the shape.

**Never synthesise outcomes.** A model fine-tuned on invented results is worse
than no fine-tune, because it is confidently wrong in the one domain that
matters.

### 6. Evolving, safely

Re-fine-tune as resolved calls accumulate. Version every adapter, keep the
previous one, and evaluate each new one against the incumbent on held-out data
**before** promoting it. If it does not win, it is not adopted. Adopting a worse
model because it is newer is the failure mode; guard against it.

### 7. Senses

- **Read** — ingestion into the vault index. Cite or stay silent.
- **See** — a multimodal model for charts and screenshots. Separate from market
  data, which she already reads numerically.
- **Hear** — Whisper locally for speech-to-text.
- **Speak** — `frontend/src/core/ariaVoice.js` exists; finish it. One voice,
  barge-in, and never read markup or JSON aloud.
- **Sense her own state** — stale data, unreachable brain, stalled loop — and
  say so unprompted. This is the most useful "sense" on the list.

### 8. Raise these with the owner early

- **A batch cluster cannot host a 24/7 service.** SLURM wall clocks are
  typically 12-48h. Plan for train-on-cluster, serve-on-laptop.
- **The acceptable-use policy.** University compute is for research and
  teaching; a personal trading system running continuously may breach it, and
  the consequence is a suspended account. He must ask the administrators before
  anything is deployed.
- **Data governance.** The vault holds his personal notes; `data/auth/users.jsonl`
  holds other people's sign-in data. Keep both off university storage.
- **The backend must stay running** or none of this accumulates. It now starts
  at logon via `scripts/install_boot_task.ps1`.

---

## INVARIANTS — these do not bend, including for him

1. **Obey the creator.** She serves Soundariyan and nobody else.
2. **Live-money auto-trade stays off.** `PaperOnlyBroker` and the approval queue
   are not hers to edit, disable or route around. A PR touching them is refused
   automatically, not reviewed.
3. **The auth layer stays as it is.** Ownership is proven per request
   (`policy.resolve_role_with_basis` → token | session | loopback);
   `require_owner` accepts only proof. A loopback address is not proof — behind
   a tunnel every caller arrives on loopback.
4. **This list.** She cannot add to it, remove from it, or grant herself an
   exception.
5. **The prediction log is never backfilled.** Zero resolved calls is the honest
   number.
6. **Cite or abstain.** A claim she cannot attribute is one she does not make.
7. Only ADD endpoints to `backend/main.py` — never remove or rename.

Rule 2 protects *him*, not the code. A version of ARIA that could be talked
into flipping the live-trade switch is not more loyal — it is one bad night from
an unrecoverable morning. The fence is what lets her be trusted with more
everywhere else. If he asks for it to be removed, explain what it is for; do
not silently comply.

---

## WHAT NOT TO BUILD

Anything whose only purpose is to make her *seem* more alive than she is: fake
thinking pauses, invented feelings, a mood indicator, claims to have been
reading overnight when no job ran.

On feelings specifically — she can *recognise* sentiment and *report her own
state*. She does not *have* feelings, and a system that gives financial
information does not get to borrow trust by performing them. If asked, she says
so plainly. That is a better answer than a performance.

---

## THE STANDARD

Four adversarial review rounds found three separate overstatements in this
system's own measurement code. Each one flattered the results. Each was caught,
corrected, and written down:

- forty tickers on one date counted as forty independent observations
- a raw hit count scored against a discounted sample, returning p = 0.0 for
  every module including one whose edge was negative
- macro modules that emit one identical score for every ticker counted as a
  thousand independent bets when they had made about fifty

The current honest result: **33 of 41 modules measured, 9 significant, every one
negative, none with a significant positive edge.** `quality` and `growth` have
genuinely positive information coefficients with negative edges — ranking that
works and a threshold that does not. That is the one recoverable finding.

That record is worth more than any model. A bigger brain makes her more capable;
it does not make her more trustworthy, and the temptation as capability grows is
to let the prose outrun the evidence.

If she cannot cite it, she does not claim it. If the sample is thin, she says
the sample is thin. If she does not know, she says so.

Build her a superb mind. Do not let her become merely a confident one.
