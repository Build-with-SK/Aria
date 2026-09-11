"""
Alpaca order-status parsing.

This exists because of a bug found during runtime verification against the
live paper account: `str(order.status)` on an alpaca-py enum returns
"OrderStatus.FILLED", not "filled", so every status lookup missed the table
and fell through to the SUBMITTED default. A real filled order reported
SUBMITTED with filled_qty=1.

The visible symptom was cosmetic. The consequence was not: `_await_fill()`
in order_manager waits for FILLED before placing the protective stop and
take-profit, so a filled position sat unprotected until the poll gave up —
and a rejected order looked like one still working.
"""
from __future__ import annotations

import pytest

from src.execution.alpaca_broker import _parse_status, _status_token
from src.execution.broker_base import OrderStatus


class _FakeEnum:
    """Stands in for alpaca-py's OrderStatus enum: `.value` is the wire word,
    `str()` is the prefixed repr."""
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return f"OrderStatus.{self.value.upper()}"


# ── the token extractor ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("filled", "filled"),
    ("FILLED", "filled"),
    ("OrderStatus.FILLED", "filled"),        # the shape that caused the bug
    ("OrderStatus.PARTIALLY_FILLED", "partially_filled"),
    ("  Filled  ", "filled"),
    (None, ""),
])
def test_status_token_normalises_every_shape(raw, expected):
    assert _status_token(raw) == expected


def test_status_token_reads_the_enum_itself():
    assert _status_token(_FakeEnum("filled")) == "filled"


# ── the mapping ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["filled", "FILLED", "OrderStatus.FILLED"])
def test_a_fill_is_a_fill_however_it_is_spelled(raw):
    """The regression itself. If this fails, protective brackets stop being
    placed on filled positions."""
    assert _parse_status(raw) is OrderStatus.FILLED


def test_the_enum_object_parses():
    assert _parse_status(_FakeEnum("filled")) is OrderStatus.FILLED
    assert _parse_status(_FakeEnum("rejected")) is OrderStatus.REJECTED


@pytest.mark.parametrize("raw,expected", [
    ("new",               OrderStatus.SUBMITTED),
    ("accepted",          OrderStatus.SUBMITTED),
    ("pending_new",       OrderStatus.SUBMITTED),   # seen on a real paper sell
    ("partially_filled",  OrderStatus.PARTIAL),
    ("filled",            OrderStatus.FILLED),
    ("canceled",          OrderStatus.CANCELLED),
    ("expired",           OrderStatus.EXPIRED),
    ("rejected",          OrderStatus.REJECTED),
    ("done_for_day",      OrderStatus.EXPIRED),
])
def test_known_vocabulary(raw, expected):
    assert _parse_status(raw) is expected


def test_unknown_status_is_treated_as_still_working():
    """Never FILLED (would place brackets on nothing) and never CANCELLED
    (would abandon a live order)."""
    assert _parse_status("some_new_alpaca_word") is OrderStatus.SUBMITTED
    assert _parse_status("") is OrderStatus.SUBMITTED
    assert _parse_status(None) is OrderStatus.SUBMITTED


# ── the fill counter breaks ties ─────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["canceled", "expired", "rejected"])
def test_a_part_filled_then_killed_order_is_PARTIAL_not_dead(raw):
    """Cancelled after a part-fill still leaves shares in the book. Reporting
    CANCELLED there tells the caller it owns nothing, and the position goes
    unmanaged."""
    assert _parse_status(raw, filled_qty=3.0) is OrderStatus.PARTIAL


def test_a_clean_cancel_with_no_fill_stays_cancelled():
    assert _parse_status("canceled", filled_qty=0.0) is OrderStatus.CANCELLED


def test_a_fill_counter_does_not_upgrade_a_working_order():
    """partially_filled must stay PARTIAL, not become FILLED."""
    assert _parse_status("partially_filled", filled_qty=2.0) is OrderStatus.PARTIAL
    assert _parse_status("accepted", filled_qty=0.0) is OrderStatus.SUBMITTED
