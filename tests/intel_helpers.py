"""
tests/intel_helpers.py
======================
Synthetic ledger rows with known answers, for the src/intelligence tests.

Not a test module (no `test_` prefix). Every row is shaped exactly like a
`predictions` row from src/core/ledger.py, so the code under test reads it
the way it reads production.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

SAME_SERIES_NOTE = ("graded on the close of {ra}; return: adjusted close {a} -> {ra} "
                    "(information session recorded at creation)")


def row(i: int, *, source: str = "technical", subject: str = "AAPL",
        direction: str = "bull", created: str = "2026-08-03T22:00:00",
        horizon: int = 10, resolved: bool = True, correct=1,
        actual_return=0.01, kind: str = "none", p_correct=None,
        note: str | None = None, resolve_after: str | None = None,
        resolved_at: str | None = None, information_session: str | None = None,
        **extra) -> dict:
    c = datetime.fromisoformat(created)
    ra = resolve_after or (c + timedelta(days=horizon * 7 / 5)).date().isoformat()
    rat = resolved_at or (f"{(date.fromisoformat(ra) + timedelta(days=1)).isoformat()}T12:00:00"
                          if resolved else None)
    sess = information_session or c.date().isoformat()
    r = {
        "id": f"p{i:05d}", "created_at": created, "source": source, "subject": subject,
        "claim": f"{direction} test call", "direction": direction,
        "probability": p_correct, "confidence": p_correct, "horizon_days": horizon,
        "resolve_after": ra, "price_at": 100.0, "regime_at": "Expansion (Goldilocks)",
        "strategy": source, "rationale": "", "evidence": None, "invalidation": "",
        "resolved": int(bool(resolved)), "resolved_at": rat,
        "price_end": (100.0 * (1 + actual_return)) if (resolved and actual_return is not None) else None,
        "actual_return": actual_return if resolved else None,
        "correct": (int(bool(correct)) if correct is not None else None) if resolved else None,
        "error": None,
        "outcome_note": (note if note is not None else
                         SAME_SERIES_NOTE.format(ra=ra, a=sess)) if resolved else None,
        "external_ref": f"ref:{source}:{i}", "provider_symbol": subject,
        "identity_basis": "UNAMBIGUOUS", "probability_kind": kind, "p_correct": p_correct,
        "model_version": f"{source}|code=abc123abc123", "data_as_of": c.date().isoformat(),
        "information_session": sess,
        "regime_basis": "src.macro.regime.current at creation; inputs as of x; coverage 1.00",
    }
    r.update(extra)
    return r


def ticker(j: int) -> str:
    """A distinct, alphabetic bare ticker (a US listing to the resolver)."""
    return "Q" + chr(65 + (j // 26) % 26) + chr(65 + j % 26)


def business_days(start: str, n: int) -> list[str]:
    d = date.fromisoformat(start)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def clustered_record(n_dates: int, per_date: int, hit_rate_by_date, *, source="technical",
                     start="2026-03-02", direction_fn=None, horizon=10,
                     subject_fn=None, actual_return_fn=None, kind="none", p_fn=None) -> list:
    """`per_date` calls on each of `n_dates` information dates. `hit_rate_by_date`
    is a callable date_index -> fraction of that date's calls that are right."""
    rows, i = [], 0
    for k, d in enumerate(business_days(start, n_dates)):
        right = round(hit_rate_by_date(k) * per_date)
        for j in range(per_date):
            ok = j < right
            direction = direction_fn(k, j) if direction_fn else "bull"
            ret = (actual_return_fn(k, j, ok, direction) if actual_return_fn else
                   ((0.02 if ok else -0.02) if direction == "bull" else (-0.02 if ok else 0.02)))
            rows.append(row(i, source=source, created=f"{d}T22:00:00", correct=int(ok),
                            direction=direction, horizon=horizon,
                            subject=subject_fn(k, j) if subject_fn else ticker(j),
                            actual_return=ret, kind=kind,
                            p_correct=p_fn(k, j, ok) if p_fn else None,
                            information_session=d,
                            external_ref=f"ref:{source}:{i}"))
            i += 1
    return rows
