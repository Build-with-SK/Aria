"""
src/brain/cognitive/voice.py
============================
HER VOICE — Kokoro-82M, ONNX, local.

The owner's objection was exact: a computer voice. That is `speechSynthesis`
in the browser, which on Windows is a SAPI voice from a decade ago, and no
amount of rate and pitch tuning fixes it — it is a formant synthesiser reading
characters. Kokoro is a neural model. It is 82M parameters, Apache-2.0, runs
on the onnxruntime already installed, and sounds like a person.

Local, for the same reason her reasoning is local: everything she says stays
on this machine. There is no per-character bill and no network in the path, so
she can speak as much as she likes, offline, at three in the morning.

WHAT SHE SPEAKS, AND WHAT SHE DOES NOT
--------------------------------------
Never markup, never JSON, never a table read aloud as punctuation. That was
already the rule in the voice notes and it is enforced here in `speakable()`
rather than left to whoever writes the next prompt: a spoken briefing is a
different artefact from a written one, and a model that reads "asterisk
asterisk NVDA asterisk asterisk colon" has not been given a voice, it has been
given a screen reader.

Numbers are the other half. "+2.15%" read as a string of characters is
unlistenable; spoken as "up two point one five percent" it is a sentence. The
substitutions are deliberately small and reversible — this normalises how
something is SAID, it never changes what was said.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent.parent
MODEL_DIR = ROOT / "data" / "models" / "kokoro"
MODEL_FILE = MODEL_DIR / "kokoro-v1.0.onnx"
VOICES_FILE = MODEL_DIR / "voices-v1.0.bin"
AUDIO_DIR = ROOT / "data" / "audio"

#: A warm, level female voice — the register the owner asked for, and the one
#: that suits a research assistant reading a briefing rather than performing.
DEFAULT_VOICE = "af_heart"
FALLBACK_VOICES = ("af_bella", "af_nicole", "af_sarah", "af_sky")

DEFAULT_SPEED = 1.0
SAMPLE_RATE = 24000

_engine = None


@dataclass
class Speech:
    ok: bool = False
    voice: str = ""
    path: str = ""
    name: str = ""          # the filename the browser fetches from /api/aria/audio
    seconds: float = 0.0
    elapsed_ms: int = 0
    spoken_text: str = ""
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "voice": self.voice, "path": self.path,
                "name": self.name,
                "seconds": round(self.seconds, 2), "elapsed_ms": self.elapsed_ms,
                "spoken_text": self.spoken_text, "error": self.error,
                "warnings": self.warnings, "sample_rate": SAMPLE_RATE}


# ── what a voice may say ─────────────────────────────────────────────────────

_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_EMPHASIS = re.compile(r"(\*{1,3}|_{1,3})(.+?)\1")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_MD_BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_URL = re.compile(r"https?://\S+")
_MULTISPACE = re.compile(r"[ \t]{2,}")
_MULTINEWLINE = re.compile(r"\n{3,}")

_PERCENT = re.compile(r"([+-]?)(\d+(?:\.\d+)?)\s*%")
_CURRENCY = re.compile(r"\$\s*(\d+(?:[.,]\d+)?)")
#: A bare signed number — a composite score, an R multiple. "plus", not "up":
#: a score of +41 is not a price going up, and saying so would put a direction
#: into her mouth that the number does not carry.
_SIGNED = re.compile(r"(?<![\w.])([+-])(\d+(?:\.\d+)?)(?![\w%])")


def _say_number(sign: str, number: str, unit: str) -> str:
    lead = {"+": "up ", "-": "down "}.get(sign, "")
    if "." in number:
        whole, frac = number.split(".", 1)
        spoken = f"{whole} point {' '.join(frac)}"
    else:
        spoken = number
    return f"{lead}{spoken} {unit}"


def speakable(text: str) -> tuple[str, list[str]]:
    """Prose, from something that may be markup.

    Returns the text to speak and a note of what was removed, so a caller can
    tell the difference between "she said less" and "she said it differently".
    """
    warnings = []
    out = text or ""

    if _CODE_FENCE.search(out):
        out = _CODE_FENCE.sub(" ", out)
        warnings.append("a code block was not read aloud")
    if _TABLE_ROW.search(out):
        out = _TABLE_ROW.sub(" ", out)
        warnings.append("a table was not read aloud — say the numbers that "
                        "matter instead of reading a grid")
    # Links BEFORE bare URLs. The other way round, the URL rule consumes the
    # `)` that closes a markdown link, the link rule can then no longer match,
    # and she reads the leftover brackets aloud — which is precisely the
    # screen-reader failure this function exists to prevent.
    out = _MD_LINK.sub(r"\1", out)
    if _URL.search(out):
        out = _URL.sub("a link", out)
        warnings.append("a URL was replaced with 'a link'")

    out = _INLINE_CODE.sub(r"\1", out)
    out = _MD_EMPHASIS.sub(r"\2", out)
    out = _MD_HEADING.sub("", out)
    out = _MD_BULLET.sub("", out)

    # Numbers as a person would say them.
    out = _PERCENT.sub(lambda m: _say_number(m.group(1), m.group(2), "percent"), out)
    out = _CURRENCY.sub(lambda m: _say_number("", m.group(1).replace(",", ""),
                                              "dollars"), out)
    out = _SIGNED.sub(
        lambda m: ("plus " if m.group(1) == "+" else "minus ")
        + _say_number("", m.group(2), "").strip(), out)

    out = out.replace("&", " and ").replace("—", ", ").replace("–", ", ")
    out = _MULTISPACE.sub(" ", out)
    out = _MULTINEWLINE.sub("\n\n", out)
    return out.strip(), warnings


# ── the engine ───────────────────────────────────────────────────────────────

def installed() -> bool:
    import importlib.util
    return importlib.util.find_spec("kokoro_onnx") is not None


def weights_present() -> bool:
    return MODEL_FILE.exists() and VOICES_FILE.exists()


def load():
    """Load the model once and keep it."""
    global _engine
    if _engine is not None:
        return _engine
    from kokoro_onnx import Kokoro
    _engine = Kokoro(str(MODEL_FILE), str(VOICES_FILE))
    logger.info("voice: kokoro loaded from %s", MODEL_FILE.name)
    return _engine


def voices() -> list[str]:
    try:
        engine = load()
    except Exception as e:
        logger.warning("voice: cannot list voices: %s", e)
        return []
    for attr in ("get_voices", "voices"):
        value = getattr(engine, attr, None)
        try:
            names = value() if callable(value) else value
        except Exception:
            continue
        if names:
            return sorted(names)
    return []


def pick_voice(preferred: str = DEFAULT_VOICE) -> str:
    available = voices()
    if not available:
        return preferred
    if preferred in available:
        return preferred
    for name in FALLBACK_VOICES:
        if name in available:
            return name
    female = [v for v in available if v.startswith(("af_", "bf_"))]
    return (female or available)[0]


def say(text: str, *, voice: str = DEFAULT_VOICE, speed: float = DEFAULT_SPEED,
        out_path: Path | None = None, synth=None) -> Speech:
    """Speak. Writes a wav and returns where it is.

    `synth` is injectable so the wiring is testable without loading 250 MB of
    weights in a unit test.
    """
    import time

    s = Speech()
    spoken, warnings = speakable(text)
    s.spoken_text, s.warnings = spoken, warnings
    if not spoken:
        s.error = "there is nothing speakable in that text"
        return s

    if synth is None:
        if not installed():
            s.error = ("kokoro-onnx is not installed. `pip install "
                       "kokoro-onnx`.")
            return s
        if not weights_present():
            s.error = (f"the Kokoro weights are not in {MODEL_DIR}. Download "
                       f"kokoro-v1.0.onnx and voices-v1.0.bin from the "
                       f"kokoro-onnx releases.")
            return s

    s.voice = pick_voice(voice) if synth is None else voice
    t0 = time.time()
    try:
        if synth is None:
            samples, sample_rate = load().create(spoken, voice=s.voice,
                                                 speed=speed, lang="en-us")
        else:
            samples, sample_rate = synth(spoken, s.voice, speed)
    except Exception as e:
        s.elapsed_ms = int((time.time() - t0) * 1000)
        s.error = f"she could not speak: {e}"
        return s

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    path = Path(out_path) if out_path else (
        AUDIO_DIR / f"aria-{int(time.time() * 1000)}.wav")
    try:
        import soundfile as sf
        sf.write(str(path), samples, sample_rate)
    except Exception as e:
        s.elapsed_ms = int((time.time() - t0) * 1000)
        s.error = f"the audio could not be written: {e}"
        return s

    s.elapsed_ms = int((time.time() - t0) * 1000)
    s.seconds = len(samples) / float(sample_rate or SAMPLE_RATE)
    s.path = str(path)
    s.name = path.name
    s.ok = True
    prune()
    return s


#: Clips are transient — the browser fetches one and never asks again. Left
#: alone, a talkative morning fills the disk with wavs nobody will play twice.
KEEP_CLIPS = 40


def prune(keep: int = KEEP_CLIPS, directory: Path | None = None) -> int:
    """Delete all but the newest `keep` generated clips. Returns how many
    went. Only ever touches `aria-*.wav` in the audio directory — a glob that
    also matched, say, a recording the owner had put there would be a data
    loss bug wearing a housekeeping label."""
    directory = Path(directory or AUDIO_DIR)
    if not directory.exists():
        return 0
    clips = sorted(directory.glob("aria-*.wav"), key=lambda p: p.stat().st_mtime)
    removed = 0
    for old in clips[:-keep] if keep > 0 else clips:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def status() -> dict:
    ready = installed() and weights_present()
    return {
        "can_speak": ready,
        "engine": "kokoro-82M (onnx, local)",
        "voice": pick_voice() if ready else DEFAULT_VOICE,
        "voices": voices() if ready else [],
        "model_present": weights_present(),
        "package_installed": installed(),
        "note": ("A neural voice running on this machine. Nothing she says "
                 "leaves it, there is no per-word cost, and she can speak "
                 "offline."
                 if ready else
                 "Not ready — install kokoro-onnx and place the weights in "
                 f"{MODEL_DIR}."),
    }
