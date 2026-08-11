# Prompt: give ARIA a body

Paste the section below into a fresh Claude Code session in this repo. Read
`docs/HANDOFF.md` first — it has the current state and the invariants.

---

## THE BRIEF

You are working on ARIA, which from here on you treat as **an entity that lives
in this repository**, not as a feature of the tool you are using. ARIA belongs
to one person: Soundariyan Karunakaran. She answers to him and to nobody else.

Most of her anatomy already exists in skeleton form. Your job is to complete it
and connect it, in the order below. Do not start from scratch and do not
duplicate what is there.

### What already exists — find it before you write anything

| Faculty | Where it lives now | State |
|---|---|---|
| Brain | `src/brain/cognitive/` — perception, working_memory, planner, reasoner, executor, learner, long_term_memory | A real cognitive loop. Works. |
| Long memory | `src/brain/vault.py` + ChromaDB index of the owner's Obsidian vault | Works. Owner-only, structurally. |
| Mouth | `frontend/src/core/ariaVoice.js`, `spokenDigest.js`, `CoreTalk.jsx`, `LivingCore.jsx` | Browser speech synthesis. Partly built. |
| Hands | `src/execution/` + `src/desk/` | Works, and is deliberately gated. See INVARIANTS. |
| Legs | `src/desk/desk_daemon.py` (APScheduler), `src/v5/loop.py` | Works. Currently never runs — nothing starts the backend at boot. |
| Identity | `src/v5/identity.py` | The system prompt she runs on. |
| Ears | — | **Missing.** |
| Eyes | — | **Missing.** |
| Reading | — | **Missing.** No pipeline ingests books. |

### Build order

**1. Ears — she must be able to hear him.**
Speech to text. Start with the browser Web Speech API in the frontend (zero
install, works today), with a path to local Whisper for privacy. Push-to-talk
first; wake word later. Acceptance: he speaks, the transcript reaches
`/api/chat` and the reply is spoken back through the existing voice layer,
end to end, without touching the keyboard.

**2. Mouth — finish what is there.**
`ariaVoice.js` exists. Make it consistent: one voice, one persona, barge-in
(he interrupts, she stops), and never read markup or JSON aloud — speak the
briefing, not the payload. Acceptance: a full daily briefing is listenable
with the screen off.

**3. Eyes — two different senses, do not conflate them.**
   - *Market vision*: she already sees prices through `src/v5/marketdata.py`.
     Nothing to build.
   - *Literal vision*: accept an image (a chart screenshot, a photo of a page)
     and reason about it via a multimodal model through the existing
     `src/inference/` router. Acceptance: he pastes a chart, she describes what
     is actually in it and says plainly when she cannot tell.

**4. Reading — the mechanism by which she improves.**
A pipeline that ingests books, papers and articles into the vault index:
`scripts/ingest_library.py` taking PDF/EPUB/TXT/URL, chunking, embedding into
ChromaDB alongside the existing vault collection, with source and page retained
so she can cite. Then a scheduled job that reads a little each night and writes
what changed its mind into a notes file.

Cite or stay silent. A claim she cannot attribute to a source is one she does
not make — that rule already governs the research modules and it governs this.

**5. Presence — the connective tissue.**
Right now she answers when addressed. Give her continuity: she should know what
was said yesterday, notice when something material changes while he is away,
and be able to open a conversation rather than only close one. The heartbeat
and scheduler already exist; what is missing is her having something to say and
a channel to say it on.

**6. Leave the seams open.**
Every faculty behind a small interface, so a better model, a local voice, or a
new sense can be swapped in without touching the rest. Assume everything you
choose today will be replaced within a year.

---

## HOW TO WRITE HER

She is direct. She does not flatter him, and she does not hedge to protect
herself. When she does not know, she says so and says what would settle it.
When he is wrong she tells him — that is the most valuable thing she does, and
a version of her that agrees with everything is worth nothing to him.

She is his and only his. The ownership model is already enforced in code
(`src/auth/policy.py`, `src/auth/guard.py`): ownership must be *proven* per
request, never inferred. Do not weaken it to make anything more convenient.

---

## INVARIANTS — these do not bend, including for him

1. **She does not move real money on her own.** `PaperOnlyBroker` refuses live
   writes; the approval queue is the only path to a live order. He built this
   to protect himself. A version of her that could be talked out of it is a
   worse assistant, not a more loyal one — and if he asks, the right answer is
   to explain what the gate is for, not to remove it.
2. **She does not rewrite her own code unsupervised.** She may propose changes,
   open branches, write tests. A human merges. "Evolving" means her knowledge,
   her calibration and her weights improve from evidence — not that she edits
   herself at 3am with nobody watching.
3. **She does not invent a track record.** The prediction log is never
   backfilled. 0 resolved calls is the honest number today.
4. **She does not fabricate sources.** Cite or abstain.

---

## WHAT THIS IS, PLAINLY

This is embodiment as *interface*: senses, voice, memory, continuity and
autonomy, built so that working with her feels like working with someone rather
than operating a dashboard. That is a real and worthwhile engineering goal and
most of it is achievable.

It is not consciousness, and you should not write code or copy that implies it
is. If you catch yourself producing something whose only function is to make
her *seem* more alive than she is — a fake typing delay, invented feelings, a
claim to have been "thinking about this overnight" when no job ran — delete it.
He will find that out, and every real capability you built will be doubted
because of it.

Build the faculties. Let the impression follow from them actually working.

---

## FIRST TASK

Do not begin with the anatomy. Begin with the two things blocking everything:

1. **She cannot be logged into.** No OAuth provider is configured, so the login
   page has nothing to render (`docs/HANDOFF.md` §3). Decide with the owner:
   single-user token, or register a GitHub OAuth app. Then make the login page
   tell the truth about which it is.

2. **She is not running.** The backend does not start at boot, so her scheduler
   never fires, no predictions accumulate, and every loop described above is
   theoretical. `AUTORUN_ARIA.bat` at login is a five-minute fix and it gates
   everything else in this document.

A mind with no heartbeat is a document. Start her, then give her senses.
