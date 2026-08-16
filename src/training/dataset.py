"""
src/training/dataset.py
=======================
THE THREE CORPORA (docs/ARIA_NEXT_SESSION.md step 5).

  outcomes — every resolved prediction as a supervised row: the module feature
             vector in, the REALISED outcome out. Nobody else has this
             dataset; it is produced by ARIA simply running.
  voice    — the owner's vault, de-identified, so she writes like him.
  domain   — filings, papers, textbooks, sources retained so citations stay real.

THE STATE OF THIS FILE TODAY
----------------------------
`outcomes` is EMPTY. Zero predictions have resolved (invariant 5), so
`build_outcomes()` refuses and says so with the count it wanted and the count
it has. It starts working on its own the day the paper loop has accumulated
the sample — roughly twenty directional calls a month. Writing it now means
the first training set is built the day the data arrives, not the day somebody
remembers this was a to-do.

THE RULES, AND WHY EACH ONE IS HERE
-----------------------------------
1. **Chronological, never shuffled.** A random split lets the model learn from
   next month to trade last month. `chronological_split()` cuts on a DATE
   boundary, so no single market day straddles the split — forty tickers on
   one date are one observation of one day, and the walk-forward harness had
   to learn that the expensive way.

2. **The label is the realised outcome, never the system's own past
   prediction.** `training_export()` carries both. Training on
   `predicted_direction` would teach her to reproduce the system's opinions,
   including its wrong ones, and would score beautifully while learning
   nothing. `_LABEL_ONLY` enforces which field may be a target.

3. **Outcomes are never synthesised.** No augmentation, no resampling of
   labels, no "plausible" rows to reach a threshold. If the count is short,
   the answer is to wait.

4. **The voice corpus is personal data and stays that way.** A LoRA trained on
   the vault has the vault in its weights: the adapter is as sensitive as the
   notes. Every manifest carries `contains_personal_data`, and a corpus built
   from the vault writes a SENSITIVE marker next to it. The brief's data
   governance point is not advisory — his notes and other people's sign-in
   data do not go onto university storage, and neither does anything trained
   on them.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DATASET_DIR = ROOT / "data" / "training" / "datasets"

#: The floor below which fine-tuning on outcomes is obviously meaningless
#: rather than merely fragile — the same reasoning, and the same number, as
#: `weightfit.MIN_RESOLVED`. One metric and one floor everywhere, so a result
#: cannot be shopped for by moving the bar.
MIN_OUTCOME_ROWS = 200

#: Held-out rows below this and the comparison in evaluate.py cannot separate
#: a better adapter from a luckier one.
MIN_HOLDOUT_ROWS = 40

#: Share of the timeline reserved for held-out evaluation. Taken from the END
#: — the future is what a forecaster is judged on.
DEFAULT_HOLDOUT_FRAC = 0.25

#: Fields from `training_export()` that may become a training TARGET. The
#: system's own past opinion is deliberately not among them.
_LABEL_ONLY = ("realised_return", "label_correct")
_NEVER_A_LABEL = ("predicted_direction", "stated_confidence")


class NotReady(RuntimeError):
    """The corpus cannot be built yet. Carries what was wanted and what exists
    so the caller can report the gap instead of an empty file."""
    def __init__(self, corpus: str, have: int, want: int, detail: str = ""):
        super().__init__(
            f"{corpus}: {have} of {want} rows — {detail or 'not enough to build on'}")
        self.corpus, self.have, self.want, self.detail = corpus, have, want, detail


# ── de-identification ────────────────────────────────────────────────────────

# Deliberately conservative on the numeric patterns. This is a TRADING vault:
# it is full of dates, price ranges and position sizes, and a phone rule loose
# enough to catch "07700 900123" also catches "2026-08-12 09", "150 - 200" and
# every strike range he has ever written down. Redacting those would quietly
# mangle the writing the voice corpus exists to preserve.
#
# So a phone number is matched only with an international prefix or an explicit
# label, and a bare digit run has to be long enough to be an account rather
# than a date. The cost is a plain domestic number in the middle of a sentence
# getting through. That is a real gap and it is stated here rather than
# implied away — de-identification that claims more than it does is the same
# kind of overstatement this project polices everywhere else.
_REDACTIONS: tuple[tuple[str, re.Pattern], ...] = (
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("KEY", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|"
                       r"AKIA[0-9A-Z]{12,}|xox[baprs]-[A-Za-z0-9-]{10,})")),
    ("PHONE", re.compile(
        r"(?:\+\d{1,3}[ .-]?(?:\(\d{1,4}\)[ .-]?)?\d[\d .-]{5,14}\d)"
        r"|(?:(?:phone|tel|telephone|mobile|cell|whatsapp|call (?:me|him|her) (?:at|on))"
        r"\W{0,4}\+?\d[\d .()-]{6,16}\d)", re.IGNORECASE)),
    # Not part of a decimal on either side — but a number ending a sentence
    # ("...account 4929123456789012.") still counts.
    ("ACCOUNT", re.compile(r"(?<![\d.])\d{11,}(?!\.?\d)")),
    ("PATH", re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"']+|/(?:home|Users)/[^/\s\"']+")),
)


def deidentify(text: str, extra_terms: tuple[str, ...] = ()) -> tuple[str, dict]:
    """Strip the things that identify a person from a note.

    Not anonymisation — it is redaction, and it is imperfect by nature. A note
    that says "my broker called about the Tuesday fill" identifies nobody by
    pattern and everybody by context. Its purpose is narrower and honest:
    keep credentials, contact details and account numbers out of a file that
    is about to be baked into model weights, where nothing can be deleted
    afterwards.
    """
    counts: dict[str, int] = {}
    out = text or ""
    for term in extra_terms:
        term = (term or "").strip()
        if len(term) < 3:
            continue
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        out, n = pattern.subn("[REDACTED:NAME]", out)
        if n:
            counts["NAME"] = counts.get("NAME", 0) + n
    for kind, pattern in _REDACTIONS:
        out, n = pattern.subn(f"[REDACTED:{kind}]", out)
        if n:
            counts[kind] = counts.get(kind, 0) + n
    return out, counts


def owner_terms() -> tuple[str, ...]:
    """Names to redact from the voice corpus — the owner's, plus each part of
    it long enough to be identifying on its own."""
    try:
        from src.v5.identity import CREATOR
    except Exception:
        return ()
    parts = [p for p in re.split(r"\s+", CREATOR) if len(p) > 3]
    return tuple([CREATOR, *parts])


# ── the chronological split ──────────────────────────────────────────────────

def _row_date(row: dict) -> str:
    at = str(row.get("at") or "")
    return at[:10]


def chronological_split(rows: list[dict], holdout_frac: float = DEFAULT_HOLDOUT_FRAC
                        ) -> tuple[list[dict], list[dict]]:
    """Oldest rows train, newest rows evaluate. Never shuffled.

    The cut lands on a DATE boundary: a market day goes entirely to one side
    or the other. Splitting mid-date puts forty names from one morning on both
    sides of the fence and calls the resulting score out-of-sample, which is
    the exact overstatement the walk-forward harness was corrected for twice.
    """
    if not rows:
        return [], []
    missing = [r.get("id", "?") for r in rows if not _row_date(r)]
    if missing:
        raise ValueError(
            f"{len(missing)} row(s) carry no timestamp, so they cannot be "
            f"ordered in time: {missing[:5]}. A dataset that cannot be split "
            f"chronologically must not be split at all.")

    ordered = sorted(rows, key=_row_date)
    cut = max(1, int(len(ordered) * (1 - holdout_frac)))
    # Walk the boundary forward until the date changes, so no date straddles.
    boundary_date = _row_date(ordered[cut - 1])
    while cut < len(ordered) and _row_date(ordered[cut]) == boundary_date:
        cut += 1
    train, holdout = ordered[:cut], ordered[cut:]
    if train and holdout:
        assert _row_date(train[-1]) < _row_date(holdout[0]), \
            "the split leaked: a date appears on both sides"
    return train, holdout


# ── the outcomes corpus ──────────────────────────────────────────────────────

def _outcome_prompt(row: dict) -> str:
    """What the model is shown: the readings, with abstentions named.

    Abstentions are stated rather than dropped. "Nine modules had nothing to
    say about this name" is information — a thin vote is a different situation
    from a unanimous one, and a model shown only the modules that spoke would
    never learn the difference.
    """
    features = row.get("features") or {}
    lines = [f"  {name}: {value:+.3f}" if isinstance(value, (int, float))
             else f"  {name}: {value}"
             for name, value in sorted(features.items())]
    abstained = row.get("abstentions") or []
    return (
        f"Ticker {row.get('ticker', '?')}, horizon {row.get('horizon_days', '?')} "
        f"trading days, as of {_row_date(row)}.\n"
        f"{len(lines)} research modules reported:\n" + "\n".join(lines) + "\n"
        + (f"{len(abstained)} abstained: {', '.join(sorted(abstained))}\n"
           if abstained else "No module abstained.\n")
        + "State the direction you expect over the horizon, and how confident "
          "you are. If the readings do not support a call, abstain."
    )


def _outcome_completion(row: dict) -> str:
    """What the model is trained toward: what ACTUALLY happened."""
    realised = row.get("realised_return")
    direction = "UP" if (realised or 0) > 0 else "DOWN"
    return (f"{direction}. Realised {realised:+.2%} over the horizon."
            if isinstance(realised, (int, float)) else "")


def build_outcomes(rows: list[dict] | None = None, *,
                   min_rows: int = MIN_OUTCOME_ROWS) -> dict:
    """The outcomes corpus, or a refusal explaining exactly what is missing.

    `rows` is injectable for testing. In production it comes from
    `track_record.training_export()`, which exports ONLY resolved predictions —
    an unresolved prediction has no label, and exporting it would invite
    training on the system's own opinion.
    """
    if rows is None:
        from src.v5.track_record import training_export
        rows = training_export()

    usable = [r for r in rows
              if isinstance(r.get("realised_return"), (int, float))
              and _row_date(r)]
    if len(usable) < min_rows:
        raise NotReady(
            "outcomes", len(usable), min_rows,
            "resolved predictions accumulate at roughly twenty directional "
            "calls a month, and they are never backfilled (invariant 5). "
            "Nothing here may be synthesised to close the gap.")

    train, holdout = chronological_split(usable)
    if len(holdout) < MIN_HOLDOUT_ROWS:
        raise NotReady("outcomes/holdout", len(holdout), MIN_HOLDOUT_ROWS,
                       "too few held-out rows to tell a better adapter from a "
                       "luckier one")

    def to_example(r: dict) -> dict:
        return {"prompt": _outcome_prompt(r), "completion": _outcome_completion(r),
                "meta": {"id": r.get("id"), "at": r.get("at"),
                         "ticker": r.get("ticker"),
                         "realised_return": r.get("realised_return"),
                         "label_correct": r.get("label_correct")}}

    return {
        "corpus": "outcomes",
        "train": [to_example(r) for r in train],
        "holdout": [to_example(r) for r in holdout],
        "contains_personal_data": False,
        "label_fields": list(_LABEL_ONLY),
        "span": {"from": _row_date(train[0]) if train else None,
                 "to": _row_date(holdout[-1]) if holdout else None},
    }


def label_leakage(examples: list[dict]) -> list[str]:
    """Any example whose PROMPT contains the answer.

    Cheap and worth running every time: a prompt template edited a year from
    now that quietly interpolates the realised return would produce a model
    that scores brilliantly on held-out data and knows nothing.
    """
    bad = []
    for ex in examples:
        prompt = ex.get("prompt", "")
        realised = (ex.get("meta") or {}).get("realised_return")
        if isinstance(realised, (int, float)) and f"{realised:+.2%}" in prompt:
            bad.append((ex.get("meta") or {}).get("id", "?"))
        for forbidden in _NEVER_A_LABEL:
            if f'"{forbidden}"' in prompt:
                bad.append((ex.get("meta") or {}).get("id", "?"))
    return bad


# ── the voice corpus ─────────────────────────────────────────────────────────

@dataclass
class VoiceNote:
    path: str
    text: str
    redactions: dict = field(default_factory=dict)


#: Vault folders that are not voice and would poison it — app internals, and
#: the archive of superseded material she should not learn to write from.
_VOICE_SKIP = (".obsidian", ".trash", ".agent", ".makemd", ".space", "_Archive")

MIN_VOICE_CHARS = 400       # a stub note teaches formatting, not voice


def build_voice(vault_path: Path | None = None, *, limit: int = 0,
                include_spoken: bool = True) -> dict:
    """The owner's own language, de-identified — in both registers.

    TWO SOURCES, AND THEY ARE NOT THE SAME THING
    --------------------------------------------
    The vault is his WRITTEN register: considered, edited, punctuated. Fine
    tuning on it alone produces something that writes like his notes.

    The spoken corpus (`src/brain/cognitive/hearing.py`) is how he actually
    talks — slang, shorthand, the shape of a thought said out loud rather than
    composed. It only exists because he speaks to her, it accumulates at the
    speed of him using her, and it is the half that makes her sound like a
    person he knows rather than like a document he wrote.

    Both are marked `register` so a training run can weight them, and so
    nobody a year from now has to guess which rows came from where.
    """
    if vault_path is None:
        from src.brain.vault import get_vault_path
        vault_path = get_vault_path()
    vault_path = Path(vault_path)
    if not vault_path.exists():
        raise NotReady("voice", 0, 1, f"no vault at {vault_path}")

    terms = owner_terms()
    notes: list[VoiceNote] = []
    for path in sorted(vault_path.rglob("*.md")):
        rel = path.relative_to(vault_path)
        if any(part in _VOICE_SKIP for part in rel.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(text.strip()) < MIN_VOICE_CHARS:
            continue
        clean, counts = deidentify(text, terms)
        notes.append(VoiceNote(path=str(rel).replace("\\", "/"), text=clean,
                               redactions=counts))
        if limit and len(notes) >= limit:
            break

    spoken = []
    if include_spoken:
        try:
            from src.brain.cognitive.hearing import spoken_corpus
            # Already de-identified on the way in — speech is redacted at the
            # moment it is retained, not at the moment it is trained on, so a
            # corpus that is never built still holds nothing raw.
            spoken = [r for r in spoken_corpus()
                      if len((r.get("text") or "").split()) >= 5]
        except Exception as e:
            logger.warning(f"spoken corpus unavailable: {e}")

    if not notes and not spoken:
        raise NotReady("voice", 0, 1,
                       f"no note in {vault_path} is longer than "
                       f"{MIN_VOICE_CHARS} characters, and he has not spoken "
                       f"to her yet")

    total_redactions = {}
    for n in notes:
        for kind, count in n.redactions.items():
            total_redactions[kind] = total_redactions.get(kind, 0) + count

    return {
        "corpus": "voice",
        "train": [{"text": n.text, "meta": {"path": n.path,
                                            "register": "written"}}
                  for n in notes]
        + [{"text": r["text"], "meta": {"at": r.get("at"),
                                        "context": r.get("context", ""),
                                        "register": "spoken"}}
           for r in spoken],
        "registers": {"written": len(notes), "spoken": len(spoken),
                      "spoken_words": sum(len(r["text"].split()) for r in spoken)},
        "holdout": [],          # voice is style, not a scored prediction
        # An adapter trained on this HAS these notes in its weights. Treat the
        # adapter exactly as you would treat the vault.
        "contains_personal_data": True,
        "redactions": total_redactions,
        "note": ("De-identified, not anonymised. Redaction removes patterns, "
                 "not context. Anything trained on this corpus is as sensitive "
                 "as the vault and as a recording of him speaking, and does "
                 "not go onto shared or university storage."),
    }


# ── writing it out ───────────────────────────────────────────────────────────

def write(corpus: dict, directory: Path = DATASET_DIR) -> dict:
    """JSONL per split, plus a manifest. Returns the manifest.

    A corpus carrying personal data gets a SENSITIVE marker written beside it,
    because the next person to look at the directory will be deciding whether
    to copy it somewhere.
    """
    name = corpus["corpus"]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(directory) / f"{name}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    written = {}
    for split in ("train", "holdout"):
        rows = corpus.get(split) or []
        if not rows:
            continue
        path = out_dir / f"{split}.jsonl"
        path.write_text("\n".join(json.dumps(r, default=str) for r in rows),
                        encoding="utf-8")
        written[split] = {"path": str(path), "rows": len(rows)}

    manifest = {
        "corpus": name,
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "splits": written,
        "contains_personal_data": bool(corpus.get("contains_personal_data")),
        "split_rule": "chronological, never shuffled, cut on a date boundary",
        "span": corpus.get("span"),
        "redactions": corpus.get("redactions"),
        "note": corpus.get("note", ""),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    if manifest["contains_personal_data"]:
        (out_dir / "SENSITIVE").write_text(
            "This corpus contains the owner's personal notes, de-identified "
            "but not anonymised.\n\n"
            "Anything trained on it carries them in its weights. Do not copy "
            "this directory, or any adapter trained from it, onto shared or "
            "university storage.\n", encoding="utf-8")
    manifest["dir"] = str(out_dir)
    return manifest


def readiness() -> dict:
    """What could be built right now, and what is missing. Safe to call at any
    time — it never builds anything."""
    out: dict = {"at": datetime.now().isoformat(timespec="seconds")}
    try:
        from src.v5.track_record import training_export
        rows = training_export()
        usable = [r for r in rows if isinstance(r.get("realised_return"), (int, float))]
        out["outcomes"] = {
            "have": len(usable), "want": MIN_OUTCOME_ROWS,
            "ready": len(usable) >= MIN_OUTCOME_ROWS,
            "note": (f"{len(usable)} resolved predictions. At roughly twenty "
                     f"directional calls a month, {MIN_OUTCOME_ROWS} is about "
                     f"{max(0, (MIN_OUTCOME_ROWS - len(usable))) // 20} more "
                     f"months of the paper loop running. Never backfilled."),
        }
    except Exception as e:
        out["outcomes"] = {"have": 0, "want": MIN_OUTCOME_ROWS, "ready": False,
                           "note": f"could not read the track record: {e}"}
    try:
        from src.brain.vault import get_vault_path
        vault = get_vault_path()
        notes = sum(1 for p in Path(vault).rglob("*.md")
                    if not any(part in _VOICE_SKIP for part in
                               p.relative_to(vault).parts))
        spoken_words = 0
        try:
            from src.brain.cognitive.hearing import spoken_corpus
            spoken_words = sum(len((r.get("text") or "").split())
                               for r in spoken_corpus())
        except Exception:
            pass
        out["voice"] = {"have": notes, "want": 1, "ready": notes > 0,
                        "spoken_words": spoken_words,
                        "note": (f"{notes} written notes at {vault}; "
                                 f"{spoken_words} words of his speech. The "
                                 f"spoken half is the one the vault cannot "
                                 f"supply, and it only grows when he talks to "
                                 f"her.")}
    except Exception as e:
        out["voice"] = {"have": 0, "want": 1, "ready": False,
                        "note": f"vault unreadable: {e}"}
    out["ready"] = bool(out["outcomes"]["ready"])
    out["blocker"] = ("" if out["ready"] else
                      "the outcomes corpus is what makes the model hers; "
                      "voice and domain alone would only change how she sounds")
    return out
