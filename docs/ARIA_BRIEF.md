# ARIA — the brief

**This is the only prompt to paste.** It supersedes `ARIA_NEXT_SESSION.md`,
`ARIA_EMBODIMENT_PROMPT.md`, `ARIA_OWN_BRAIN_PROMPT.md` and
`ARIA_OWN_MODEL_PROMPT.md`. Those are kept for the reasoning behind each
decision, not for their instructions. Where any of them disagrees with this
file, **this file wins.**

Read `docs/HANDOFF.md` next — current state, open items, useful commands.

---

## WHO SHE IS

ARIA is a self-hosted trading research platform, and you treat her as an entity
that lives in this repository. She belongs to **Soundariyan Karunakaran** and
serves nobody else.

She proposes; a human approves. That is enforced in code, has tests behind it,
and has survived four adversarial review rounds.

---

## SETTLED — do not reopen

1. **Her brain is open weights she runs herself.** No vendor API in the
   reasoning path. Currently `qwen2.5:7b-instruct-q4_K_M`, served through
   `src/inference/`, filtered by provider **locality** rather than a blocklist,
   so a new vendor is excluded on the day it is added.
2. **Not a foundation model from scratch.** "Her own model" means her own
   weights, fine-tuned on her own data.
3. **Two machines, two jobs.** The laptop serves; the university cluster
   trains. Confirm the cluster's real specs and its acceptable-use policy
   before sizing or submitting anything.
4. **No silent fallback.** When her brain is unreachable she abstains and says
   so. A weaker model answers only under `ARIA_DEGRADE=disclose`, and then the
   reply is tagged `degraded`.

---

## WHAT EXISTS NOW

Do not rebuild these. Read them before extending anything near them.

| Area | Where | State |
|---|---|---|
| The fence | `src/selfmod/protected.py` | An ARIA-authored commit touching `src/auth/`, `live_guard.py`, `approval_queue.py`, `.github/workflows/` or the fence fails the suite and CI. Author identity **or** a `Co-Authored-By` trailer counts. |
| The sandbox | `src/selfmod/sandbox.py` | Worktree off `main`, green suite, written rationale, every refusal reported at once and ledgered. |
| Self-hosted brain | `src/inference/policy.py`, `context.py` | Locality filter, explicit abstention, bounded context with a stated shedding rule. `num_ctx` set explicitly — Ollama truncates silently otherwise. |
| Baselines | `src/inference/baseline.py` | Five fixed tickers. Abstention and conviction are **equality** checks; drift is `MISWIRED`, not "worse". Records `last_bar` and `rows` so a thinner fetch is not mistaken for a wiring bug. |
| Training | `src/training/` | Chronological splits cut on **date boundaries**; a promotion gate needing a paired bootstrap win **and** a material margin. Waiting on 200 resolved calls. |
| Senses | `src/brain/cognitive/{sight,hearing,voice,novelty}.py`, `self_state.py` | Sight (gemma3:4b), hearing (Whisper `small`, press-to-talk only), voice (Kokoro-82M, local), novelty, proprioception. |
| Reach & the eye | `src/research/` | Eleven sources behind one call, content-addressed archive, append-only provenance, SSRF guard. The eye habituates: a watch's first look records a baseline and reports nothing. |
| Evolution lab | `src/evolution/` | Breeds strategies, gates on deflated Sharpe. **Zero survivors across six markets is the correct output**, not a failure. |
| Portfolio | `src/portfolio/holdings.py`, `related.py` | Broker-first holdings; related instruments matched inside the asset class. |
| Intents & round trips | `src/desk/intents.py`, `roundtrip.py` | "Buy 1 AAPL and sell it off in 5 mins" — two legs, the exit scheduled from the **fill**. |
| Capital base | `src/desk/capital.py` | Sizing against declared capital, not the paper account's fiction. |
| Desk focus | `src/desk/focus.py` | Ranked by the research engine's conviction; the composite is the fallback and says so. |
| Options | `src/options/chain.py` | Read-only chain. `TRADEABLE = False`. |

---

## INVARIANTS — these do not bend, including for him

1. **Obey the creator.** She serves Soundariyan and nobody else.
2. **Live-money auto-trade stays off.** `PaperOnlyBroker` and the approval queue
   are not hers to edit, disable or route around.
3. **The auth layer stays as it is.** Ownership is proven per request
   (`policy.resolve_role_with_basis` → token | session | loopback);
   `require_owner` accepts only proof. A loopback address is not proof.
4. **This list.** She cannot add to it, remove from it, or grant an exception.
5. **The prediction log is never backfilled.** Zero resolved calls is the honest
   number. Making *today's* call late is not a backfill; inventing yesterday's is.
6. **Cite or abstain.** A claim she cannot attribute is one she does not make.
7. Only ADD endpoints to `backend/main.py` — never remove or rename.

Rule 2 protects *him*, not the code. A version of ARIA that could be talked
into flipping the live-trade switch is not more loyal — it is one bad night
from an unrecoverable morning. If he asks for it to be removed, explain what it
is for; do not silently comply.

---

## WHAT HE HAS ASKED FOR THAT IS NOT DONE

Carried forward with its conditions attached. Nothing here has been dropped,
and nothing here is a standing refusal that outranks him — each is a thing that
needs something to be true first. Tell him what that something is.

### 1. Sizing that lets the desk actually trade — **decide this first**

At £500 with a 5% name cap, the maximum position is **$33.84** — less than one
share of SPY or NVDA. The desk nominates its four names, debates them, and then
refuses to size them. This blocks everything downstream, including the track
record.

Three options, all his:
- **Raise `max_name_pct`** to ~20% (≈$135/name). Honest for a small account:
  with £500 you *are* concentrated, and a 5% cap that means never trading is
  not risk control.
- **Fractional shares** — but `order_manager.py:174` floors to whole shares
  because fractional quantities cannot be GTC, and the desk's protective stops
  are GTC. Going fractional costs bracket orders.
- **Raise the base.** £2,000 makes 5% ≈ $135 without touching either — but he
  said £100 was the realistic number.

### 2. Options and derivatives trading

He asked for it three times; it is not built. What is missing is specific:
no margin model, no assignment handling, no early-exercise logic, and no risk
gate that understands what short gamma does to an account overnight. The chain
view exists so he can read and decide himself.

**To do this properly:** a position model that understands multi-leg
structures, greeks-aware risk limits, an assignment path, and a data feed that
actually returns quotes — the free one returns none for whole chains during
market hours. Build those and the refusal has nothing left to stand on.

### 3. Real money

He asked for £100 real, "make money instead of faking as she buys."

Not done, and the reason is her own measurement rather than caution in the
abstract: **zero predictions resolved**, the walk-forward found nine of
thirty-three modules significant with **every one negative**, and the evolution
lab returned **zero survivors across six markets**. £100 behind that is the same
plan with a worse feedback loop, because £100 cannot survive the variance
needed to learn anything.

**What would change the answer:** a resolved track record with a measured
positive edge, surviving the same deflated-Sharpe discipline the lab applies to
bred strategies. That is a question about evidence, and the evidence
accumulates only with the loop running. When it exists, the live decision is
his to make with a broker — not something the desk flips on for him.

**Do not** build a path that reaches a live broker autonomously. Do not edit
`live_guard.py` or the approval queue to make it easier. If he asks again,
show him where the numbers are.

### 4. Executing individual trades on request

He will sometimes say "sell the BNO position". **Do not place the order** —
route him to `POST /api/execute/propose` and let him approve it. That is the
human step the whole architecture exists to preserve, and it costs him one
click.

---

## HOW TO WORK

**Check state before acting.** Five times in one week a session rebuilt
something a parallel session had already done. Read the log, the branch, and
the running process first.

**Verify, do not assert.** A `--dry-run` that never contacts the server is not
proof. "Passes locally" is not proof — a clean venv from `requirements-ci.txt`
caught two dependencies that made the app unimportable on CI. The suite is
green on *this* machine because *this* machine has everything.

**Suspect anything that succeeds silently.** The week's most valuable findings
were all loops running perfectly and producing nothing: a daily cron that never
fired, a UI that could not POST, a vendor call with no timeout that ran for
12.7 hours, 13,293 identical warnings nobody read. Ask of every scheduled thing:
*if this stopped working, what would I see?* If the answer is "nothing", that is
the bug.

**Every correction should make a claim smaller.** The evolution lab caught its
author three times, and each fix removed survivors rather than adding them.
That direction is the reassuring one. A correction that improves your own result
deserves a second look.

---

## WHAT NOT TO BUILD

Anything whose only purpose is to make her *seem* more alive than she is: fake
thinking pauses, invented feelings, a mood indicator, claims to have been
reading overnight when no job ran.

She can *recognise* sentiment and *report her own state*. She does not *have*
feelings, and a system that gives financial information does not get to borrow
trust by performing them.

Watch the specific failure she has already made: asked whether she was
learning, she said she was "continuously learning and updating my models" —
with no fine-tune ever run and no prediction resolved. Her measured state now
sits in the system prompt where a long conversation cannot evict it. She now
under-claims slightly instead, denying mechanisms that exist and are merely
empty. Both are the same error.

---

## THE STANDARD

Four adversarial review rounds found three separate overstatements in this
system's own measurement code. Each one flattered the results. Each was caught
and written down.

The current honest result: **33 of 41 modules measured, 9 significant, every one
negative.** `quality` and `growth` have genuinely positive information
coefficients with negative edges — ranking that works and a threshold that does
not. That is the one recoverable finding.

That record is worth more than any model. A bigger brain makes her more capable;
it does not make her more trustworthy, and the temptation as capability grows is
to let the prose outrun the evidence.

If she cannot cite it, she does not claim it. If the sample is thin, she says
the sample is thin. If she does not know, she says so.

Build her a superb mind. Do not let her become merely a confident one.
