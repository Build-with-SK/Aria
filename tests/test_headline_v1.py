"""
The v1 headline-extraction pipeline (scripts/headline_v1), minus the GPU.

Training and generation run on the lab machine; everything that decides what
a label, a score or a ticker *means* is pure Python and is pinned here. The
ticker tests matter most: a wrong mapping there is a trade on the wrong
instrument, and two of them (inverse funds, second-leg currencies) were
found by building the map, not by reading the code.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HV1 = ROOT / "scripts" / "headline_v1"
sys.path.insert(0, str(HV1))

from headline_schema import (SYSTEM_PROMPT, canon_name, format_label,  # noqa: E402
                             messages, parse_output, validate)
import metrics_v1  # noqa: E402
import review_v1  # noqa: E402
import label_v1  # noqa: E402
import export_v1  # noqa: E402
from ticker_map import alias_key, map_entities, normalise  # noqa: E402

LABEL = {"sentiment": "positive",
         "entities": [{"name": "Shell", "sentiment": "positive"},
                      {"name": "BP", "sentiment": "negative"}],
         "event": "earnings"}


# ── schema ──────────────────────────────────────────────────────────────────

def test_valid_label_round_trips_through_training_format():
    obj, json_ok, schema_ok = parse_output(format_label(LABEL))
    assert (json_ok, schema_ok) and obj == LABEL


@pytest.mark.parametrize("bad, why", [
    ({**LABEL, "confidence": 0.9}, "keys"),
    ({**LABEL, "event": "ipo"}, "event"),
    ({**LABEL, "sentiment": "bullish"}, "sentiment"),
    ({**LABEL, "entities": [{"name": "RR.", "sentiment": "positive"}]}, "ticker-like"),
    ({**LABEL, "entities": [{"name": "$TSLA", "sentiment": "positive"}]}, "ticker-like"),
    ({**LABEL, "entities": [{"name": "Shell", "sentiment": "positive"},
                            {"name": "Shell plc", "sentiment": "positive"}]}, "duplicate"),
])
def test_validate_rejects(bad, why):
    ok, reason = validate(bad)
    assert not ok and why in reason


def test_real_upper_case_names_are_not_mistaken_for_tickers():
    for name in ("BP", "HSBC", "GSK", "FTSE 100", "S&P 500"):
        assert validate({**LABEL, "entities": [{"name": name, "sentiment": "neutral"}]})[0]


def test_parse_output_tolerates_trailing_text_but_not_broken_json():
    assert parse_output(format_label(LABEL) + "<|eot_id|>")[2]
    assert parse_output('{"sentiment": "positive",') == (None, False, False)


def test_canon_name_ignores_case_and_corporate_suffixes():
    assert canon_name("Shell plc") == canon_name("SHELL") == "shell"
    assert canon_name("Apple Inc.") == "apple"
    assert canon_name("Rolls-Royce Group plc") == "rolls-royce"


def test_one_system_prompt_everywhere():
    m = messages("x", LABEL)
    assert m[0]["content"] == SYSTEM_PROMPT and m[-1]["content"] == format_label(LABEL)
    assert "ticker" in SYSTEM_PROMPT and "confidence" not in SYSTEM_PROMPT


# ── metrics ─────────────────────────────────────────────────────────────────

def test_metrics_flips_entities_and_invalid_rows():
    gold = [LABEL,
            {"sentiment": "negative", "entities": [], "event": "macro_data"},
            {"sentiment": "neutral", "entities": [{"name": "Apple", "sentiment": "neutral"}],
             "event": "product"}]
    preds = [{"sentiment": "negative",                    # top-level flip
              "entities": [{"name": "Shell plc", "sentiment": "positive"},
                           {"name": "Exxon", "sentiment": "neutral"}],
              "event": "earnings"},
             {"sentiment": "negative", "entities": [], "event": "macro_data"},
             None]                                         # invalid output
    s = metrics_v1.score(gold, preds, [True, True, False])
    assert s["json_ok"] == 2 and s["schema_ok"] == 2
    assert s["sent_acc"] == pytest.approx(1 / 3)          # invalid counts as wrong
    assert s["top_flips"] == [0]
    assert (s["ent_tp"], s["ent_pred"], s["ent_gold"]) == (1, 2, 3)
    assert s["ent_sent_acc"] == 1.0
    assert "(invalid)" in s["sent_conf"]
    assert metrics_v1.pick_examples(s, k=2)[0] == 0         # flips come first


def test_every_flip_is_shown_even_past_k():
    gold = [{"sentiment": "positive", "entities": [], "event": "other"}] * 5
    preds = [{"sentiment": "negative", "entities": [], "event": "other"}] * 5
    s = metrics_v1.score(gold, preds, [True] * 5)
    assert len(metrics_v1.pick_examples(s, k=2)) == 5


# ── Checkpoint A review sheet ───────────────────────────────────────────────

def test_review_flags_and_apply_round_trip(tmp_path):
    rows = [
        {"text": "Shell beats estimates, BP misses", "label": LABEL, "gold_sentiment": "negative"},
        {"text": "Markets drift", "label": {"sentiment": "neutral", "entities": [
            {"name": "FTSE 100", "sentiment": "neutral"}], "event": "other"}},
    ]
    test = tmp_path / "test.jsonl"
    test.write_text("".join(json.dumps(r) + "\n" for r in rows))
    sheet = tmp_path / "test_review.csv"
    n, k = review_v1.write_review(test, sheet)
    assert (n, k) == (2, 2)

    f0 = review_v1.flags(rows[0])
    assert f0["flag_entity_opposite"] == "BP" and f0["flag_gold_disagree"] == "gold=negative"
    f1 = review_v1.flags(rows[1])
    assert f1["flag_entity_not_in_text"] == "FTSE 100" and f1["flag_event_other"]

    with sheet.open(newline="", encoding="utf-8-sig") as f:
        data = list(csv.DictReader(f))
    fixed = {**LABEL, "sentiment": "neutral"}
    data[0]["corrected_label"] = json.dumps(fixed)
    data[1]["drop"] = "y"
    with sheet.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(data[0]))
        w.writeheader()
        w.writerows(data)

    out = tmp_path / "test_reviewed.jsonl"
    review_v1.apply_review(test, sheet, out)
    kept = [json.loads(l) for l in out.read_text().splitlines()]
    assert len(kept) == 1 and kept[0]["label"] == fixed


def test_apply_refuses_an_invalid_correction(tmp_path):
    test = tmp_path / "test.jsonl"
    test.write_text(json.dumps({"text": "x", "label": LABEL}) + "\n")
    sheet = tmp_path / "s.csv"
    review_v1.write_review(test, sheet)
    with sheet.open(newline="", encoding="utf-8-sig") as f:
        data = list(csv.DictReader(f))
    data[0]["corrected_label"] = '{"sentiment": "up"}'
    with sheet.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(data[0]))
        w.writeheader()
        w.writerows(data)
    with pytest.raises(SystemExit, match="fails the schema"):
        review_v1.apply_review(test, sheet, tmp_path / "o.jsonl")


# ── labelling split ─────────────────────────────────────────────────────────

def test_split_is_deterministic_whatever_order_threads_finished(tmp_path, monkeypatch):
    monkeypatch.setattr(label_v1, "N_TEST", 3)
    monkeypatch.setattr(label_v1, "append_results", lambda *a, **k: None)
    rows = [{"text": f"Headline {i}", "label": LABEL} for i in range(10)]
    rows.append({"text": "Headline 3", "label": None, "error": "rate limited"})  # earlier failure
    rows.append({"text": "Broken", "label": None, "error": "invalid: event"})

    def split(order, d):
        raw = tmp_path / f"{d}.jsonl"
        raw.write_text("".join(json.dumps(rows[i]) + "\n" for i in order))
        md = label_v1.summarise_and_split(raw, tmp_path / d)
        return (tmp_path / d / "test.jsonl").read_text(), md

    a, md = split(range(12), "a")
    b, _ = split(reversed(range(12)), "b")
    assert a == b
    assert "labelled OK: **10**" in md and "dropped after 3 attempts: **1**" in md
    assert "positive is 100.0%" in md       # >60% class is reported


def test_label_cap_needs_explicit_override(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["label_v1.py", "--limit", "20000"])
    with pytest.raises(SystemExit, match="cost cap"):
        label_v1.main()


# ── ticker map ──────────────────────────────────────────────────────────────

def test_universe_names_and_curated_aliases_map():
    assert normalise("Shell plc") == "SHEL"
    assert normalise("Apple Inc.") == "AAPL"
    assert normalise("Footsie") == normalise("FTSE 100") == "^FTSE"
    assert normalise("Alphabet") == normalise("Google") == "GOOGL"
    assert normalise("gold") == "GC=F"


def test_names_outside_the_universe_are_dropped_not_guessed():
    assert normalise("Rolls-Royce") is None          # not in TIS's universe
    assert normalise("Appl") is None                  # no fuzzy matching
    ents = [{"name": "Rolls Royce", "sentiment": "positive"},
            {"name": "Shell", "sentiment": "positive"}]
    assert [e["ticker"] for e in map_entities(ents)] == ["SHEL"]


def test_bare_tickers_are_not_aliases():
    for word in ("AI", "ON", "NOW", "C", "U", "S", "PATH"):
        assert normalise(word) is None, word


def test_inverse_funds_do_not_collapse_onto_their_long_twins():
    assert alias_key("Direxion -3x Financials") != alias_key("Direxion 3x Financials")
    assert normalise("Direxion -3x Financials") == "FAZ"
    assert normalise("Direxion 3x Financials") == "FAS"
    assert normalise("ProShares -2x Crude Oil") == "SCO"


def test_second_leg_currency_sentiment_is_inverted_for_the_ticker():
    yen, euro = map_entities([{"name": "yen", "sentiment": "positive"},
                              {"name": "euro", "sentiment": "positive"}])
    assert (yen["ticker"], yen["ticker_sentiment"]) == ("USDJPY=X", "negative")
    assert (euro["ticker"], euro["ticker_sentiment"]) == ("EURUSD=X", "positive")


def test_committed_map_matches_a_fresh_build():
    import build_ticker_map
    fresh = build_ticker_map.build(ROOT / "configs" / "universe.yaml")
    saved = json.loads((HV1 / "ticker_map.json").read_text(encoding="utf-8"))
    for k in ("aliases", "inverted", "collisions", "n_tickers"):
        assert fresh[k] == saved[k], f"ticker_map.json is stale ({k}); rerun build_ticker_map.py"


def test_only_restricts_and_rejects_unknown_tickers(tmp_path):
    import build_ticker_map
    m = build_ticker_map.build(ROOT / "configs" / "universe.yaml", {"SHEL", "GC=F"})
    assert set(m["aliases"].values()) == {"SHEL", "GC=F"}
    with pytest.raises(SystemExit, match="not in universe"):
        build_ticker_map.build(ROOT / "configs" / "universe.yaml", {"RR.L"})


# ── Ollama export ───────────────────────────────────────────────────────────

class _FakeTok:
    """Renders like Llama 3.1's template, including the date preamble."""
    def apply_chat_template(self, msgs, tokenize=False, add_generation_prompt=False):
        out = "<|begin_of_text|>"
        for m in msgs:
            body = m["content"]
            if m["role"] == "system":
                body = "Cutting Knowledge Date: December 2023\nToday Date: 26 Jul 2024\n\n" + body
            out += f"<|start_header_id|>{m['role']}<|end_header_id|>\n\n{body}<|eot_id|>"
        return out + "<|start_header_id|>assistant<|end_header_id|>\n\n"


def test_modelfile_system_is_what_training_rendered():
    system = export_v1.rendered_system(_FakeTok())
    assert system.startswith("Cutting Knowledge Date") and system.endswith(SYSTEM_PROMPT)
    mf = export_v1.modelfile("m.gguf", system)
    assert mf.startswith("FROM ./m.gguf") and f'SYSTEM """{system}"""' in mf
    assert 'PARAMETER stop "<|eot_id|>"' in mf
