"""
tests/test_inference_context.py
===============================
The stated truncation rule (docs/ARIA_NEXT_SESSION.md step 4).

The requirement is not "do not overflow" — it is "do not overflow SILENTLY".
So most of these tests are about what the caller is told after something was
dropped, not merely that the prompt got shorter.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.inference.context import (Block, Budget, FitResult, bound,
                                   compose_system, estimate_tokens, fit)


def words(n: int, token: str = "word ") -> str:
    return token * n


SMALL = Budget(context_tokens=1000, reply_tokens=200, per_block_chars=200)

#: Trimming off, so the DROP order can be tested in isolation. With trimming
#: enabled a small block set is rescued by step 2 and never reaches step 3 —
#: which is the desired behaviour, and the reason it has to be disabled here.
NO_TRIM = Budget(context_tokens=1000, reply_tokens=200, per_block_chars=10 ** 6)


# ── the budget ───────────────────────────────────────────────────────────────

def test_the_budget_holds_back_a_margin():
    """The token count is an estimate. A budget with no margin is an estimate
    presented as a measurement."""
    b = Budget(context_tokens=8192, reply_tokens=1024)
    assert b.prompt_tokens < 8192 - 1024
    assert b.prompt_tokens > 0.7 * (8192 - 1024)


def test_a_tiny_window_still_leaves_something_to_say():
    assert Budget(context_tokens=200, reply_tokens=400).prompt_tokens >= 256


# ── nothing lost, nothing said ───────────────────────────────────────────────

def test_a_prompt_that_fits_is_untouched():
    result = fit("identity", [{"role": "user", "content": "hello"}],
                 [Block("vault:note", "short note")], SMALL)
    assert result.fits
    assert result.dropped == [] and result.trimmed == []
    assert result.note == ""            # a notice that always fires is noise
    assert len(result.blocks) == 1


# ── the order things are shed in ─────────────────────────────────────────────

def test_low_priority_blocks_go_first():
    blocks = [Block("vault:old", words(400), priority=10, order=1),
              Block("debate:AAPL", words(400), priority=90, order=2)]
    result = fit("identity", [{"role": "user", "content": "what now?"}],
                 blocks, NO_TRIM)
    assert "vault:old" in result.dropped
    assert [b.name for b in result.blocks] == ["debate:AAPL"]


def test_within_one_priority_the_oldest_goes_first():
    blocks = [Block("vault:a", words(400), priority=50, order=1),
              Block("vault:b", words(400), priority=50, order=2)]
    result = fit("identity", [{"role": "user", "content": "q"}], blocks, NO_TRIM)
    assert result.dropped[0] == "vault:a"


def test_the_latest_user_message_survives_when_turns_are_shed():
    messages = [{"role": "user", "content": words(200)},
                {"role": "assistant", "content": words(200)},
                {"role": "user", "content": words(200)},
                {"role": "assistant", "content": words(200)},
                {"role": "user", "content": "THE ACTUAL QUESTION"}]
    result = fit("identity", messages, [], SMALL)
    assert result.messages[-1]["content"] == "THE ACTUAL QUESTION"
    assert any(d.startswith("turn:") for d in result.dropped)


def test_blocks_are_shed_before_conversation_turns():
    """A vault excerpt is worth less than the conversation it supports — so
    when trimming is not enough, the block goes and the turns stay."""
    budget = Budget(context_tokens=1000, reply_tokens=200, per_block_chars=2000)
    messages = [{"role": "user", "content": words(100)},
                {"role": "assistant", "content": words(100)},
                {"role": "user", "content": "latest"}]
    blocks = [Block("vault:essay", words(2000), priority=10, order=1)]
    result = fit("identity", messages, blocks, budget)
    assert "vault:essay" in result.dropped
    assert len(result.messages) == 3     # no turn needed to go


def test_a_block_that_fits_when_trimmed_is_not_dropped():
    """One long note must not be able to evict itself entirely — or evict
    everything else — simply by being long."""
    result = fit("identity", [{"role": "user", "content": "q"}],
                 [Block("vault:long-note", words(2000), priority=50, order=1)],
                 SMALL)
    assert result.dropped == []
    assert "vault:long-note" in result.trimmed
    assert len(result.blocks) == 1


def test_surviving_blocks_are_trimmed_before_the_question_is():
    big = Block("debate:AAPL", words(2000), priority=99, order=1)
    result = fit("identity", [{"role": "user", "content": "q"}], [big], SMALL)
    assert "debate:AAPL" in result.trimmed
    assert len(result.blocks[0].text) <= SMALL.per_block_chars + 20


def test_cutting_the_question_is_the_last_resort_and_is_disclosed():
    """A truncated question answered as if whole is the failure this module
    exists to prevent."""
    result = fit("identity", [{"role": "user", "content": words(5000)}], [], SMALL)
    assert "the latest message" in result.trimmed
    assert "cut here" in result.messages[-1]["content"]
    assert "CONTEXT NOTICE" in result.note


# ── what she is told about what she cannot see ───────────────────────────────

def test_the_notice_names_what_went_missing():
    blocks = [Block("vault:trading-plan", words(400), priority=10, order=1),
              Block("vault:risk-notes", words(400), priority=10, order=2)]
    result = fit("identity", [{"role": "user", "content": "q"}], blocks, NO_TRIM)
    note = result.note
    assert "vault:trading-plan" in note
    assert "do not fill the gap" in note


def test_the_notice_reaches_the_system_prompt():
    """Cite-or-abstain is not satisfiable by a model that does not know its
    evidence went missing."""
    blocks = [Block("vault:a", "SECRET-VAULT-CONTENT " + words(500),
                    priority=10, order=1),
              Block("debate:AAPL", "the debate said X", priority=90, order=2)]
    budget = Budget(context_tokens=1000, reply_tokens=200, per_block_chars=3000)
    system, messages, result = bound("IDENTITY PREAMBLE",
                                     [{"role": "user", "content": "q"}],
                                     blocks, budget)
    assert "IDENTITY PREAMBLE" in system
    assert "the debate said X" in system      # the surviving block is inlined
    assert "CONTEXT NOTICE" in system
    assert "SECRET-VAULT-CONTENT" not in system   # the dropped block is gone
    assert "vault:a" in system                    # but it is NAMED as missing


def test_composition_reads_in_assembly_order():
    blocks = [Block("second", "B", priority=50, order=2),
              Block("first", "A", priority=50, order=1)]
    system = compose_system("identity", blocks)
    assert system.index("--- first ---") < system.index("--- second ---")


def test_the_result_is_reportable():
    result = fit("identity", [{"role": "user", "content": "q"}],
                 [Block("vault:a", "x")], SMALL)
    d = result.to_dict()
    assert d["blocks_kept"] == ["vault:a"]
    assert d["budget_tokens"] == SMALL.prompt_tokens
    assert d["fits"] is True


def test_token_estimate_scales_with_length():
    assert estimate_tokens("") == 1
    assert estimate_tokens(words(100)) > estimate_tokens(words(10))


def test_an_empty_conversation_does_not_crash():
    result = fit("identity", [], [], SMALL)
    assert isinstance(result, FitResult)
    assert result.messages == []
