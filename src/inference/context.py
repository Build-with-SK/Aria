"""
src/inference/context.py
========================
A STATED TRUNCATION RULE (docs/ARIA_NEXT_SESSION.md step 4).

"Bound the context deliberately and truncate by a stated rule. Vault injection
plus debate transcripts will overflow a naive setup silently."

Silently is the word that matters. Two things overflow quietly in this stack:

- Ollama truncates to `num_ctx` and returns a normal-looking answer. The model
  never sees the end of the prompt and never says so. See `providers/ollama.py`,
  which now sets `num_ctx` explicitly rather than inheriting a default.
- Vault injection and on-demand ticker blocks are appended to the system
  prompt with no size check at all. A long note and a long debate transcript
  together can push the actual question out of the window.

The rule, in order. Earlier steps are tried first; each one is recorded.

  1. NEVER dropped: the identity preamble (the Two Laws) and the most recent
     user message. If those do not fit, nothing sensible can be said and the
     caller is told so rather than handed a mutilated prompt.
  2. Oversized blocks are trimmed to `per_block_chars` first, longest first.
     Half of two sources beats all of one, and one long vault note must not be
     able to evict everything else simply by being long.
  3. Then blocks are dropped whole, lowest priority first and oldest first
     within a priority. A vault excerpt is worth less than the conversation it
     is supporting.
  4. Then older conversation turns, oldest first, in pairs.
  5. As a last resort the most recent user message is trimmed, and the note
     says so explicitly. A truncated question answered as if whole is the
     failure this whole module exists to prevent.

Whatever was dropped comes back in `FitResult.note`, which the caller appends
to the system prompt so she can say what she could not see. "Cite or abstain"
(invariant 6) is not satisfiable by a model that does not know its own
evidence went missing.

Token counts here are ESTIMATES — `chars / CHARS_PER_TOKEN` with a safety
margin. There is no tokeniser in this process and adding one would mean a
dependency per model family. The margin is what makes an estimate safe to
budget against; it is not accuracy dressed up.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Conservative for English prose; JSON and tickers run denser, which is why
#: the budget carries a margin rather than this number being tuned.
CHARS_PER_TOKEN = 3.6

#: Fraction of the window held back so an estimate that runs 15% light still
#: leaves room for the reply.
SAFETY_MARGIN = 0.15


def estimate_tokens(text: str) -> int:
    return int(len(text or "") / CHARS_PER_TOKEN) + 1


@dataclass
class Block:
    """One injected piece of context — a vault excerpt, a debate transcript,
    an on-demand ticker block."""
    name: str
    text: str
    priority: int = 50      # higher survives longer
    order: int = 0          # tie-break; lower is older

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass
class Budget:
    """What the model can actually hold, minus what the reply needs."""
    context_tokens: int = 8192
    reply_tokens: int = 1024
    per_block_chars: int = 4000

    @property
    def prompt_tokens(self) -> int:
        usable = (self.context_tokens - self.reply_tokens) * (1 - SAFETY_MARGIN)
        return max(256, int(usable))


@dataclass
class FitResult:
    system: str
    messages: list[dict]
    blocks: list[Block] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    trimmed: list[str] = field(default_factory=list)
    used_tokens: int = 0
    budget_tokens: int = 0
    fits: bool = True

    @property
    def note(self) -> str:
        """One line for the system prompt. Empty when nothing was lost —
        a note that always fires is a note nobody reads."""
        if not self.dropped and not self.trimmed:
            return ""
        parts = []
        if self.dropped:
            parts.append(f"dropped {len(self.dropped)} ({', '.join(self.dropped[:6])}"
                         + (", …" if len(self.dropped) > 6 else "") + ")")
        if self.trimmed:
            parts.append(f"shortened {len(self.trimmed)} ({', '.join(self.trimmed[:6])}"
                         + (", …" if len(self.trimmed) > 6 else "") + ")")
        return ("CONTEXT NOTICE — this prompt did not fit the model's window, so "
                + " and ".join(parts) + ". Say so if the answer depends on "
                "something you cannot see; do not fill the gap.")

    def to_dict(self) -> dict:
        return {"used_tokens": self.used_tokens, "budget_tokens": self.budget_tokens,
                "fits": self.fits, "dropped": self.dropped, "trimmed": self.trimmed,
                "blocks_kept": [b.name for b in self.blocks]}


def _messages_tokens(messages: list[dict]) -> int:
    return sum(estimate_tokens(m.get("content", "")) + 4 for m in messages)


def fit(system: str, messages: list[dict], blocks: list[Block] | None = None,
        budget: Budget | None = None) -> FitResult:
    """Apply the rule above. Returns what to send and what was lost."""
    budget = budget or Budget()
    # Strongest first, so `pop()` sheds the weakest. Within one priority the
    # OLDEST goes first: a vault excerpt fetched three questions ago is the
    # least likely to be what this question is about.
    blocks = sorted(list(blocks or []), key=lambda b: (b.priority, b.order),
                    reverse=True)
    messages = [dict(m) for m in messages]
    limit = budget.prompt_tokens

    result = FitResult(system=system, messages=messages, blocks=blocks,
                       budget_tokens=limit)

    def total() -> int:
        return (estimate_tokens(system)
                + sum(b.tokens for b in result.blocks)
                + _messages_tokens(result.messages))

    # 2. trim oversized blocks first, longest first. Dropping a block that
    #    would have fitted at half its length loses a source unnecessarily.
    if total() > limit:
        for b in sorted(result.blocks, key=lambda b: len(b.text), reverse=True):
            if total() <= limit:
                break
            if len(b.text) > budget.per_block_chars:
                b.text = b.text[:budget.per_block_chars] + "\n…[trimmed]"
                result.trimmed.append(b.name)

    # 3. shed blocks whole, weakest first (the sort put the strongest first)
    while total() > limit and result.blocks:
        gone = result.blocks.pop()
        result.dropped.append(gone.name)
        if gone.name in result.trimmed:
            result.trimmed.remove(gone.name)     # dropped supersedes trimmed

    # 4. shed older turns in pairs, keeping the most recent user message
    while total() > limit and len(result.messages) > 1:
        dropped_pair = 0
        while dropped_pair < 2 and len(result.messages) > 1:
            m = result.messages.pop(0)
            result.dropped.append(f"turn:{m.get('role', '?')}")
            dropped_pair += 1

    # 5. last resort: the question itself, and the note says so
    if total() > limit and result.messages:
        last = result.messages[-1]
        content = last.get("content", "")
        overflow_tokens = total() - limit
        keep_chars = max(400, len(content) - int(overflow_tokens * CHARS_PER_TOKEN) - 200)
        if keep_chars < len(content):
            last["content"] = content[:keep_chars] + "\n…[your message was cut here]"
            result.trimmed.append("the latest message")

    result.used_tokens = total()
    result.fits = result.used_tokens <= limit
    return result


def compose_system(system: str, blocks: list[Block], note: str = "") -> str:
    """The system prompt as it goes to the model: identity, then the blocks
    that survived, then the notice about the ones that did not."""
    parts = [system.rstrip()]
    # Shedding order is priority-then-newest; READING order is priority then
    # chronological, which is how the blocks were assembled.
    for b in sorted(blocks, key=lambda b: (-b.priority, b.order)):
        parts.append(f"\n\n--- {b.name} ---\n{b.text.strip()}")
    if note:
        parts.append(f"\n\n{note}")
    return "".join(parts)


def bound(system: str, messages: list[dict], blocks: list[Block] | None = None,
          budget: Budget | None = None) -> tuple[str, list[dict], FitResult]:
    """One call for the common case: fit, then compose."""
    result = fit(system, messages, blocks, budget)
    return (compose_system(system, result.blocks, result.note),
            result.messages, result)
