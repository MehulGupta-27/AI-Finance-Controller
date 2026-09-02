"""
tests/test_three_state_output.py
Guards Section 0C.1 — status must never be a boolean or any value outside
MATCHED / PARTIAL / UNRESOLVED.

Tests the router's routing functions directly with synthetic inputs so this
runs without any LLM calls and stays fast.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.router import (
    RouteResult,
    route_pending_settlement,
    route_unidentified_bank_credit,
    route_missing_ledger,
)
from agents.ingestion_agent import CanonicalRecord
from agents.fuzzy_match_agent import FuzzyMatchPair, FuzzyScores
from agents.config import BASE_DATE, DAY_SPAN, OVERDUE_SETTLEMENT_DAYS

FIXED_AS_OF = BASE_DATE + timedelta(days=DAY_SPAN)
VALID_STATUSES = {"MATCHED", "PARTIAL", "UNRESOLVED"}


def _make_rzp_record(record_id="rzp-test-001", amount=1500.0, days_ago=3):
    captured = FIXED_AS_OF - timedelta(days=days_ago)
    return CanonicalRecord(
        record_id  = record_id,
        source     = "razorpay",
        source_ref = "pay_test001",
        order_id   = "ORD_TEST",
        amount     = amount,
        date       = captured,
        text_field = "card",
        notes      = "",
        status     = "captured",
        raw        = {"rzp_fee": 35.40, "refund_amount": 0.0},
    )


def _make_bank_record(record_id="bank-test-001", amount=1464.60):
    return CanonicalRecord(
        record_id  = record_id,
        source     = "bank",
        source_ref = "UTR_TEST001",
        order_id   = None,
        amount     = amount,
        date       = FIXED_AS_OF - timedelta(days=1),
        text_field = "PG SETL 123",
        notes      = "",
        status     = "NEFT",
        raw        = {},
    )


def _make_ledger_record(record_id="led-test-001", amount=1500.0):
    return CanonicalRecord(
        record_id  = record_id,
        source     = "ledger",
        source_ref = "LED_TEST001",
        order_id   = "ORD_TEST",
        amount     = amount,
        date       = FIXED_AS_OF - timedelta(days=3),
        text_field = "Test Customer",
        notes      = "",
        status     = "paid",
        raw        = {"refund_amount": 0.0, "status": "paid"},
    )


def _make_fuzzy_pair(rzp=None, bank=None, ledger=None, score=0.85):
    rzp    = rzp    or _make_rzp_record()
    bank   = bank   or _make_bank_record()
    ledger = ledger or _make_ledger_record()
    return FuzzyMatchPair(
        rzp_record            = rzp,
        bank_record           = bank,
        predicted_settlement  = 1464.60,
        scores                = FuzzyScores(amount_score=1.0, date_score=0.80, text_score=0.20, composite=score),
        ledger_record         = ledger,
        refund_amount         = 0.0,
    )


# ---------------------------------------------------------------------------
# Test 1 — every routing function returns one of the three valid statuses
# ---------------------------------------------------------------------------

def test_pending_settlement_within_window_is_partial():
    rzp = _make_rzp_record(days_ago=3)
    result = route_pending_settlement(rzp, FIXED_AS_OF)
    assert result.status in VALID_STATUSES
    assert not isinstance(result.status, bool)
    assert result.status == "PARTIAL"
    assert result.sub_reason == "awaiting_settlement"


def test_pending_settlement_overdue_is_unresolved():
    rzp = _make_rzp_record(days_ago=OVERDUE_SETTLEMENT_DAYS + 2)
    result = route_pending_settlement(rzp, FIXED_AS_OF)
    assert result.status in VALID_STATUSES
    assert not isinstance(result.status, bool)
    assert result.status == "UNRESOLVED"
    assert result.sub_reason == "overdue_settlement"


def test_unidentified_bank_credit_is_unresolved():
    bank = _make_bank_record()
    result = route_unidentified_bank_credit(bank)
    assert result.status in VALID_STATUSES
    assert not isinstance(result.status, bool)
    assert result.status == "UNRESOLVED"
    assert result.sub_reason == "unidentified_bank_credit"


def test_missing_ledger_is_partial():
    pair = _make_fuzzy_pair()
    result = route_missing_ledger(pair, FIXED_AS_OF)
    assert result.status in VALID_STATUSES
    assert not isinstance(result.status, bool)
    assert result.status == "PARTIAL"
    assert result.sub_reason == "no_ledger_record"


# ---------------------------------------------------------------------------
# Test 2 — status field is always a string, never a boolean
# ---------------------------------------------------------------------------

def test_status_is_never_boolean():
    """
    Exhaustively verify that all routing functions return a string status,
    not True/False. This is the exact Section 0C.1 invariant.
    """
    results = [
        route_pending_settlement(_make_rzp_record(days_ago=3),  FIXED_AS_OF),
        route_pending_settlement(_make_rzp_record(days_ago=15), FIXED_AS_OF),
        route_unidentified_bank_credit(_make_bank_record()),
        route_missing_ledger(_make_fuzzy_pair(), FIXED_AS_OF),
    ]
    for r in results:
        assert isinstance(r.status, str),  f"status is not a string: {type(r.status)!r} = {r.status!r}"
        assert r.status != True,           f"status is boolean True"
        assert r.status != False,          f"status is boolean False"
        assert r.status in VALID_STATUSES, f"status {r.status!r} not in {VALID_STATUSES}"


# ---------------------------------------------------------------------------
# Test 3 — explanation is always present and headline is non-empty
# ---------------------------------------------------------------------------

def test_every_route_result_has_explanation():
    results = [
        route_pending_settlement(_make_rzp_record(days_ago=3),  FIXED_AS_OF),
        route_pending_settlement(_make_rzp_record(days_ago=15), FIXED_AS_OF),
        route_unidentified_bank_credit(_make_bank_record()),
        route_missing_ledger(_make_fuzzy_pair(), FIXED_AS_OF),
    ]
    for r in results:
        assert r.explanation is not None
        assert r.explanation.headline, f"Empty headline for status={r.status} sub={r.sub_reason}"
        assert len(r.explanation.checklist) > 0, f"Empty checklist for status={r.status}"
        if r.status in ("PARTIAL", "UNRESOLVED"):
            assert r.explanation.recommendation, \
                f"Missing recommendation for {r.status}/{r.sub_reason}"
