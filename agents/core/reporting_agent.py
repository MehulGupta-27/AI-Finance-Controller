"""
agents/reporting_agent.py  —  Agent 8
Reporting & Scoring Agent.

This is the ONLY file permitted to import ground_truth.json (Section 9).
No other agent may touch data/ground_truth/.

This module has two distinct phases:
  Phase A (active now):  Record identity invariant check (Section 0C.3)
                         Runs automatically after every pipeline execution.
  Phase B (after all agents complete):  Full multi-class scoring,
                         confusion matrix, exception list, cost report.

The invariant check is deliberately wired up early (Section 11 step 8)
so a silent record-drop bug is caught on the first partial pipeline run,
not discovered during final accuracy evaluation.

Ground truth mapping (Section 8 / Section 5 Agent 8):
  clean_triple_match       → MATCHED
  delayed_settlement       → MATCHED
  hard_garbled_narration   → MATCHED
  duplicate_capture        → MATCHED
  partial_refund_split     → MATCHED  (resolves at Agent 3 via refund_amount col)
  adversarial_near_miss    → MATCHED
  semantic_brand_narration → MATCHED  (via Agent 4 semantic_similarity)
  failed_payment_orphan    → MATCHED, sub_reason="no_action_needed"
  pending_settlement       → PARTIAL, sub_reason="awaiting_settlement"
  missing_from_ledger      → PARTIAL, sub_reason="no_ledger_record"
  unidentified_bank_credit → UNRESOLVED, sub_reason="unidentified_bank_credit"
"""

import json
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pydantic import BaseModel

logger = logging.getLogger(__name__)

GROUND_TRUTH_PATH = _ROOT / "data" / "ground_truth" / "ground_truth.json"


# ---------------------------------------------------------------------------
# Pipeline result container — what every agent hands to reporting
# ---------------------------------------------------------------------------

class RecordResult(BaseModel):
    """Minimal result record: one per input case, always one of three statuses."""
    record_id:   str
    case_id:     Optional[str]   = None
    status:      str             # MATCHED | PARTIAL | UNRESOLVED — never bool
    sub_reason:  Optional[str]   = None
    confidence:  Optional[float] = None
    source:      Optional[str]   = None   # which agent resolved it

    model_config = {"arbitrary_types_allowed": True}


class PipelineRunResult(BaseModel):
    """Aggregated output of a full pipeline run — passed to reporting_agent."""
    # Record IDs that entered the pipeline (from ingestion)
    input_record_ids:  list[str]

    # Per-status record ID lists — these three must together equal input_record_ids
    matched_ids:    list[str]
    partial_ids:    list[str]
    unresolved_ids: list[str]

    # Full result objects for scoring and reporting
    results: list[RecordResult]

    # Optional metadata
    as_of_date:            Optional[str]   = None  # YYYY-MM-DD from compute_as_of_date()
    total_runtime_seconds: Optional[float] = None
    llm_calls_made:        int = 0
    llm_tokens_used:       int = 0

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Section 0C.3 — Record Identity Invariant
# Must run automatically after every pipeline execution.
# ---------------------------------------------------------------------------

class InvariantViolation(Exception):
    """Raised when the record identity invariant fails — halts the pipeline."""
    pass


def check_record_identity_invariant(result: PipelineRunResult) -> None:
    """
    Verify that:
    1. The exact set of output record IDs equals the exact set of input IDs.
       (No silent drops, no records added from nowhere.)
    2. No record ID appears in more than one status bucket.
       (No duplications across MATCHED/PARTIAL/UNRESOLVED.)

    This is stronger than a count check — len(matched)+len(partial)+len(unresolved)==total
    can pass even if one record was dropped and another duplicated, since counts cancel.
    Set membership catches both failure modes.

    Raises InvariantViolation with the specific offending record IDs.
    Never continues silently on failure (Section 0C.3).
    """
    input_ids  = set(result.input_record_ids)
    all_output = result.matched_ids + result.partial_ids + result.unresolved_ids
    output_ids = set(all_output)

    # --- Check 1: missing records (silent drops) ---
    missing = input_ids - output_ids
    if missing:
        msg = (
            f"{len(missing)} record(s) vanished from the pipeline "
            f"(in input, not in output):\n"
            + "\n".join(f"  - {rid}" for rid in sorted(missing))
        )
        logger.error("INVARIANT VIOLATION: %s", msg)
        raise InvariantViolation(msg)

    # --- Check 2: phantom records (appeared from nowhere) ---
    phantom = output_ids - input_ids
    if phantom:
        msg = (
            f"{len(phantom)} record(s) appeared in output but were not in input:\n"
            + "\n".join(f"  - {rid}" for rid in sorted(phantom))
        )
        logger.error("INVARIANT VIOLATION: %s", msg)
        raise InvariantViolation(msg)

    # --- Check 3: duplicates across buckets ---
    seen:     dict[str, list[str]] = {}
    buckets = [
        ("MATCHED",    result.matched_ids),
        ("PARTIAL",    result.partial_ids),
        ("UNRESOLVED", result.unresolved_ids),
    ]
    for bucket_name, ids in buckets:
        for rid in ids:
            seen.setdefault(rid, []).append(bucket_name)

    duplicated = {rid: buckets_list for rid, buckets_list in seen.items()
                  if len(buckets_list) > 1}
    if duplicated:
        lines = [f"  - {rid} appears in: {', '.join(bkts)}"
                 for rid, bkts in sorted(duplicated.items())]
        msg = (
            f"{len(duplicated)} record(s) appear in more than one status bucket:\n"
            + "\n".join(lines)
        )
        logger.error("INVARIANT VIOLATION: %s", msg)
        raise InvariantViolation(msg)

    logger.info(
        "Record identity invariant: OK — %d records in, %d out "
        "(%d MATCHED / %d PARTIAL / %d UNRESOLVED), no missing, no duplicates",
        len(input_ids),
        len(all_output),
        len(result.matched_ids),
        len(result.partial_ids),
        len(result.unresolved_ids),
    )


# ---------------------------------------------------------------------------
# Phase A: basic summary report (works with partial pipeline output)
# ---------------------------------------------------------------------------

def basic_summary(result: PipelineRunResult) -> str:
    """
    Print and return a dashboard summary.
    Works at any pipeline stage — just shows what's available.
    """
    total    = len(result.input_record_ids)
    matched  = len(result.matched_ids)
    partial  = len(result.partial_ids)
    unresolved = len(result.unresolved_ids)
    accounted = matched + partial + unresolved

    match_rate = (matched / total * 100) if total > 0 else 0.0
    runtime    = (
        f"{result.total_runtime_seconds:.1f}s"
        if result.total_runtime_seconds is not None
        else "n/a"
    )

    lines = [
        "",
        "=" * 55,
        "  PIPELINE RUN SUMMARY",
        "=" * 55,
        f"  Records processed  : {total}",
        f"  Accounted for      : {accounted}",
        f"  Matched            : {matched}",
        f"  Partial            : {partial}",
        f"  Unresolved         : {unresolved}",
        f"  Match Rate         : {match_rate:.1f}%",
        f"  Pipeline logic time: {runtime}",
        f"  LLM calls          : {result.llm_calls_made}",
        f"  LLM tokens used    : {result.llm_tokens_used}",
        "=" * 55,
        "",
    ]

    report = "\n".join(lines)
    logger.info(report)
    return report


# ---------------------------------------------------------------------------
# Phase B: Full scoring against ground truth (activated once all agents done)
# ONLY consumer of ground_truth.json — no other module may import it.
# ---------------------------------------------------------------------------

def _load_ground_truth(gt_path: Path = GROUND_TRUTH_PATH) -> list[dict]:
    """Load ground_truth.json. Raises FileNotFoundError if absent."""
    if not gt_path.exists():
        raise FileNotFoundError(
            f"ground_truth.json not found at {gt_path}. "
            "Run data/generator/generate_dataset.py first."
        )
    with open(gt_path) as f:
        return json.load(f)


def score_against_ground_truth(
    result:     PipelineRunResult,
    case_id_map: dict[str, str],   # record_id → case_id
    gt_path:    Path = GROUND_TRUTH_PATH,
) -> dict:
    """
    Full multi-class scoring. Activated in Phase B once all agents are complete.

    Parameters
    ----------
    result       : PipelineRunResult from a full pipeline run
    case_id_map  : maps each record_id to its case_id (from ground truth)
    gt_path      : path to ground_truth.json (default)

    Returns
    -------
    dict with keys: classification_report, confusion_matrix,
                    per_case_type_counts, correct, total, accuracy
    """
    from sklearn.metrics import classification_report, confusion_matrix

    gt_entries = _load_ground_truth(gt_path)
    gt_by_case = {g["case_id"]: g for g in gt_entries}

    y_true, y_pred = [], []
    per_case_type:  dict[str, Counter] = {}

    for res in result.results:
        case_id = case_id_map.get(res.record_id)
        if not case_id or case_id not in gt_by_case:
            continue
        gt = gt_by_case[case_id]
        expected = gt["expected_status"]
        predicted = res.status

        y_true.append(expected)
        y_pred.append(predicted)

        ct = gt.get("case_type", "unknown")
        per_case_type.setdefault(ct, Counter())
        per_case_type[ct][f"{expected}→{predicted}"] += 1

    if not y_true:
        logger.warning("No scored records — case_id_map may be empty or mismatched")
        return {}

    labels = ["MATCHED", "PARTIAL", "UNRESOLVED"]
    report = classification_report(y_true, y_pred, labels=labels, zero_division=0)
    cm     = confusion_matrix(y_true, y_pred, labels=labels).tolist()
    correct = sum(1 for t, p in zip(y_true, y_pred) if t == p)

    return {
        "classification_report": report,
        "confusion_matrix":      cm,
        "confusion_labels":      labels,
        "per_case_type_counts":  {k: dict(v) for k, v in per_case_type.items()},
        "correct":               correct,
        "total":                 len(y_true),
        "accuracy":              correct / len(y_true),
    }


# ---------------------------------------------------------------------------
# Standalone smoke-test — runs the invariant check on a synthetic result
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # --- Test 1: invariant passes on clean synthetic data ---
    ids = [f"rec_{i:04d}" for i in range(10)]
    good = PipelineRunResult(
        input_record_ids = ids,
        matched_ids      = ids[:6],
        partial_ids      = ids[6:8],
        unresolved_ids   = ids[8:],
        results          = [],
        llm_calls_made   = 0,
        llm_tokens_used  = 0,
    )
    check_record_identity_invariant(good)
    print("✓ Invariant passed on clean data")

    # --- Test 2: invariant catches a missing record ---
    bad_missing = PipelineRunResult(
        input_record_ids = ids,
        matched_ids      = ids[:5],   # dropped ids[5]
        partial_ids      = ids[6:8],
        unresolved_ids   = ids[8:],
        results          = [],
    )
    try:
        check_record_identity_invariant(bad_missing)
        print("✗ Should have raised InvariantViolation for missing record")
    except InvariantViolation as e:
        print(f"✓ Invariant correctly caught missing record: {str(e)[:80]}...")

    # --- Test 3: invariant catches a duplicated record ---
    bad_dup = PipelineRunResult(
        input_record_ids = ids,
        matched_ids      = ids[:6],
        partial_ids      = ids[5:8],  # ids[5] is in both matched and partial
        unresolved_ids   = ids[8:],
        results          = [],
    )
    try:
        check_record_identity_invariant(bad_dup)
        print("✗ Should have raised InvariantViolation for duplicated record")
    except InvariantViolation as e:
        print(f"✓ Invariant correctly caught duplicated record: {str(e)[:80]}...")

    # --- Summary report smoke-test ---
    summary = basic_summary(good)
    print(summary)
    print("=== reporting_agent smoke test OK ===")


# ---------------------------------------------------------------------------
# Section 8B — Cash Flow Forecast
# ---------------------------------------------------------------------------

def forecast_cash_inflow(
    results: list[RecordResult],
    raw_lookup: dict[str, dict],  # record_id → {customer, amount, date, ...}
    as_of_date,  # date object, never datetime.now()
) -> dict:
    """
    Forecast expected cash inflows based on median settlement lag computed
    from this run's own MATCHED records (Section 8B).
    
    Key requirements (per spec):
    - Median settlement lag computed from MATCHED records, not hardcoded
    - Uses AS_OF_DATE for all date calculations, never datetime.now()
    - Deterministic: identical input → identical output
    - Only forecasts PARTIAL records with sub_reason="awaiting_settlement"
    
    Parameters
    ----------
    results    : list of RecordResult from pipeline
    raw_lookup : dict mapping record_id to raw fields (amount, date, customer)
    as_of_date : fixed date from compute_as_of_date(), never wall clock
    
    Returns
    -------
    dict:
        median_settlement_lag_days: int
        pending_settlements: list of dicts with forecast details
        expected_inflow_next_7_days: float
        expected_inflow_next_30_days: float
    """
    import statistics
    from datetime import timedelta
    
    # Step 1: Compute median settlement lag from MATCHED records
    settlement_lags = []
    
    for result in results:
        if result.status != "MATCHED":
            continue
        
        raw = raw_lookup.get(result.record_id, {})
        captured_date = raw.get("captured_date")  # from Razorpay
        settled_date  = raw.get("settled_date")   # from Bank
        
        if captured_date and settled_date:
            # Both are date objects from ingestion
            lag = (settled_date - captured_date).days
            if lag >= 0:  # Only positive lags (settlement after capture)
                settlement_lags.append(lag)
    
    if not settlement_lags:
        # No MATCHED records with valid settlement data
        # Return empty forecast
        return {
            "median_settlement_lag_days": 0,
            "pending_settlements": [],
            "expected_inflow_next_7_days": 0.0,
            "expected_inflow_next_30_days": 0.0,
            "note": "No MATCHED records available to compute settlement lag"
        }
    
    median_lag_days = int(statistics.median(settlement_lags))
    
    # Step 2: Forecast pending settlements
    pending = []
    inflow_7d = 0.0
    inflow_30d = 0.0
    
    for result in results:
        if result.status == "PARTIAL" and result.sub_reason == "awaiting_settlement":
            raw = raw_lookup.get(result.record_id, {})
            captured_date = raw.get("captured_date")
            amount = float(raw.get("amount", 0))
            customer = raw.get("customer", "")
            order_id = raw.get("order_id", "")
            
            if not captured_date:
                continue
            
            # Expected settlement date = captured_date + median_lag
            expected_settlement = captured_date + timedelta(days=median_lag_days)
            
            # Section 8B clamping: if expected_settlement < AS_OF_DATE, clamp to as_of_date + 1
            # Never exclude overdue records - they're expected "now" (tomorrow)
            if expected_settlement < as_of_date:
                expected_settlement = as_of_date + timedelta(days=1)
            
            # Days since capture (relative to AS_OF_DATE, not wall clock)
            days_since = (as_of_date - captured_date).days
            
            # Days until expected settlement (after clamping)
            days_until = (expected_settlement - as_of_date).days
            
            pending.append({
                "order_id": order_id,
                "customer": customer,
                "amount": round(amount, 2),
                "captured_date": str(captured_date),
                "expected_settlement_date": str(expected_settlement),
                "days_since_capture": days_since,
                "days_until_settlement": days_until,
            })
            
            # Accumulate expected inflows (after clamping, days_until is always >= 1)
            if 0 <= days_until <= 7:
                inflow_7d += amount
            if 0 <= days_until <= 30:
                inflow_30d += amount
    
    return {
        "median_settlement_lag_days": median_lag_days,
        "pending_settlements": sorted(pending, key=lambda x: x["days_until_settlement"]),
        "expected_inflow_next_7_days": round(inflow_7d, 2),
        "expected_inflow_next_30_days": round(inflow_30d, 2),
        "forecast_date": str(as_of_date),
    }
