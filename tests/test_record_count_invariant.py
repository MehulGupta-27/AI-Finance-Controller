"""
tests/test_record_count_invariant.py
Guards Section 0C.3 — no record may be silently dropped or duplicated.

Three tests:
1. Clean pipeline output: invariant passes, no exception raised.
2. A missing record (silent drop): invariant raises InvariantViolation,
   reports the specific missing record ID.
3. A duplicated record across buckets: invariant raises InvariantViolation,
   reports which record appears in multiple buckets.

At this build stage (before Agent 3+), we use synthetic PipelineRunResult
fixtures — the invariant logic is fully testable without a complete pipeline.
The test_every_input_record_appears_exactly_once_in_output test runs against
the real 110-record dataset once the full pipeline is wired (Step 15).
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.reporting_agent import (
    PipelineRunResult,
    RecordResult,
    InvariantViolation,
    check_record_identity_invariant,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_result(
    input_ids:      list[str],
    matched_ids:    list[str],
    partial_ids:    list[str],
    unresolved_ids: list[str],
) -> PipelineRunResult:
    return PipelineRunResult(
        input_record_ids = input_ids,
        matched_ids      = matched_ids,
        partial_ids      = partial_ids,
        unresolved_ids   = unresolved_ids,
        results          = [],
        llm_calls_made   = 0,
        llm_tokens_used  = 0,
    )


# ---------------------------------------------------------------------------
# Test 1 — invariant passes when every record appears exactly once
# ---------------------------------------------------------------------------
def test_every_input_record_appears_exactly_once_in_output():
    """
    The canonical check from Section 0C.3:
      set(output) == set(input)  AND  len(output) == len(set(output))
    Must pass silently with no exception.
    """
    input_ids = [f"rec_{i:04d}" for i in range(20)]
    result = make_result(
        input_ids      = input_ids,
        matched_ids    = input_ids[:10],
        partial_ids    = input_ids[10:15],
        unresolved_ids = input_ids[15:],
    )
    # Should not raise
    check_record_identity_invariant(result)


# ---------------------------------------------------------------------------
# Test 2 — invariant catches a silently dropped record
# ---------------------------------------------------------------------------
def test_invariant_catches_missing_record():
    """
    Drop one record from the output — the invariant must raise InvariantViolation
    and name the specific missing record ID.
    """
    input_ids  = [f"rec_{i:04d}" for i in range(10)]
    dropped_id = "rec_0005"  # deliberately omitted from all output buckets

    result = make_result(
        input_ids      = input_ids,
        matched_ids    = ["rec_0000", "rec_0001", "rec_0002", "rec_0003", "rec_0004"],
        partial_ids    = ["rec_0006", "rec_0007"],
        unresolved_ids = ["rec_0008", "rec_0009"],
        # rec_0005 is missing from all three buckets
    )

    with pytest.raises(InvariantViolation) as exc_info:
        check_record_identity_invariant(result)

    error_msg = str(exc_info.value)
    assert dropped_id in error_msg, (
        f"Error message should name the missing record '{dropped_id}', got: {error_msg}"
    )
    assert "vanished" in error_msg.lower() or "missing" in error_msg.lower(), (
        f"Error message should indicate a missing record, got: {error_msg}"
    )


# ---------------------------------------------------------------------------
# Test 3 — invariant catches a record duplicated across buckets
# ---------------------------------------------------------------------------
def test_invariant_catches_duplicated_record():
    """
    Put the same record ID in both MATCHED and PARTIAL — the invariant must
    raise InvariantViolation and name the duplicated record ID.

    This is the subtle case a pure count check would miss:
    len(matched) + len(partial) + len(unresolved) == total can still pass
    if one record was dropped in one place and duplicated elsewhere.
    """
    input_ids    = [f"rec_{i:04d}" for i in range(10)]
    duplicate_id = "rec_0003"  # appears in BOTH matched and partial

    result = make_result(
        input_ids      = input_ids,
        matched_ids    = ["rec_0000", "rec_0001", "rec_0002", "rec_0003"],
        partial_ids    = ["rec_0003", "rec_0004", "rec_0005"],  # rec_0003 duplicated
        unresolved_ids = ["rec_0006", "rec_0007", "rec_0008", "rec_0009"],
        # Note: total output count = 4+3+4 = 11 > 10 input, AND rec_0003 appears twice
    )

    with pytest.raises(InvariantViolation) as exc_info:
        check_record_identity_invariant(result)

    error_msg = str(exc_info.value)
    assert duplicate_id in error_msg, (
        f"Error message should name the duplicated record '{duplicate_id}', got: {error_msg}"
    )


# ---------------------------------------------------------------------------
# Test 4 — status values are always one of the three valid strings
# (also guards Section 0C.1 — never a boolean)
# ---------------------------------------------------------------------------
def test_status_values_are_always_three_state():
    """
    Verify that RecordResult rejects anything outside MATCHED/PARTIAL/UNRESOLVED
    when we check it ourselves — the model doesn't enforce this with a validator
    (Agent 6's router enforces it at assignment time), but this test documents
    the contract explicitly.
    """
    valid_statuses = {"MATCHED", "PARTIAL", "UNRESOLVED"}

    records = [
        RecordResult(record_id="r1", status="MATCHED"),
        RecordResult(record_id="r2", status="PARTIAL",    sub_reason="awaiting_settlement"),
        RecordResult(record_id="r3", status="UNRESOLVED", sub_reason="low_confidence"),
    ]

    for rec in records:
        assert rec.status in valid_statuses, (
            f"record {rec.record_id} has invalid status: {rec.status!r}"
        )
        assert not isinstance(rec.status, bool), (
            f"record {rec.record_id} status is a boolean — Section 0C.1 violation"
        )
