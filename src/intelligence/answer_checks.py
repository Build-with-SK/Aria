"""Mechanical checks on a model's written answer.

A 4B model asked "1,000 assets, 280 bullish, 420 bearish — how many are
unclassified?" answered 700, dated a 2026 conversation "October 14, 2024", and
began a seven-question table it never finished (SENTINEL conversation,
2026-10-14). Each of those can be caught without a second model:

* arithmetic the answer writes out ("1000 - 280 - 420 = 700") can be re-done;
* a year the answer mentions that appears nowhere in the question or the data
  it was given was invented;
* a question with numbered parts can be counted against the answer's.

These checks only ever ADD a reason to verify. They never rewrite an answer
and never certify one: an answer that passes has not been shown correct, only
not shown wrong in these three ways.

Pure functions, no I/O, standard library only.
"""
from __future__ import annotations

import re
from datetime import date

__all__ = ["arithmetic_errors", "foreign_years", "numbered_parts",
           "missing_parts", "check_answer"]

_NUM = r"\d[\d,]*(?:\.\d+)?"
# A number, optionally followed by a few words ("1000 total assets"), then an
# operator. Words are allowed because models write "280 bullish + 420 bearish".
_OPERAND = rf"({_NUM})(?:\s+[A-Za-z][A-Za-z\-]*){{0,3}}"
_OP = r"\s*([-+*/×x−–])\s*"
_EXPR = re.compile(
    rf"(?<![\w.,]){_OPERAND}(?:{_OP}{_OPERAND}){{1,5}}\s*=\s*({_NUM})\s*(%?)")


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def _evaluate(numbers: list[float], ops: list[str]) -> float | None:
    """Left-to-right with * and / binding tighter, as written arithmetic does."""
    ops = ["-" if o in "−–" else "*" if o in "×x" else o for o in ops]
    terms, signs, cur = [], [], numbers[0]
    for op, n in zip(ops, numbers[1:]):
        if op == "*":
            cur *= n
        elif op == "/":
            if n == 0:
                return None
            cur /= n
        else:
            terms.append(cur)
            signs.append(op)
            cur = n
    terms.append(cur)
    total = terms[0]
    for sign, t in zip(signs, terms[1:]):
        total = total + t if sign == "+" else total - t
    return total


def arithmetic_errors(answer: str) -> list[str]:
    """Each written calculation whose stated result does not follow."""
    out = []
    for m in _EXPR.finditer(answer or ""):
        whole = m.group(0)
        pieces = re.findall(rf"({_NUM})(?:\s+[A-Za-z][A-Za-z\-]*){{0,3}}", whole.split("=")[0])
        ops = re.findall(r"(?<=[\s\d])([-+*/×x−–])(?=\s*\d)", whole.split("=")[0])
        if len(pieces) < 2 or len(ops) != len(pieces) - 1:
            continue
        try:
            numbers = [_num(p) for p in pieces]
            stated = _num(m.group(m.lastindex - 1))
        except ValueError:
            continue
        value = _evaluate(numbers, ops)
        if value is None:
            continue
        if m.group(m.lastindex) == "%" and ops == ["/"]:
            value *= 100
        tolerance = max(0.051, abs(value) * 0.005)
        if abs(value - stated) > tolerance:
            shown = f"{value:,.2f}".rstrip("0").rstrip(".")
            out.append(f"{whole.strip()} does not hold (it is {shown})")
    return out


_YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20\d\d)(?!\d)")


def foreign_years(answer: str, *sources: str, today: date | None = None) -> list[str]:
    """Years the answer names that appear in none of `sources` and are not
    this year or last year."""
    today = today or date.today()
    allowed = {str(today.year), str(today.year - 1)}
    for s in sources:
        allowed.update(_YEAR.findall(s or ""))
    return sorted({y for y in _YEAR.findall(answer or "") if y not in allowed})


# "1. …", "**1.** …", "Q1: …" or a table row "| 1 | …".
_PART = re.compile(r"(?m)^\s*\|?\s*(?:\*\*)?Q?(\d{1,2})(?:[.):]|\s*\|)\s*\S")


#: The same numbering written inline — a pasted prompt often arrives with its
#: newlines collapsed to spaces ("Answer these questions: 1. What … 2. Can …").
_INLINE_PART = re.compile(r"(?:^|(?<=\s))(\d{1,2})[.)]\s+(?=[A-Z])")


def numbered_parts(text: str) -> int:
    """How many numbered parts a block has: the longest run 1, 2, 3 … at the
    start of lines, or written inline. A list of facts numbered elsewhere does
    not inflate it, because only an unbroken run from 1 counts."""
    return max(_longest_run(_PART.findall(text or "")),
               _longest_run(_INLINE_PART.findall(text or "")))


def _longest_run(found: list) -> int:
    seen = [int(n) for n in found]
    best = run = 0
    for n in seen:
        run = run + 1 if n == run + 1 else (1 if n == 1 else run)
        best = max(best, run)
    return best


_ANSWER_THESE = re.compile(
    r"answer\s+(?:exactly\s+)?(?:these|the\s+following|all|each)\b"
    r"|(?:^|\n)\s*tasks?\s*:", re.I)


def missing_parts(question: str, answer: str) -> str:
    """'' when the answer has as many numbered parts as the question asked
    for, else a sentence saying how many are missing. Only applies when the
    question numbers its parts AND asks for them to be answered."""
    if not _ANSWER_THESE.search(question or ""):
        return ""
    tail = question[_ANSWER_THESE.search(question).start():]
    asked = numbered_parts(tail)
    if asked < 2:
        return ""
    given = numbered_parts(answer)
    if given >= asked:
        return ""
    return (f"the question asked for {asked} numbered answers and the answer "
            f"has {given}")


def check_answer(question: str, answer: str, *, data: str = "",
                 context: str = "", today: date | None = None) -> list[str]:
    """Every reason, in plain words, that this answer needs checking."""
    reasons = [f"arithmetic: {e}" for e in arithmetic_errors(answer)]
    years = foreign_years(answer, question, data, context, today=today)
    if years:
        reasons.append("the answer names year(s) " + ", ".join(years)
                       + ", which appear nowhere in the question or the data")
    gap = missing_parts(question, answer)
    if gap:
        reasons.append(gap)
    return reasons
