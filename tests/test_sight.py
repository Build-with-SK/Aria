"""
tests/test_sight.py
===================
SIGHT — she can look at something (src/brain/cognitive/sight.py).

The tests that matter are not "can it call the model". They are the ones that
keep a picture from becoming a fact: a sighting has to stay labelled as a
sighting, a blind machine has to say it is blind rather than describe an
image from its filename, and a hedging model has to come back marked
inconclusive rather than smoothed into confident prose.

No model is called here — `look()` takes an injectable `complete`.
"""
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.brain.cognitive import sight as S

PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@pytest.fixture
def image(tmp_path):
    p = tmp_path / "chart.png"
    p.write_bytes(PNG)
    return p


def seeing(text: str):
    """A stand-in vision model that replies with `text`."""
    return lambda model, prompt, encoded: text


# ── a sighting stays a sighting ─────────────────────────────────────────────

def test_a_sighting_is_never_quoted_as_a_measurement(image):
    """Measured on a real chart: the model read the moving average as the
    price and the y-axis floor as 50 when it was 82 — in the same confident
    register as everything it got right. The citation is what stops that
    becoming a number in a report."""
    s = S.look(image, model="gemma3:4b",
               complete=seeing("The price is 214.30 and rising."))
    assert s.ok
    assert "not a measurement" in s.citation
    assert "looked at" in s.citation
    assert s.to_dict()["kind"] == "sighting"


def test_a_failed_sighting_cites_nothing(image):
    s = S.look(image / "missing.png", model="m", complete=seeing("x"))
    assert not s.ok
    assert s.citation.startswith("nothing was seen")


# ── blind is said out loud ──────────────────────────────────────────────────

def test_with_no_vision_model_she_says_she_cannot_look(image, monkeypatch):
    """A text model handed an image describes it from the filename. She
    refuses instead."""
    monkeypatch.setattr(S, "vision_models", lambda *a, **kw: [])
    s = S.look(image)
    assert not s.ok
    assert "no vision-capable model" in s.error
    assert "ollama pull" in s.error          # and says how to fix it
    assert s.description == ""


def test_status_reports_blindness_plainly(monkeypatch):
    monkeypatch.setattr(S, "vision_models", lambda *a, **kw: [])
    st = S.status()
    assert st["can_see"] is False
    assert "blind to images" in st["note"]


def test_status_reports_sight_with_the_model_named(monkeypatch):
    monkeypatch.setattr(S, "vision_models", lambda *a, **kw: ["gemma3:4b"])
    st = S.status()
    assert st["can_see"] is True and st["model"] == "gemma3:4b"


# ── hedging is not smoothed over ────────────────────────────────────────────

@pytest.mark.parametrize("reply", [
    "I cannot make out the axis labels in this image.",
    "The chart is too blurry to read the values.",
    "I don't see any text I can resolve here.",
])
def test_a_hedging_model_is_marked_inconclusive(image, reply):
    s = S.look(image, model="m", complete=seeing(reply))
    assert s.ok and s.inconclusive
    assert any("guess becomes a citation" in w for w in s.warnings)


def test_a_clear_description_is_not_flagged(image):
    """A warning that fires on every sighting is a warning nobody reads."""
    s = S.look(image, model="m", complete=seeing(
        "A line chart titled SYN, 120 sessions, falling in the last third."))
    assert s.ok and not s.inconclusive and s.warnings == []


def test_an_empty_reply_is_not_a_sighting(image):
    s = S.look(image, model="m", complete=seeing("   "))
    assert not s.ok and "returned nothing" in s.error


def test_a_model_that_raises_is_reported_not_swallowed(image):
    def boom(model, prompt, encoded):
        raise RuntimeError("ollama died")

    s = S.look(image, model="m", complete=boom)
    assert not s.ok and "ollama died" in s.error


# ── what she will and will not open ─────────────────────────────────────────

def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        S.encode(tmp_path / "nope.png")


def test_a_non_image_is_refused(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("this is not a chart", encoding="utf-8")
    with pytest.raises(ValueError, match="not an image format"):
        S.encode(p)


def test_an_empty_file_is_refused(tmp_path):
    p = tmp_path / "empty.png"
    p.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        S.encode(p)


def test_an_oversized_image_is_refused_before_the_model(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "MAX_IMAGE_BYTES", 10)
    p = tmp_path / "huge.png"
    p.write_bytes(PNG)
    with pytest.raises(ValueError, match="resize it"):
        S.encode(p)


def test_bytes_can_be_looked_at_directly():
    s = S.look(PNG, model="m", complete=seeing("a one-pixel image"))
    assert s.ok and s.source == "an image in memory"


def test_encoding_round_trips(image):
    assert base64.b64decode(S.encode(image)) == PNG


# ── choosing a model ────────────────────────────────────────────────────────

def test_the_better_vision_model_is_preferred():
    assert S.pick_model(["gemma3:4b", "llava:13b"]) == "llava:13b"


def test_an_unknown_vision_model_is_still_used():
    """Capability comes from Ollama, not from a list of names this file
    happens to know."""
    assert S.pick_model(["some-new-vlm:8b"]) == "some-new-vlm:8b"


def test_no_models_means_no_choice():
    assert S.pick_model([]) == ""


def test_the_question_reaches_the_model(image):
    seen = {}

    def capture(model, prompt, encoded):
        seen["prompt"] = prompt
        return "ok"

    S.look(image, "What timeframe is this chart?", model="m", complete=capture)
    assert seen["prompt"] == "What timeframe is this chart?"


def test_a_missing_question_still_asks_something(image):
    seen = {}

    def capture(model, prompt, encoded):
        seen["prompt"] = prompt
        return "ok"

    S.look(image, model="m", complete=capture)
    assert "cannot make out" in seen["prompt"]     # asks for the gaps too
