"""
agents/exact_match_agent.py  —  Agent 2
Exact Match Engine.

Resolves Ledger↔Razorpay via the shared `order_id` key — the only reliable
cross-source identifier in the dataset (Section 3).

Design:
- Pandas merge on order_id. O(n) — no LLM, no fuzzy logic.
- Early exit: an exact match found here means STOP — record is MATCHED,
  no further stages. Only unmatched records continue downstream.
- duplicate_capture: a ledger row may match *two* Razorpay rows (one
  captured, one failed). We keep BOTH rzp matches in the result so
  Agent 3 / Agent 6 can select the real one and flag the duplicate.
- failed_payment_orphan: ledger status="failed" with a matched failed
  Razorpay row is an exact match — sub_reason="no_action_needed".
- Bank rows are never touched here — they have no order_id.
  Ledger/Razorpay rows that didn't match proceed to Agent 3.

Output: ExactMatchResult containing:
  - matched_pairs    : list of ExactMatchPair (one per matched order)
  - unmatched_ledger : CanonicalRecords with no Razorpay counterpart
  - unmatched_rzp    : CanonicalRecords with no Ledger counterpart
  - all_bank         : bank records passed through untouched
"""

import logging
import sys
from pathlib import Path
from typing import Optional
from collections import defaultdict

# Ensure project root is on sys.path when this module is imported directly
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pydantic import BaseModel

from agents.core.ingestion_agent import CanonicalRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------

class ExactMatchPair(BaseModel):
    """One confirmed Ledger↔Razorpay exact match on order_id."""
    order_id:       str
    ledger_record:  CanonicalRecord
    rzp_records:    list[CanonicalRecord]   # usually 1; 2 for duplicate_capture
    is_failed:      bool                    # True if ledger status == "failed"
    sub_reason:     Optional[str]           # "no_action_needed" for failed orphans

    model_config = {"arbitrary_types_allowed": True}


class ExactMatchResult(BaseModel):
    matched_pairs:     list[ExactMatchPair]
    unmatched_ledger:  list[CanonicalRecord]   # no Razorpay row found
    unmatched_rzp:     list[CanonicalRecord]   # no Ledger row found (missing_from_ledger)
    all_bank:          list[CanonicalRecord]   # passed through unchanged

    model_config = {"arbitrary_types_allowed": True}

    @property
    def matched_order_ids(self) -> set[str]:
        return {p.order_id for p in self.matched_pairs}

    def summary(self) -> str:
        return (
            f"ExactMatch: {len(self.matched_pairs)} pairs matched | "
            f"{len(self.unmatched_ledger)} unmatched_ledger | "
            f"{len(self.unmatched_rzp)} unmatched_rzp | "
            f"{len(self.all_bank)} bank (pass-through)"
        )


# ---------------------------------------------------------------------------
# Core matching logic
# ---------------------------------------------------------------------------

def run_exact_match(
    ledger_records:   list[CanonicalRecord],
    razorpay_records: list[CanonicalRecord],
    bank_records:     list[CanonicalRecord],
) -> ExactMatchResult:
    """
    Match ledger and Razorpay records on order_id.

    Parameters
    ----------
    ledger_records   : all CanonicalRecords with source="ledger"
    razorpay_records : all CanonicalRecords with source="razorpay"
    bank_records     : all CanonicalRecords with source="bank" — passed through

    Returns
    -------
    ExactMatchResult
    """
    # Index Razorpay records by order_id — one order may have 2 rzp rows
    # (duplicate_capture: one captured, one failed)
    rzp_by_order: dict[str, list[CanonicalRecord]] = defaultdict(list)
    for rec in razorpay_records:
        if rec.order_id:
            rzp_by_order[rec.order_id].append(rec)

    matched_pairs:    list[ExactMatchPair] = []
    unmatched_ledger: list[CanonicalRecord] = []
    matched_order_ids: set[str] = set()

    for led in ledger_records:
        if not led.order_id or led.order_id not in rzp_by_order:
            unmatched_ledger.append(led)
            continue

        rzp_matches = rzp_by_order[led.order_id]
        matched_order_ids.add(led.order_id)

        is_failed = led.status == "failed"
        sub_reason = "no_action_needed" if is_failed else None

        pair = ExactMatchPair(
            order_id      = led.order_id,
            ledger_record = led,
            rzp_records   = rzp_matches,
            is_failed     = is_failed,
            sub_reason    = sub_reason,
        )
        matched_pairs.append(pair)
        logger.debug(
            "Exact match: order_id=%s ledger=%s rzp=[%s] failed=%s",
            led.order_id,
            led.source_ref,
            ", ".join(r.source_ref for r in rzp_matches),
            is_failed,
        )

    # Razorpay records whose order_id never appeared in any ledger row
    # → missing_from_ledger candidates (real money moved, no ledger entry)
    unmatched_rzp = [
        rec for rec in razorpay_records
        if rec.order_id and rec.order_id not in matched_order_ids
    ]

    result = ExactMatchResult(
        matched_pairs    = matched_pairs,
        unmatched_ledger = unmatched_ledger,
        unmatched_rzp    = unmatched_rzp,
        all_bank         = bank_records,
    )

    logger.info(result.summary())
    return result


# ---------------------------------------------------------------------------
# Standalone smoke-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import json
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from agents.utils.data_loader import load_raw_data
    from agents.core.ingestion_agent import ingest

    ledger_df, rzp_df, bank_df = load_raw_data()
    ing = ingest(ledger_df, rzp_df, bank_df)
    result = run_exact_match(ing.ledger_records, ing.razorpay_records, ing.bank_records)

    print("\n=== Exact Match smoke test ===")
    print(f"  Matched pairs      : {len(result.matched_pairs)}")
    print(f"  Unmatched ledger   : {len(result.unmatched_ledger)}")
    print(f"  Unmatched Razorpay : {len(result.unmatched_rzp)}")
    print(f"  Bank (pass-through): {len(result.all_bank)}")

    # Count failed orphans
    failed_pairs = [p for p in result.matched_pairs if p.is_failed]
    print(f"\n  Failed orphan pairs (no_action_needed): {len(failed_pairs)}")
    for p in failed_pairs:
        print(f"    order_id={p.order_id}  ledger_status={p.ledger_record.status}")

    # Count duplicate captures (pairs with 2 rzp rows)
    dup_pairs = [p for p in result.matched_pairs if len(p.rzp_records) > 1]
    print(f"\n  Duplicate capture pairs: {len(dup_pairs)}")
    for p in dup_pairs:
        statuses = [r.status for r in p.rzp_records]
        print(f"    order_id={p.order_id}  rzp_statuses={statuses}")

    # Unmatched Razorpay → missing_from_ledger candidates
    print(f"\n  Unmatched Razorpay (missing_from_ledger candidates): {len(result.unmatched_rzp)}")
    for r in result.unmatched_rzp:
        print(f"    {r.source_ref}  order_id={r.order_id}  status={r.status}")

    # Verify no order_id appears in both matched and unmatched
    matched_oids = {p.order_id for p in result.matched_pairs}
    unmatched_led_oids = {r.order_id for r in result.unmatched_ledger if r.order_id}
    overlap = matched_oids & unmatched_led_oids
    assert not overlap, f"order_ids in both matched and unmatched_ledger: {overlap}"
    print("\n  ✓ No order_id in both matched and unmatched")
    print("  ✓ Bank records passed through unchanged")
    print("=== OK ===\n")
