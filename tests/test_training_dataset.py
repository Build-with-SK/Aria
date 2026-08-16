"""
tests/test_training_dataset.py
==============================
The three corpora (docs/ARIA_NEXT_SESSION.md step 5).

Four things are worth testing here, and they are all about what the builder
REFUSES to do:

  - build an outcomes corpus that does not exist yet
  - shuffle
  - train on the system's own past opinion instead of the realised outcome
  - let the vault's contents leave without being marked as the owner's
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.training import dataset as D


def rows(n: int, start: str = "2026-01-01", per_day: int = 1) -> list[dict]:
    """n resolved predictions, `per_day` of them sharing each date."""
    out, day = [], datetime.fromisoformat(start)
    for i in range(n):
        if i and i % per_day == 0:
            day += timedelta(days=1)
        out.append({
            "id": f"p{i:04d}",
            "at": day.isoformat(timespec="seconds"),
            "ticker": f"T{i % 7}",
            "horizon_days": 21,
            "features": {"value": 0.1 * (i % 5), "quality": -0.2},
            "abstentions": ["credit"],
            "predicted_direction": "BULL",
            "stated_confidence": 0.6,
            "realised_return": 0.01 if i % 2 else -0.02,
            "label_correct": bool(i % 2),
        })
    return out


# ── the refusal that is the honest state today ──────────────────────────────

def test_an_empty_track_record_refuses_with_the_counts():
    """Zero resolved predictions is the honest number. The builder says so
    rather than writing an empty file that looks like a dataset."""
    with pytest.raises(D.NotReady) as ei:
        D.build_outcomes([])
    assert ei.value.have == 0
    assert ei.value.want == D.MIN_OUTCOME_ROWS
    assert "never backfilled" in str(ei.value)


def test_a_short_sample_refuses_rather_than_shrinking_the_floor():
    with pytest.raises(D.NotReady) as ei:
        D.build_outcomes(rows(50))
    assert ei.value.have == 50


def test_unresolved_rows_do_not_count_toward_the_floor():
    """An unresolved prediction has no label; counting it would let the corpus
    'become ready' without a single new outcome."""
    unresolved = rows(300)
    for r in unresolved:
        r["realised_return"] = None
    with pytest.raises(D.NotReady) as ei:
        D.build_outcomes(unresolved)
    assert ei.value.have == 0


def test_a_sufficient_sample_builds():
    corpus = D.build_outcomes(rows(400, per_day=4))
    assert corpus["corpus"] == "outcomes"
    assert corpus["train"] and corpus["holdout"]
    assert corpus["contains_personal_data"] is False


# ── chronological, never shuffled ───────────────────────────────────────────

def test_the_holdout_is_the_future_not_a_random_sample():
    train, holdout = D.chronological_split(rows(100))
    assert train[-1]["at"] < holdout[0]["at"]
    assert len(holdout) == pytest.approx(25, abs=3)


def test_a_date_never_straddles_the_split():
    """Forty tickers on one date are one observation of one market day. A cut
    through the middle of a date puts the same morning on both sides and calls
    the result out-of-sample — the exact overstatement the walk-forward
    harness was corrected for."""
    train, holdout = D.chronological_split(rows(120, per_day=40))
    train_dates = {r["at"][:10] for r in train}
    holdout_dates = {r["at"][:10] for r in holdout}
    assert not (train_dates & holdout_dates)


def test_input_order_does_not_change_the_split():
    """Sorting by time, not trusting the order the rows arrived in."""
    forward = rows(80)
    train_a, holdout_a = D.chronological_split(forward)
    train_b, holdout_b = D.chronological_split(list(reversed(forward)))
    assert [r["id"] for r in train_a] == [r["id"] for r in train_b]
    assert [r["id"] for r in holdout_a] == [r["id"] for r in holdout_b]


def test_rows_without_a_timestamp_are_refused_not_dropped():
    """A dataset that cannot be split chronologically must not be split."""
    bad = rows(40)
    bad[7]["at"] = ""
    with pytest.raises(ValueError, match="cannot be ordered in time"):
        D.chronological_split(bad)


def test_a_corpus_crammed_onto_two_dates_has_no_future_to_test_on():
    """200 rows across two market days is 200 rows and two observations. The
    date-boundary rule then leaves nothing on the far side of the cut, and an
    adapter that cannot be evaluated cannot be promoted — so the builder
    refuses rather than handing back a holdout of four."""
    with pytest.raises(D.NotReady) as ei:
        D.build_outcomes(rows(210, per_day=105), min_rows=200)
    assert ei.value.corpus == "outcomes/holdout"
    assert ei.value.have < D.MIN_HOLDOUT_ROWS


# ── the label is what happened, not what she said ───────────────────────────

def test_the_target_is_the_realised_outcome():
    corpus = D.build_outcomes(rows(400, per_day=4))
    up = [e for e in corpus["train"]
          if (e["meta"]["realised_return"] or 0) > 0]
    assert up and all(e["completion"].startswith("UP") for e in up)


def test_the_prompt_never_contains_the_answer():
    corpus = D.build_outcomes(rows(400, per_day=4))
    assert D.label_leakage(corpus["train"] + corpus["holdout"]) == []


def test_leakage_is_caught_when_a_template_starts_interpolating_the_outcome():
    """The guard has to fail on a bad template, or it is decoration."""
    leaky = [{"prompt": "Readings... realised +1.00% over the horizon",
              "completion": "UP", "meta": {"id": "p1", "realised_return": 0.01}}]
    assert D.label_leakage(leaky) == ["p1"]


def test_the_systems_own_past_opinion_is_not_a_label():
    """Training toward `predicted_direction` would teach her to reproduce the
    system's opinions, including its wrong ones — and would score well while
    learning nothing."""
    assert "predicted_direction" in D._NEVER_A_LABEL
    assert "predicted_direction" not in D._LABEL_ONLY
    corpus = D.build_outcomes(rows(400, per_day=4))
    assert all("predicted_direction" not in e["prompt"] for e in corpus["train"])


def test_abstentions_are_shown_not_dropped():
    """A thin vote is a different situation from a unanimous one."""
    corpus = D.build_outcomes(rows(400, per_day=4))
    assert "abstained: credit" in corpus["train"][0]["prompt"]


# ── de-identification ───────────────────────────────────────────────────────

def test_contact_details_and_credentials_are_redacted():
    text = ("Email me at trader.person@example.com or +44 7700 900123. "
            "Key sk-abcdefghijklmnopqrstuvwx and account 4929123456789012. "
            r"Notes in C:\Users\sound\Documents\private.md")
    clean, counts = D.deidentify(text)
    assert "example.com" not in clean
    assert "sk-abcdefghijklmnopqrstuvwx" not in clean
    assert "4929123456789012" not in clean
    assert r"C:\Users\sound" not in clean
    assert set(counts) >= {"EMAIL", "KEY", "ACCOUNT", "PATH"}


def test_the_owners_name_is_redacted_from_his_own_notes():
    terms = D.owner_terms()
    assert terms, "the creator's name should be known"
    clean, counts = D.deidentify(f"{terms[0]} decided to cut the position.", terms)
    assert terms[0] not in clean
    assert counts.get("NAME")


def test_deidentification_leaves_the_writing_intact():
    """It redacts patterns; it must not mangle the prose the corpus exists
    for."""
    text = ("I size down when the regime flips. Two bad weeks in a row means "
            "I have stopped reading the tape and started arguing with it.")
    clean, counts = D.deidentify(text, D.owner_terms())
    assert clean == text and counts == {}


def test_a_trading_vault_full_of_numbers_survives_de_identification():
    """The corpus is a trader's notebook. A phone rule loose enough to catch
    '07700 900123' also eats every date, strike range and position size in it,
    and the mangling would be invisible in the resulting weights."""
    text = ("On 2026-08-12 09:30 I bought 150 shares between 214 - 218. "
            "Range 4500 - 4700 on the index, stop 4450. "
            "Held 2024 - 2025 through the drawdown. Ticket 12345 6789.")
    clean, counts = D.deidentify(text, D.owner_terms())
    assert clean == text, f"redacted something it should not have: {counts}"


def test_a_labelled_or_international_number_is_still_caught():
    for text in ("Call me at +44 7700 900123 tomorrow.",
                 "mobile: 07700 900123",
                 "His phone 020 7946 0958 is on the sheet."):
        clean, counts = D.deidentify(text)
        assert counts.get("PHONE"), f"missed a phone number in {text!r}"
        assert "900123" not in clean or "7946" not in text


# ── the vault corpus is personal data and says so ───────────────────────────

def test_the_voice_corpus_is_marked_personal(tmp_path):
    (tmp_path / "note.md").write_text("x" * 500, encoding="utf-8")
    corpus = D.build_voice(tmp_path)
    assert corpus["contains_personal_data"] is True
    assert "university storage" in corpus["note"]


def test_app_internals_and_the_archive_are_skipped(tmp_path):
    for folder in (".obsidian", "_Archive"):
        d = tmp_path / folder
        d.mkdir()
        (d / "junk.md").write_text("y" * 500, encoding="utf-8")
    (tmp_path / "real.md").write_text("z" * 500, encoding="utf-8")
    corpus = D.build_voice(tmp_path)
    assert [n["meta"]["path"] for n in corpus["train"]] == ["real.md"]


def test_stub_notes_do_not_teach_voice(tmp_path):
    (tmp_path / "stub.md").write_text("todo", encoding="utf-8")
    with pytest.raises(D.NotReady):
        D.build_voice(tmp_path)


def test_a_missing_vault_is_a_refusal_not_an_empty_corpus(tmp_path):
    with pytest.raises(D.NotReady):
        D.build_voice(tmp_path / "nope")


def test_writing_a_personal_corpus_leaves_a_marker(tmp_path):
    (tmp_path / "note.md").write_text("w" * 500, encoding="utf-8")
    corpus = D.build_voice(tmp_path)
    manifest = D.write(corpus, directory=tmp_path / "out")
    marker = Path(manifest["dir"]) / "SENSITIVE"
    assert marker.exists()
    assert "university storage" in marker.read_text(encoding="utf-8")
    assert manifest["split_rule"].startswith("chronological")


def test_a_written_corpus_round_trips(tmp_path):
    corpus = D.build_outcomes(rows(400, per_day=4))
    manifest = D.write(corpus, directory=tmp_path)
    train = Path(manifest["splits"]["train"]["path"])
    first = json.loads(train.read_text(encoding="utf-8").splitlines()[0])
    assert "prompt" in first and "completion" in first
    assert not (Path(manifest["dir"]) / "SENSITIVE").exists()


# ── readiness never builds anything ─────────────────────────────────────────

def test_readiness_reports_the_gap_without_raising():
    r = D.readiness()
    assert set(r) >= {"outcomes", "voice", "ready", "blocker"}
    assert r["outcomes"]["want"] == D.MIN_OUTCOME_ROWS
    if not r["ready"]:
        assert r["blocker"]
