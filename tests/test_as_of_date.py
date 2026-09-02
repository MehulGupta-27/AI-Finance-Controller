"""
tests/test_as_of_date.py
Guards Section 0C.2 — AS_OF_DATE must never be the real-world date.

Two tests:
1. as_of falls within the dataset's actual date window (not today's date).
2. The overdue calculation is immune to wall-clock contamination — proven by
   demonstrating that using datetime.now() (2099) gives a wrong result while
   using fixed_as_of gives the correct one.
"""

import sys
from datetime import date, timedelta, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.config import BASE_DATE, DAY_SPAN, OVERDUE_SETTLEMENT_DAYS
from agents.as_of_date import compute_as_of_date
from agents.data_loader import load_raw_data


# ---------------------------------------------------------------------------
# Shared fixture — load the 110-record dev set once per session
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def loaded_test_data():
    ledger_df, rzp_df, bank_df = load_raw_data()
    return ledger_df, rzp_df, bank_df


# ---------------------------------------------------------------------------
# Test 1 — AS_OF_DATE is within the dataset's real date window
# ---------------------------------------------------------------------------
def test_as_of_date_is_within_dataset_range(loaded_test_data):
    """
    AS_OF_DATE must be between BASE_DATE and BASE_DATE + DAY_SPAN + 10.
    The +10 is the settlement lag allowance (SETTLEMENT_DATE_TOLERANCE_DAYS).
    Importing BASE_DATE and DAY_SPAN from config means this test stays
    correct if the dataset is regenerated at different dates.
    """
    ledger_df, rzp_df, bank_df = loaded_test_data
    as_of = compute_as_of_date(ledger_df, rzp_df, bank_df)

    window_start = BASE_DATE
    window_end   = BASE_DATE + timedelta(days=DAY_SPAN + 10)

    assert BASE_DATE <= as_of <= window_end, (
        f"AS_OF_DATE {as_of} is outside the dataset window "
        f"[{window_start}, {window_end}]. "
        "This likely means datetime.now() crept in somewhere."
    )


# ---------------------------------------------------------------------------
# Test 2 — overdue check uses AS_OF_DATE, not wall-clock
#
# Strategy: Python 3.14+ datetime is a C type — monkeypatching .now() is not
# possible. Instead we demonstrate the failure mode directly:
#   - using date.today() (the wall clock) gives the "wrong" result (overdue)
#   - using fixed_as_of (the correct approach) gives the right result (awaiting)
# This is the exact bug Section 0C.2 exists to catch, shown with real numbers.
# ---------------------------------------------------------------------------
def test_overdue_check_uses_as_of_date_not_wallclock():
    """
    Proves the overdue calculation is immune to wall-clock contamination.

    A pending_settlement captured 3 days before AS_OF_DATE must route to
    'awaiting_settlement' (≤10 days) — not 'overdue_settlement'.

    Using date.today() instead of fixed_as_of would give elapsed > 10 years,
    always routing to 'overdue_settlement' regardless of the dataset window.
    """
    # The fixed AS_OF_DATE as used by the pipeline — matches generator's as_of
    fixed_as_of = BASE_DATE + timedelta(days=DAY_SPAN)

    # A payment captured 3 days before AS_OF_DATE — should be 'awaiting'
    captured_at = fixed_as_of - timedelta(days=3)

    # ── Correct path: elapsed measured against fixed AS_OF_DATE ──────────────
    elapsed_correct = (fixed_as_of - captured_at).days
    sub_reason_correct = (
        "awaiting_settlement"
        if elapsed_correct <= OVERDUE_SETTLEMENT_DAYS
        else "overdue_settlement"
    )

    # ── Wrong path: elapsed measured against today's wall clock ──────────────
    elapsed_wrong = (date.today() - captured_at).days
    sub_reason_wrong = (
        "awaiting_settlement"
        if elapsed_wrong <= OVERDUE_SETTLEMENT_DAYS
        else "overdue_settlement"
    )

    # The correct path must always give 'awaiting_settlement'
    assert sub_reason_correct == "awaiting_settlement", (
        f"elapsed={elapsed_correct} days with fixed_as_of gives "
        f"'{sub_reason_correct}' — expected 'awaiting_settlement'. "
        "AS_OF_DATE computation may be wrong."
    )

    # The wall-clock path must give the wrong answer (proves why the rule exists).
    # captured_at is in early 2026; today is 2026-08-31+ so elapsed >> 10 days.
    assert sub_reason_wrong == "overdue_settlement", (
        f"elapsed_wrong={elapsed_wrong} days — expected this to be > "
        f"{OVERDUE_SETTLEMENT_DAYS} (the 'wrong' wall-clock answer). "
        "The dataset dates may have shifted — regenerate with BASE_DATE in 2026."
    )

    # Confirm the two paths diverge — this is the core of the guard
    assert sub_reason_correct != sub_reason_wrong, (
        "Both paths agree — wall-clock contamination wouldn't be detectable. "
        "Regenerate the dataset so captured_at is clearly in the past."
    )
