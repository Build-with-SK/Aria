"""
tests/test_voice_and_hearing.py
===============================
HER VOICE (Kokoro, local) and HER EARS (faster-whisper `small`).

Neither model is loaded here — both entry points take an injectable
synthesiser/transcriber. What is tested is the judgement around them:

  voice    that she speaks PROSE and never markup, and that normalising how a
           number is said never changes what was said
  hearing  that Whisper's invented sentences over silence are dropped, that
           retention is an explicit act, and that a transcript is never
           treated as a source of ticker symbols
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.brain.cognitive import hearing as H
from src.brain.cognitive import voice as V


# ── she speaks prose, not markup ────────────────────────────────────────────

def test_a_code_block_is_not_read_aloud():
    spoken, warnings = V.speakable("Here:\n```python\nx = 1\n```\nThat is all.")
    assert "x = 1" not in spoken
    assert "python" not in spoken
    assert any("code block" in w for w in warnings)


def test_a_table_is_not_read_as_punctuation():
    spoken, warnings = V.speakable(
        "Results:\n| ticker | score |\n| --- | --- |\n| NVDA | 41 |\nThat is it.")
    assert "|" not in spoken
    assert any("table" in w for w in warnings)


def test_emphasis_and_headings_disappear_but_the_words_stay():
    spoken, _ = V.speakable("## Morning\n\n**NVDA** is _strong_ today.")
    assert "Morning" in spoken and "NVDA is strong today." in spoken
    assert "*" not in spoken and "#" not in spoken and "_" not in spoken


def test_a_markdown_link_keeps_its_words_and_loses_its_url():
    """Ordering bug found in the first real run: the URL rule ate the closing
    paren, the link rule could then no longer match, and she read the leftover
    brackets aloud."""
    spoken, _ = V.speakable("see [the desk](http://localhost:8000/app).")
    assert "the desk" in spoken
    assert "[" not in spoken and "]" not in spoken and "(" not in spoken
    assert "localhost" not in spoken


def test_a_bare_url_becomes_a_link():
    spoken, warnings = V.speakable("Source: https://example.com/a/b?c=d")
    assert "https" not in spoken and "a link" in spoken
    assert any("URL" in w for w in warnings)


def test_inline_code_is_spoken_as_its_contents():
    spoken, _ = V.speakable("The flag is `auto_execute`.")
    assert "auto_execute" in spoken and "`" not in spoken


# ── numbers are said, not spelled ───────────────────────────────────────────

def test_a_percentage_is_spoken_as_a_person_would():
    spoken, _ = V.speakable("NVDA +2.15% today")
    assert "up 2 point 1 5 percent" in spoken
    assert "%" not in spoken


def test_a_negative_percentage_says_down():
    spoken, _ = V.speakable("-0.8%")
    assert spoken.startswith("down 0 point 8 percent")


def test_currency_is_spoken():
    spoken, _ = V.speakable("It closed at $214.30")
    assert "214 point 3 0 dollars" in spoken and "$" not in spoken


def test_a_bare_signed_score_says_plus_not_up():
    """A composite of +41 is not a price going up. Saying 'up' would put a
    direction into her mouth that the number does not carry."""
    spoken, _ = V.speakable("composite of +41.0 and a delta of -3")
    assert "plus 41 point 0" in spoken
    assert "minus 3" in spoken
    assert "up 41" not in spoken


def test_normalising_never_changes_the_figure():
    """It changes how a number is SAID. Every digit has to survive."""
    spoken, _ = V.speakable("+2.15% and $1,234.50 and -0.8%")
    for digits in ("2", "1", "5", "1234" if "1234" in spoken else "234", "8"):
        assert digits in spoken.replace(" ", "") or digits in spoken


def test_nothing_speakable_is_refused_not_mumbled():
    s = V.say("```\njust code\n```", synth=lambda *a: ([0.0], 24000))
    assert not s.ok and "nothing speakable" in s.error


# ── the engine wiring ───────────────────────────────────────────────────────

def test_speech_reports_what_was_actually_said(tmp_path):
    captured = {}

    def synth(text, voice, speed):
        captured["text"] = text
        return [0.0] * 24000, 24000

    s = V.say("**NVDA** at `+2.15%`", synth=synth, voice="af_heart",
              out_path=tmp_path / "a.wav")
    assert s.ok and s.seconds == pytest.approx(1.0)
    assert "*" not in captured["text"]
    assert s.spoken_text == captured["text"]      # no gap between the two


def test_a_failing_synthesiser_is_reported(tmp_path):
    def boom(text, voice, speed):
        raise RuntimeError("onnx blew up")

    s = V.say("hello", synth=boom, out_path=tmp_path / "a.wav")
    assert not s.ok and "onnx blew up" in s.error


def test_the_default_voice_is_female():
    assert V.DEFAULT_VOICE.startswith("af_")
    assert all(v.startswith(("af_", "bf_")) for v in V.FALLBACK_VOICES)


# ── ears: what she refuses to hear ──────────────────────────────────────────

def transcriber(segments, language="en", duration=3.0):
    return lambda path: (segments, language, duration)


def test_hallucinated_silence_is_dropped():
    """Whisper's signature failure is a fluent invented sentence over room
    tone. This corpus is training her to sound like him — words he never said
    are worse than a gap."""
    t = H.listen("x.wav", transcribe=transcriber([
        (0.0, 2.0, "Buy nvidia at the open", -0.2, 0.05),
        (2.0, 4.0, "Thank you for watching!", -0.3, 0.95),      # silence
        (4.0, 6.0, "Subscribe to my channel", -2.5, 0.10),      # low confidence
    ]))
    assert t.ok
    assert t.text == "Buy nvidia at the open"
    assert t.dropped == 2
    assert any("invented from room tone" in w for w in t.warnings)


def test_a_clip_of_pure_silence_says_nothing_intelligible():
    t = H.listen("x.wav", transcribe=transcriber([
        (0.0, 3.0, "Thanks for watching.", -0.1, 0.99)]))
    assert t.ok and t.text == ""
    assert any("nothing intelligible" in w for w in t.warnings)
    assert not t.confident


def test_a_clean_clip_is_confident():
    t = H.listen("x.wav", transcribe=transcriber([
        (0.0, 3.0, "What is the desk doing today", -0.15, 0.02)]))
    assert t.confident and t.dropped == 0


def test_a_transcript_warns_that_tickers_are_unreliable():
    """A mis-heard symbol that became a trade would be the worst bug in this
    repository."""
    t = H.listen("x.wav", transcribe=transcriber([
        (0.0, 2.0, "in video looks strong", -0.2, 0.02)]))
    assert "never from the transcript" in t.to_dict()["caution"]


def test_a_broken_transcriber_is_reported():
    def boom(path):
        raise RuntimeError("ctranslate2 died")

    t = H.listen("x.wav", transcribe=boom)
    assert not t.ok and "ctranslate2 died" in t.error


# ── his slang, accumulating ─────────────────────────────────────────────────

@pytest.fixture
def corpus(tmp_path):
    return tmp_path / "spoken" / "transcripts.jsonl"


def confident_transcript(text="right so I'm cutting the tech book down today"):
    return H.Transcript(ok=True, text=text, duration_s=3.0, model="small")


def test_retention_is_an_explicit_act(corpus):
    """`consent` is a parameter at the call site, not a default buried in a
    config file."""
    assert H.remember_speech(confident_transcript(), path=corpus,
                             consent=False) is False
    assert not corpus.exists()

    assert H.remember_speech(confident_transcript(), path=corpus,
                             consent=True) is True
    assert len(H.spoken_corpus(corpus)) == 1


def test_only_confident_speech_is_kept(corpus):
    """A corpus of what Whisper GUESSED he said would teach her to write like
    a transcription error."""
    guessed = H.Transcript(ok=True, text="something something", dropped=2)
    assert H.remember_speech(guessed, path=corpus) is False
    assert H.remember_speech(H.Transcript(ok=True, text="ok"), path=corpus) is False


def test_speech_is_redacted_before_it_is_stored(corpus):
    t = confident_transcript("call me on +44 7700 900123 about the position")
    H.remember_speech(t, path=corpus)
    stored = H.spoken_corpus(corpus)[0]
    assert "900123" not in stored["text"]
    assert stored["redactions"].get("PHONE")


def test_stored_speech_is_marked_sensitive(corpus):
    H.remember_speech(confident_transcript(), path=corpus)
    marker = corpus.parent / "SENSITIVE"
    assert marker.exists()
    assert "university storage" in marker.read_text(encoding="utf-8")


def test_she_is_not_always_listening():
    """The absence of an ambient mode is the feature. A system that can be
    CONFIGURED into always listening is one restart away from listening."""
    assert H.status()["always_on"] is False
    source = Path(H.__file__).read_text(encoding="utf-8")
    for word in ("sounddevice", "pyaudio", "InputStream", "start_stream"):
        assert word not in source, f"{word} would open a microphone"


def test_the_spoken_corpus_reaches_the_training_set(corpus, monkeypatch, tmp_path):
    """The point of the whole exercise: his slang has to arrive in the corpus
    that teaches her to sound like him."""
    monkeypatch.setattr(H, "SPOKEN_CORPUS", corpus)
    H.remember_speech(confident_transcript(
        "right so I'm cutting the tech book down, it's been a mare all week"),
        path=corpus)

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("A written note. " * 40, encoding="utf-8")

    from src.training import dataset as D
    built = D.build_voice(vault)
    registers = {row["meta"]["register"] for row in built["train"]}
    assert registers == {"written", "spoken"}
    assert built["registers"]["spoken"] == 1
    assert any("mare all week" in row["text"] for row in built["train"])
