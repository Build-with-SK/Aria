"""
Regression tests from one SENTINEL conversation (2026-10-14) that was
confidently wrong. ARIA's half of it:

* the ticker detector turned English into companies — "match" -> MTCH,
  "earn" -> EARN, "step" -> STEP, the headings TRAINING and FEEDBACK — and the
  specialist answered about those companies;
* a follow-up ("about their presence in the market") reached ARIA without the
  company it was about;
* a 4B model wrote 1,000 - 280 - 420 = 700, dated the conversation 2024, and
  left a seven-question answer unfinished, and nothing said so.

The universe is a small fake with the same search contract as
src/data/universe.py, so these do not depend on the live symbol master.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("fastapi")

from src.intelligence import answer_checks as ac  # noqa: E402

ROWS = [
    ("TSLA", "Tesla"), ("AAPL", "Apple"), ("NVDA", "NVIDIA"),
    ("MSFT", "Microsoft"), ("MTCH", "Match Group, Inc."),
    ("EARN", "Ellington Credit Company"), ("STEP", "StepStone Group Inc."),
    ("TTGIL.BO", "Tomorrow Technologies Global"), ("YESBANK", "Yes Bank"),
    ("AVAILFC.BO", "Available Finance"), ("WOK", "Work Medical"),
    ("ARIA", "ARIA Opportunities ETF"), ("S", "SentinelOne"),
    ("CPAI", "Counterpoint Quantitative Equity ETF"), ("ALL", "Allstate"),
    ("FIVE", "Five Below"), ("USE", "USCF Energy"), ("FDS", "FactSet"),
    ("RELIANCE", "Reliance Industries"), ("SAIL", "SailPoint"),
    ("GOLD", "Gold.com, Inc."),
]
TRACKED = {"TSLA", "AAPL", "NVDA", "MSFT"}


class FakeUniverse:
    def search(self, q, n=20):
        qu = (q or "").upper()
        exact = [r for r in ROWS if r[0].upper() == qu]
        rest = [r for r in ROWS if r not in exact
                and (qu in r[0].upper() or qu in r[1].upper())]
        return [{"symbol": s, "yahoo": s, "name": name} for s, name in (exact + rest)[:n]]


@pytest.fixture
def m(monkeypatch):
    import backend.main as main
    monkeypatch.setattr(main, "_get_universe", lambda: FakeUniverse())
    monkeypatch.setattr(main, "_load_optional",
                        lambda f: {t: {"action": "Hold"} for t in TRACKED}
                        if f == "signals.json" else {})
    return main


# ── the detector ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("message", [
    "can do you know the match will is going in india in few days. and can you "
    "predict the winner",
    "I'm talking about cricket match, not the stock market",
    "do you think if i buy 1 stock, how much will i earn tomorrow",
    "explain step by step why a portfolio can have high expected returns but low "
    "realized returns",
    "yes, before i specifically asked for rate from all countries you didn't say "
    "anything",
    "why it's not available to you",
    "will yfinance work",
    "you are, if you want you can ask me any questions",
    "give the gold rate from all countries",
])
def test_english_words_are_not_companies(m, message):
    assert m._detect_query_ticker(message)[0] is None


@pytest.mark.parametrize("message", [
    "TRAINING LESSON 1: EVIDENCE DISCIPLINE  You must follow these rules",
    "FEEDBACK ON YOUR LAST ANSWER — CORRECTION REQUIRED",
    "5. USE PROPORTIONATE CONCLUSIONS",
    "1. FACTS VS HYPOTHESES",
    "ANSWER ALL FIVE QUESTIONS",
])
def test_a_capitalised_heading_is_not_a_ticker(m, message):
    assert m._detect_query_ticker(message)[0] is None


def test_the_systems_own_names_are_not_tickers(m):
    # ARIA is an ETF's symbol; SENTINEL starts SentinelOne's name.
    assert m._detect_query_ticker("You are auditing ARIA's financial decision "
                                  "pipeline and SENTINEL's router")[0] is None


def test_a_heading_does_not_hide_the_ticker_after_it(m):
    msg = ("TRAINING LESSON 1: EVIDENCE DISCIPLINE\n"
           "- No signal exists for asset CPAI.")
    assert m._detect_query_ticker(msg)[0] == "CPAI"


@pytest.mark.parametrize("message,ticker", [
    ("what do you think about the tesla", "TSLA"),
    ("how's AAPL doing", "AAPL"),
    ("compare AAPL MSFT NVDA", "AAPL"),
    ("ANSWER ALL FIVE QUESTIONS about NVDA", "NVDA"),
    ("is NVIDIA a buy", "NVDA"),
    ("what about Reliance Industries", "RELIANCE"),
    ("price of BTC-USD", "BTC-USD"),
    ("tell me about TATAMOTORS from bse", "TATAMOTORS"),
])
def test_real_mentions_still_resolve(m, message, ticker):
    assert m._detect_query_ticker(message)[0] == ticker


def test_nse_inside_an_english_word_is_not_a_market_hint(m):
    _, market = m._detect_query_ticker("your response was not in my license")
    assert market is None


# ── the specialist endpoint ──────────────────────────────────────────────

OWNER = "owner-token-for-transcript-tests"


@pytest.fixture
def app(m, monkeypatch):
    from src.auth import hardening
    monkeypatch.setenv("ARIA_OWNER_TOKEN", OWNER)
    monkeypatch.delenv("ARIA_OWNER_TOKEN_HASH", raising=False)
    monkeypatch.setattr(m, "_ollama_available", lambda: True)
    monkeypatch.setattr(m, "_build_market_context", lambda: "MARKET STATE as of 2026-10-14")
    hardening._limiter._hits.clear()
    return m


def _model(monkeypatch, text, seen):
    from src.inference import router as r

    class Res:
        pass

    class R:
        def complete_with(self, provider, model, msgs, *, system="", **kw):
            seen.append({"system": system, "msgs": msgs})
            out = Res(); out.text = text; out.model = model
            return out
    monkeypatch.setattr(r, "get_router", lambda: R())


def _ask(m, question, context=""):
    from fastapi.testclient import TestClient
    c = TestClient(m.app, client=("203.0.113.9", 55001))
    body = {"question": question}
    if context:
        body["context"] = context
    return c.post("/api/specialist/analysis", json=body,
                  headers={"Authorization": f"Bearer {OWNER}"})


SECTIONS = "ANALYSIS:\n{}\n\nASSUMPTIONS:\n- none stated\n\nRISKS:\n- it could be wrong\n"


def test_a_follow_up_finds_its_company_in_the_owners_earlier_words(app, monkeypatch):
    seen = []
    _model(monkeypatch, SECTIONS.format("Tesla holds a large EV share."), seen)
    r = _ask(app, "about their persence in the market",
             context="Earlier in this conversation the owner said (oldest first):\n"
                     "- what do you think about the tesla")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["signal"] and d["signal"]["ticker"] == "TSLA"
    assert not any("no signal exists" in x for x in d["verification_reasons"])


def test_a_heading_never_becomes_a_missing_signal(app, monkeypatch):
    seen = []
    _model(monkeypatch, SECTIONS.format("1. a\n2. b"), seen)
    d = _ask(app, "TRAINING LESSON 1: EVIDENCE DISCIPLINE\nFEEDBACK ON YOUR "
                  "LAST ANSWER").json()
    assert not any("TRAINING" in x or "FEEDBACK" in x
                   for x in d["verification_reasons"])


def test_the_prompt_states_todays_date_and_scopes_the_market_data(app, monkeypatch):
    seen = []
    _model(monkeypatch, SECTIONS.format("ok"), seen)
    _ask(app, "how do markets look")
    system = seen[0]["system"]
    assert f"TODAY IS {date.today().strftime('%A %d %B %Y')}" in system
    assert "Answer THE QUESTION ASKED" in system


def test_wrong_arithmetic_is_flagged(app, monkeypatch):
    seen = []
    _model(monkeypatch, SECTIONS.format(
        "There are 1000 total assets – 280 bullish + 420 bearish = 700 assets "
        "unclassified."), seen)
    d = _ask(app, "A system contains 1,000 assets: - 280 bullish - 420 bearish. "
                  "How many are unclassified?").json()
    assert d["needs_verification"] is True
    assert any(x.startswith("arithmetic:") and "700" in x
               for x in d["verification_reasons"])


def test_an_invented_year_is_flagged(app, monkeypatch):
    seen = []
    _model(monkeypatch, SECTIONS.format("October 14, 2024. The macro regime is "
                                        "Goldilocks."), seen)
    d = _ask(app, "how does the macro regime look").json()
    assert any("2024" in x for x in d["verification_reasons"])


def test_an_unfinished_numbered_answer_is_flagged(app, monkeypatch):
    seen = []
    _model(monkeypatch, SECTIONS.format("| Question | Evidence |\n| :--- | :--- |"), seen)
    q = ("A financial system reports the following:\n- No signal exists for "
         "asset CPAI.\n\nAnswer these questions:\n\n" +
         "\n".join(f"{i}. question {i}?" for i in range(1, 8)))
    d = _ask(app, q).json()
    assert any("7 numbered answers" in x for x in d["verification_reasons"])


# ── the checks themselves ────────────────────────────────────────────────

def test_arithmetic_from_the_transcript():
    assert ac.arithmetic_errors("717 - 217 - 303 = 200 assets")  # it is 197
    assert ac.arithmetic_errors("1,000 - 280 - 420 = 700")
    assert not ac.arithmetic_errors("1,000 - 280 - 420 = 300")
    assert not ac.arithmetic_errors("280/1000 = 28%")
    assert not ac.arithmetic_errors("217/717 = 30.3%")
    assert ac.arithmetic_errors("217/717 = 42.4%")


def test_years_from_the_question_or_data_are_allowed():
    today = date(2026, 10, 15)
    assert ac.foreign_years("as of 2026-10-14", today=today) == []
    assert ac.foreign_years("in 2024", today=today) == ["2024"]
    assert ac.foreign_years("in 2024", "since 2024", today=today) == []


def test_numbered_parts_count_an_unbroken_run():
    assert ac.numbered_parts("1. a\n2. b\n3. c") == 3
    assert ac.numbered_parts("| 1 | a |\n| 2 | b |") == 2
    assert ac.numbered_parts("Q1: a\nQ2: b") == 2
    assert ac.numbered_parts("3. a\n7. b") == 0


def test_missing_parts_only_when_parts_were_asked_for():
    asked = "Answer exactly these five numbered questions:\n" + \
            "\n".join(f"{i}. q" for i in range(1, 6))
    assert ac.missing_parts(asked, "1. a\n2. b") != ""
    assert ac.missing_parts(asked, "\n".join(f"{i}. a" for i in range(1, 6))) == ""
    assert ac.missing_parts("1. a fact\n2. another", "short") == ""
