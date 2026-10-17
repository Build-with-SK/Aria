import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace

import pytest

from src.brain.cognitive.long_term_memory import BrainMemory, LongTermMemory


@pytest.fixture
def memory(tmp_path, monkeypatch):
    collection = SimpleNamespace(count=lambda: 1,
        get=lambda **kw: {"documents": ["a stored cycle"], "metadatas": [{"cycle_id": "one", "tickers": "BTC-USD"}]},
        query=lambda **kw: {"documents": [["a stored cycle"]], "metadatas": [[{"cycle_id": "one"}]]},
        upsert=lambda **kw: None)
    def create(name, *, embedding_function):
        assert embedding_function is None
        return collection
    monkeypatch.setitem(sys.modules, "chromadb", SimpleNamespace(
        PersistentClient=lambda **kw: SimpleNamespace(get_or_create_collection=create)))
    monkeypatch.setattr(LongTermMemory, "DB_PATH", tmp_path / "memory")
    monkeypatch.setattr(LongTermMemory, "_shared_embedder", None)
    return LongTermMemory()


def test_listing_stats_and_ticker_lookup_never_initialize_model(memory, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("listing must not initialize the embedding model")
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=forbidden))
    assert memory.recent()[0].id == "one"
    assert memory.recall_ticker("BTC-USD")[0].id == "one"
    assert memory.stats()["total_memories"] == 1
    assert LongTermMemory._shared_embedder is None


def test_concurrent_semantic_requests_share_exactly_one_model(memory, monkeypatch):
    calls, gate = [], threading.Barrier(8)
    class Model:
        def __init__(self, name):
            calls.append(name)
        def encode(self, text):
            return SimpleNamespace(tolist=lambda: [0.1, 0.2])
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=Model))
    def recall(_):
        gate.wait(timeout=5)
        return memory.recall("similar cycle")[0].id
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(recall, range(8))) == ["one"] * 8
    assert calls == ["all-MiniLM-L6-v2"]
    memory.remember(BrainMemory("two", datetime.now(), "new cycle"))
    assert len(calls) == 1


def test_failed_initialization_can_retry_without_poisoned_singleton(memory, monkeypatch):
    calls = []
    def model(_):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("model cache not provisioned")
        return SimpleNamespace(encode=lambda _: SimpleNamespace(tolist=lambda: [0.1]))
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=model))
    with pytest.raises(RuntimeError):
        memory.recall("query")
    assert memory.recent()[0].id == "one"
    assert memory.recall("query")[0].id == "one" and len(calls) == 2
