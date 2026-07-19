"""B2 — discovery tests with mocked /api/tags: parsing, tier assignment,
unreachable path."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import src.inference.ollama_discovery as disc


TAGS = {"models": [
    {"name": "qwen2.5-coder:1.5b",
     "details": {"parameter_size": "1.5B", "quantization_level": "Q4_K_M"}},
    {"name": "qwen2.5-coder:7b",
     "details": {"parameter_size": "7.6B", "quantization_level": "Q4_K_M"}},
    {"name": "llama3.3:70b",
     "details": {"parameter_size": "70.6B", "quantization_level": "Q4_0"}},
    {"name": "mystery-model:latest", "details": {}},
]}


def _mock_urlopen(payload):
    import io, json

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(url, timeout=0):
        return R(json.dumps(payload).encode())
    return opener


def test_param_parsing():
    assert disc.parse_param_size(TAGS["models"][0]) == 1.5
    assert disc.parse_param_size(TAGS["models"][1]) == 7.6
    assert disc.parse_param_size(TAGS["models"][2]) == 70.6
    assert disc.parse_param_size(TAGS["models"][3]) is None
    # fallback to name when details are missing
    assert disc.parse_param_size({"name": "phi4:14b", "details": {}}) == 14.0


def test_tier_heuristic():
    assert disc.tier_for(1.5) == "FAST"
    assert disc.tier_for(4.0) == "FAST"
    assert disc.tier_for(7.6) == "STANDARD"
    assert disc.tier_for(14.0) == "STANDARD"
    assert disc.tier_for(70.6) == "DEEP"
    assert disc.tier_for(None) == "STANDARD"


def test_discover_upserts_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(disc, "REGISTRY_DB", tmp_path / "registry.db")
    monkeypatch.setattr(disc.urllib.request, "urlopen", _mock_urlopen(TAGS))
    result = disc.discover()
    assert result["ok"] and len(result["models"]) == 4
    models = {m["name"]: m for m in disc.list_models()}
    assert models["qwen2.5-coder:1.5b"]["tier"] == "FAST"
    assert models["llama3.3:70b"]["tier"] == "DEEP"
    assert models["qwen2.5-coder:7b"]["quantization"] == "Q4_K_M"
    # re-discovery updates, doesn't duplicate
    disc.discover()
    assert len(disc.list_models()) == 4


def test_unreachable_marks_down_never_crashes(tmp_path, monkeypatch):
    monkeypatch.setattr(disc, "REGISTRY_DB", tmp_path / "registry.db")
    monkeypatch.setattr(disc.urllib.request, "urlopen", _mock_urlopen(TAGS))
    disc.discover()
    assert len(disc.list_models(available_only=True)) == 4

    def dead(url, timeout=0):
        raise OSError("connection refused")
    monkeypatch.setattr(disc.urllib.request, "urlopen", dead)
    result = disc.discover()
    assert result["ok"] is False
    assert disc.list_models(available_only=True) == []
    assert len(disc.list_models(available_only=False)) == 4


def test_registry_candidates_by_tier(tmp_path, monkeypatch):
    monkeypatch.setattr(disc, "REGISTRY_DB", tmp_path / "registry.db")
    monkeypatch.setattr(disc.urllib.request, "urlopen", _mock_urlopen(TAGS))
    disc.discover()
    cands = disc.registry_candidates()
    assert ["ollama", "qwen2.5-coder:1.5b"] in cands["FAST"]
    assert ["ollama", "llama3.3:70b"] in cands["DEEP"]


def test_deep_fallback_uses_biggest_when_no_deep_model(tmp_path, monkeypatch):
    monkeypatch.setattr(disc, "REGISTRY_DB", tmp_path / "registry.db")
    small = {"models": [
        {"name": "a:3b", "details": {"parameter_size": "3B"}},
        {"name": "b:7b", "details": {"parameter_size": "7B"}},
    ]}
    monkeypatch.setattr(disc.urllib.request, "urlopen", _mock_urlopen(small))
    disc.discover()
    cands = disc.registry_candidates()
    assert cands["DEEP"] == [["ollama", "b:7b"]]
