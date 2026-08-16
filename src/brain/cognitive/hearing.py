"""
src/brain/cognitive/hearing.py
==============================
EARS — faster-whisper, local, `small`.

TWO JOBS, AND THE SECOND IS THE INTERESTING ONE
-----------------------------------------------
1. Hear what he says, so he can talk to her instead of typing.
2. Keep what he said, so she learns HOW HE TALKS.

The vault gives her his written register: considered, edited, punctuated. It
does not contain his slang, his shorthand, the way he actually phrases a
thought out loud. That only exists in speech, it accumulates at the speed of
him using her, and it is the difference between a model that writes like a
report and one that sounds like him. Transcripts therefore accumulate into a
spoken corpus that `src/training/dataset.py` reads alongside the vault.

NOT ALWAYS ON. EVER.
--------------------
The microphone opens on an explicit act and closes when that act ends. There
is no ambient mode in this file and no configuration flag that would create
one — the absence is the feature. A hot mic on a machine holding his vault and
other people's sign-in data is a large standing risk for a small convenience,
and a system that can be *configured* into always listening is one restart
away from listening.

Every retained transcript is his personal speech: the corpus is marked as
personal data, redacted the same way the vault is, and an adapter trained on
it is as sensitive as a recording of him.

WHAT IT WILL GET WRONG
----------------------
Tickers. `small` hears "NVDA" as "in video" and "ARM" as "arm" — routinely,
not occasionally. So a transcript is TEXT FOR THE RESEARCH PATH and never a
source of symbols: ticker extraction stays with the existing resolver, which
works from a symbol table rather than from phonemes. Nothing here may reach an
order. A mis-heard symbol that became a trade would be the worst bug in this
repository.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent.parent
MODEL_DIR = ROOT / "data" / "models" / "whisper"

#: His spoken register, accumulating. Read by src/training/dataset.py.
SPOKEN_CORPUS = ROOT / "data" / "training" / "spoken" / "transcripts.jsonl"

#: `small` was chosen deliberately: `base` mangles accents and proper nouns,
#: `large-v3-turbo` is 1.6 GB competing with a 7B model for 8 GB of VRAM.
DEFAULT_MODEL = "small"

#: int8 on CPU keeps the GPU free for her reasoning. A press-to-talk clip is a
#: few seconds; there is nothing to gain by making her brain wait for it.
DEFAULT_DEVICE = "cpu"
DEFAULT_COMPUTE = "int8"

#: Whisper hallucinates fluent sentences from silence — this is its
#: best-known failure. Segments below this average log-probability are dropped
#: rather than shown, because a confident sentence invented from room tone is
#: worse than a gap.
MIN_SEGMENT_LOGPROB = -1.0

#: Above this, the model itself believes the audio was silence.
MAX_NO_SPEECH_PROB = 0.6

_model = None
_model_name = ""


@dataclass
class Segment:
    start: float
    end: float
    text: str
    logprob: float = 0.0
    no_speech: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Transcript:
    ok: bool = False
    text: str = ""
    language: str = ""
    duration_s: float = 0.0
    elapsed_ms: int = 0
    model: str = ""
    segments: list[Segment] = field(default_factory=list)
    dropped: int = 0
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def confident(self) -> bool:
        return self.ok and bool(self.text.strip()) and not self.dropped

    def to_dict(self) -> dict:
        return {"ok": self.ok, "text": self.text, "language": self.language,
                "duration_s": round(self.duration_s, 2),
                "elapsed_ms": self.elapsed_ms, "model": self.model,
                "segments": [s.to_dict() for s in self.segments],
                "dropped": self.dropped, "confident": self.confident,
                "error": self.error, "warnings": self.warnings,
                "kind": "transcript",
                "caution": ("Heard, not read. Ticker symbols in speech are "
                            "unreliable at this model size and must be resolved "
                            "from the symbol table, never from the transcript.")}


def available() -> bool:
    import importlib.util
    return importlib.util.find_spec("faster_whisper") is not None


def load(model_name: str = DEFAULT_MODEL):
    """Load once and keep it. Loading `small` takes seconds; doing it per clip
    would make her feel slow for a reason that has nothing to do with her."""
    global _model, _model_name
    if _model is not None and _model_name == model_name:
        return _model
    from faster_whisper import WhisperModel
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    _model = WhisperModel(model_name, device=DEFAULT_DEVICE,
                          compute_type=DEFAULT_COMPUTE,
                          download_root=str(MODEL_DIR))
    _model_name = model_name
    logger.info("hearing: whisper %s loaded (%s/%s)", model_name,
                DEFAULT_DEVICE, DEFAULT_COMPUTE)
    return _model


def listen(audio: str | Path, *, model_name: str = DEFAULT_MODEL,
           language: str | None = "en", transcribe=None) -> Transcript:
    """Transcribe one clip. `transcribe` is injectable for testing."""
    import time

    t = Transcript(model=model_name)
    path = Path(audio)
    if transcribe is None:
        if not available():
            t.error = ("faster-whisper is not installed, so she cannot hear. "
                       "`pip install faster-whisper`.")
            return t
        if not path.exists():
            t.error = f"no audio at {path}"
            return t
        if path.stat().st_size == 0:
            t.error = "the audio file is empty"
            return t

    t0 = time.time()
    try:
        if transcribe is None:
            segments, info = load(model_name).transcribe(
                str(path), language=language, vad_filter=True,
                beam_size=5, condition_on_previous_text=False)
            raw = [(s.start, s.end, s.text, getattr(s, "avg_logprob", 0.0),
                    getattr(s, "no_speech_prob", 0.0)) for s in segments]
            t.language = getattr(info, "language", "") or ""
            t.duration_s = float(getattr(info, "duration", 0.0) or 0.0)
        else:
            raw, t.language, t.duration_s = transcribe(path)
    except Exception as e:
        t.elapsed_ms = int((time.time() - t0) * 1000)
        t.error = f"hearing failed: {e}"
        return t

    kept = []
    for start, end, text, logprob, no_speech in raw:
        # Whisper's signature failure: fluent invented sentences over silence.
        # Dropping them costs a few real words; keeping them puts words in his
        # mouth, and this corpus is training her to sound like him.
        if logprob < MIN_SEGMENT_LOGPROB or no_speech > MAX_NO_SPEECH_PROB:
            t.dropped += 1
            continue
        cleaned = (text or "").strip()
        if cleaned:
            kept.append(Segment(start=float(start), end=float(end),
                                text=cleaned, logprob=float(logprob),
                                no_speech=float(no_speech)))

    t.segments = kept
    t.text = " ".join(s.text for s in kept).strip()
    t.elapsed_ms = int((time.time() - t0) * 1000)
    t.ok = True
    if t.dropped:
        t.warnings.append(
            f"{t.dropped} segment(s) dropped as probable silence or "
            f"hallucination — a confident sentence invented from room tone is "
            f"worse than a gap")
    if not t.text:
        t.warnings.append("nothing intelligible was heard")
    return t


# ── his spoken register, accumulating ────────────────────────────────────────

def remember_speech(transcript: Transcript, *, path: Path | None = None,
                    context: str = "", consent: bool = True) -> bool:
    """Keep what he said, so she can learn how he says things.

    `consent` is a parameter and not a default buried in a config file: the
    caller has to state, at the call site, that this speech may be retained.
    Passing False transcribes and forgets.

    Only confident text is kept. A corpus of what Whisper GUESSED he said
    would teach her to write like a transcription error.
    """
    if not consent:
        return False
    if not transcript.confident or len(transcript.text.strip()) < 8:
        return False

    from src.training.dataset import deidentify, owner_terms
    clean, redactions = deidentify(transcript.text, owner_terms())

    # Resolved at CALL time, not bound at def time. A module-level default in
    # the signature is captured once at import and can never afterwards be
    # redirected — not by a test, not by a config, not by a future move of the
    # data directory.
    path = Path(path or SPOKEN_CORPUS)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": datetime.now().isoformat(timespec="seconds"),
           "text": clean, "context": context,
           "duration_s": round(transcript.duration_s, 2),
           "model": transcript.model, "redactions": redactions}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    marker = path.parent / "SENSITIVE"
    if not marker.exists():
        marker.write_text(
            "Transcripts of the owner speaking, de-identified but not "
            "anonymised.\n\n"
            "This is his voice and his slang. Anything trained on it carries "
            "them in its weights. Do not copy this directory, or any adapter "
            "trained from it, onto shared or university storage.\n",
            encoding="utf-8")
    return True


def spoken_corpus(path: Path | None = None) -> list[dict]:
    path = Path(path or SPOKEN_CORPUS)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def status(path: Path | None = None) -> dict:
    rows = spoken_corpus(path)
    words = sum(len((r.get("text") or "").split()) for r in rows)
    return {
        "can_hear": available(),
        "model": DEFAULT_MODEL,
        "device": f"{DEFAULT_DEVICE}/{DEFAULT_COMPUTE}",
        "always_on": False,
        "spoken_corpus": {
            "clips": len(rows),
            "words": words,
            "since": rows[0].get("at") if rows else None,
            "note": (f"{words} words of his own speech. This is the register "
                     f"the vault does not contain — slang, shorthand, how he "
                     f"actually phrases a thought. It accumulates only when he "
                     f"speaks to her."),
        },
        "note": ("She hears when spoken to and not otherwise. There is no "
                 "ambient mode in this build and no setting that creates one."
                 if available() else
                 "faster-whisper is not installed; she cannot hear."),
    }
