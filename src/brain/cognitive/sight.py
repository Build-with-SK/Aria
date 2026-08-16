"""
src/brain/cognitive/sight.py
============================
SIGHT — she can look at something.

Not to be confused with `src/research/eye.py`, which is the reading eye: it
watches sources, corroborates them and files observations. This is the other
kind of seeing. Until now every input she had was a number somebody had
already computed for her. `perception.py` says it plainly in its own
docstring — "reads all available data files". She had never looked at
anything.

WHAT THIS IS FOR, AND WHAT IT IS NOT
------------------------------------
It is for the things that only exist as pictures: a chart somebody sends her,
a screenshot of a broker screen, a page of a filing that is an image of a
table rather than a table. She already reads market data numerically and
should keep doing so — a model squinting at a candlestick chart to guess a
price it could have looked up is worse in every way than the lookup. So:

    NEVER use sight to read a number she can fetch.
    Use it for what the picture shows that the feed does not.

That rule is not a style preference, and it is not a guess. Measured on this
machine, 2026-08-12, gemma3:4b shown a generated 120-session line chart with a
deliberate breakdown in the last third:

    what it got RIGHT   the title, the axis labels, the shape, and that the
                        last third fell — ending "approximately 82" against a
                        true 82.4
    what it got WRONG   the start of that decline: "around 93" against a true
                        97.6; the y-axis range: "approximately 50 to 100"
                        against a true 81.9 to 101.1; and WHICH LINE was the
                        price — it read the orange 20-day average as the price
                        and never mentioned the black price line

So the narrative is worth having and the numbers are not, and the model gives
no sign of which is which — every one of those errors arrived in the same
even, confident register. Everything downstream treats her prose as citable.
That is why a sighting is labelled as a SIGHTING everywhere it travels, and
why `Sighting.citation` says the model looked at a picture rather than that
she knows.

HONESTY ABOUT WHAT SHE SAW
--------------------------
Three failure modes, handled rather than hoped about:

  - No vision-capable model installed. She says so. She does not describe the
    image from its filename, which is what a text model handed an image
    silently does.
  - The image is unreadable or absurdly large. Refused before it reaches the
    model, with the reason.
  - The model hedges or refuses. Reported as an inconclusive sighting, not
    smoothed into a confident description.
"""
from __future__ import annotations

import base64
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from json import loads as _json_loads
from pathlib import Path

logger = logging.getLogger(__name__)

#: Ollama accepts base64 images on any vision model. Bigger than this and the
#: encode alone becomes the slow part, and a 20 MB screenshot is a mistake
#: rather than a request.
MAX_IMAGE_BYTES = 8 * 1024 * 1024

SUPPORTED = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")

#: Sight runs on whatever vision model is installed. Preference order, first
#: one present wins.
PREFERRED_MODELS = ("qwen2.5vl:7b", "llava:13b", "llava:7b", "gemma3:4b",
                    "llama3.2-vision:11b", "minicpm-v")

_SYSTEM = (
    "You are looking at an image on behalf of a trading research system.\n"
    "Describe ONLY what is visibly present. State plainly when something is "
    "unreadable, cut off, or too small to be sure of.\n"
    "Do not infer prices, dates or values that are not legible, and do not "
    "fill in what a chart like this usually shows. If you cannot tell, say you "
    "cannot tell — that answer is useful and a confident wrong one is not."
)


@dataclass
class Sighting:
    """What she saw, and how much it is worth."""
    ok: bool = False
    description: str = ""
    model: str = ""
    source: str = ""
    at: str = ""
    elapsed_ms: int = 0
    inconclusive: bool = False
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def citation(self) -> str:
        """How a sighting is allowed to be quoted downstream.

        Never "AAPL is at 214.30". Always "a vision model looked at this
        image and reported …", so a reader can weigh it as what it is.
        """
        if not self.ok:
            return f"nothing was seen ({self.error or 'no sighting'})"
        return (f"{self.model} looked at {self.source or 'an image'} at "
                f"{self.at} and described what was visible; a description of a "
                f"picture, not a measurement")

    def to_dict(self) -> dict:
        return {"ok": self.ok, "description": self.description,
                "model": self.model, "source": self.source, "at": self.at,
                "elapsed_ms": self.elapsed_ms,
                "inconclusive": self.inconclusive, "error": self.error,
                "warnings": self.warnings, "citation": self.citation,
                "kind": "sighting"}


# ── which model can actually see ─────────────────────────────────────────────

def vision_models(timeout: float = 5.0) -> list[str]:
    """Installed models that report the `vision` capability.

    Asked of Ollama rather than assumed from the name: `gemma3:4b` sees and
    `qwen2.5-coder:7b` does not, and nothing about either name says so.
    """
    from src.inference.base import ollama_base
    base = ollama_base()
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=timeout) as r:
            names = [m["name"] for m in _json_loads(r.read()).get("models", [])]
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        logger.warning(f"sight: cannot list models at {base}: {e}")
        return []

    seeing = []
    for name in names:
        try:
            req = urllib.request.Request(
                f"{base}/api/show",
                data=f'{{"name": "{name}"}}'.encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                info = _json_loads(r.read())
            if "vision" in (info.get("capabilities") or []):
                seeing.append(name)
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return seeing


def pick_model(available: list[str] | None = None) -> str:
    """The best installed model that can see, or '' if she is blind."""
    available = vision_models() if available is None else available
    if not available:
        return ""
    for preferred in PREFERRED_MODELS:
        for name in available:
            if name == preferred or name.startswith(preferred.split(":")[0] + ":"):
                return name
    return available[0]


# ── looking ──────────────────────────────────────────────────────────────────

def encode(path: Path) -> str:
    """An image file as base64, refused rather than truncated when wrong."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no image at {path}")
    if path.suffix.lower() not in SUPPORTED:
        raise ValueError(f"{path.suffix or 'no extension'} is not an image "
                         f"format she can look at ({', '.join(SUPPORTED)})")
    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise ValueError(f"{size / 1e6:.1f} MB is past the {MAX_IMAGE_BYTES / 1e6:.0f} MB "
                         f"limit — resize it rather than sending it")
    if size == 0:
        raise ValueError("the file is empty")
    return base64.b64encode(path.read_bytes()).decode()


_HEDGES = ("i cannot", "i can't", "unable to", "no image", "not able to see",
           "i don't see", "cannot determine", "too blurry", "unreadable",
           "as an ai")


def look(image: str | Path | bytes, question: str = "", *,
         model: str = "", max_tokens: int = 400, timeout: float = 120.0,
         complete=None) -> Sighting:
    """Look at one image and say what is visibly there.

    `question` narrows it — "what timeframe is this chart" beats "describe
    this". `complete` is injectable so the wiring can be tested without a
    model.
    """
    import time

    s = Sighting(at=datetime.now().isoformat(timespec="seconds"))
    try:
        if isinstance(image, bytes):
            encoded, s.source = base64.b64encode(image).decode(), "an image in memory"
        else:
            encoded, s.source = encode(Path(image)), str(image)
    except (FileNotFoundError, ValueError, OSError) as e:
        s.error = str(e)
        return s

    s.model = model or pick_model()
    if not s.model:
        s.error = ("no vision-capable model is installed, so she cannot look "
                   "at this. `ollama pull gemma3:4b` gives her sight; until "
                   "then she is not guessing from the filename.")
        return s

    prompt = (question.strip() or
              "Describe what is visible in this image, and say what you cannot "
              "make out.")
    t0 = time.time()
    try:
        if complete is None:
            from src.inference.router import get_router
            text = get_router().complete_with(
                "ollama", s.model,
                [{"role": "user", "content": prompt, "images": [encoded]}],
                system=_SYSTEM, max_tokens=max_tokens, temperature=0.2,
                timeout=timeout).text
        else:
            text = complete(s.model, prompt, encoded)
    except Exception as e:
        s.elapsed_ms = int((time.time() - t0) * 1000)
        s.error = f"the sighting failed: {e}"
        return s

    s.elapsed_ms = int((time.time() - t0) * 1000)
    s.description = (text or "").strip()
    if not s.description:
        s.error = "the model returned nothing"
        return s

    s.ok = True
    lowered = s.description.lower()
    if any(h in lowered for h in _HEDGES):
        s.inconclusive = True
        s.warnings.append(
            "the model hedged or said it could not make something out — this "
            "is an inconclusive sighting, and smoothing it into a confident "
            "description is how a guess becomes a citation")
    return s


def status() -> dict:
    """Can she see at all, and with what?"""
    available = vision_models()
    chosen = pick_model(available)
    return {
        "can_see": bool(chosen),
        "model": chosen,
        "vision_models": available,
        "note": ("She can look at charts, screenshots and scanned pages. She "
                 "still reads market data numerically — a model squinting at a "
                 "candlestick to guess a price it could look up is worse in "
                 "every way than the lookup."
                 if chosen else
                 "No vision-capable model is installed; she is blind to images "
                 "and says so rather than describing them from context."),
    }
