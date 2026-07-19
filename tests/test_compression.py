"""B3 — compression cascade tests: each stage fires at its threshold on a
simulated long session; summaries respect their token budget; every action
lands in the typed audit log; stubs are retrievable."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.compression.engine import CompressionEngine, estimate_tokens
from src.compression.store import CompressionStore, ulid

# Tiny limit so tests build utilization with small transcripts
CFG = {
    "context_limit_tokens": 1000,
    "compact_at": 0.70, "summarize_at": 0.80,
    "checkpoint_at": 0.90, "truncate_at": 0.95,
    "keep_recent_turns": 4, "summary_batch_turns": 4,
    "summary_max_tokens": 40, "checkpoint_keep_turns": 3,
    "truncate_keep_turns": 2,
}


def engine(tmp_path, llm=None, **over):
    return CompressionEngine(store=CompressionStore(tmp_path / "c.db"),
                             llm=llm, config={**CFG, **over})


def turns(n, chars=100, role_cycle=("user", "assistant")):
    return [{"role": role_cycle[i % 2], "content": f"turn {i} " + "x" * chars}
            for i in range(n)]


def tool_msg(chars=600):
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "y" * chars}]}


def util_of(msgs):
    return estimate_tokens(msgs) / CFG["context_limit_tokens"]


def test_below_threshold_untouched(tmp_path):
    e = engine(tmp_path)
    msgs = turns(4)                                   # tiny
    assert util_of(msgs) < 0.70
    assert e.process(msgs) == msgs
    assert e.store.audit_log() == []


def test_stage1_compacts_old_tool_results(tmp_path):
    e = engine(tmp_path)
    # tool results early, fresh turns after; utilization ≥ 70% but < 80%
    msgs = [tool_msg(2200)] + turns(8, chars=60)
    assert 0.70 <= util_of(msgs) < 0.80
    out = e.process(msgs)
    assert "compacted → stub" in out[0]["content"]
    log = e.store.audit_log()
    assert log[-1]["action"] == "COMPACT" and log[-1]["tokens_saved"] > 0
    # the stub round-trips to the original content
    sid = out[0]["content"].split("stub ")[1].split(";")[0]
    assert "y" * 100 in e.store.get(sid)


def test_stage1_spares_recent_turns(tmp_path):
    e = engine(tmp_path)
    msgs = turns(6, chars=100) + [tool_msg(1700)]     # tool result is fresh
    out = e.process(msgs)
    assert not any("compacted" in str(m.get("content")) for m in out)


def test_stage2_summarizes_batches_within_budget(tmp_path):
    calls = []

    def llm(prompt, budget):
        calls.append(budget)
        return "Decided to buy AAPL; unresolved: TSLA question."
    e = engine(tmp_path, llm=llm)
    msgs = turns(16, chars=200)                       # ≥ 80%, < 90%
    assert 0.80 <= util_of(msgs) < 0.90
    out = e.process(msgs)
    summaries = [m for m in out if "[SUMMARY of turns" in str(m.get("content"))]
    assert summaries and calls
    for s in summaries:                               # budget respected
        assert len(s["content"]) <= CFG["summary_max_tokens"] * 4 + 80
    actions = {a["action"] for a in e.store.audit_log()}
    assert "SUMMARIZE" in actions
    assert estimate_tokens(out) < estimate_tokens(msgs)


def test_stage2_deterministic_fallback_without_llm(tmp_path):
    e = engine(tmp_path, llm=None)
    out = e.process(turns(16, chars=200))
    assert any("[SUMMARY of turns" in str(m.get("content")) for m in out)


def test_stage3_checkpoint_resets_context(tmp_path):
    e = engine(tmp_path, llm=lambda p, b: "s")
    # ≥ 90% and structured so stages 1-2 can't save enough: few, huge turns
    msgs = turns(6, chars=650)
    assert util_of(msgs) >= 0.90
    out = e.process(msgs)
    assert "[CONTEXT CHECKPOINT" in out[0]["content"]
    assert len(out) == 1 + CFG["checkpoint_keep_turns"]
    actions = [a["action"] for a in e.store.audit_log()]
    assert "CHECKPOINT" in actions
    # the checkpoint stores the full pre-reset transcript
    cid = out[0]["content"].split("CHECKPOINT ")[1].split(":")[0]
    assert "turn 0" in e.store.get(cid)


def test_stage4_emergency_truncate_is_loud(tmp_path):
    # truncate fires when even a checkpoint leaves util ≥ 95%: gigantic tail
    e = engine(tmp_path, config_over := {})
    msgs = turns(8, chars=2400)
    out = e.process(msgs)
    assert len(out) <= CFG["truncate_keep_turns"]
    actions = [a["action"] for a in e.store.audit_log()]
    assert "EMERGENCY_TRUNCATE" in actions
    trunc = [a for a in e.store.audit_log()
             if a["action"] == "EMERGENCY_TRUNCATE"][0]
    assert trunc["tokens_saved"] > 0 and trunc["turn_ids"]


def test_ulid_sortable_unique():
    ids = [ulid() for _ in range(50)]
    assert len(set(ids)) == 50
    assert all(len(i) == 26 for i in ids)
