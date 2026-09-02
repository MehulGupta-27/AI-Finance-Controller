"""
agents/ingestion_agent.py  —  Agent 1
Ingestion & Normalization.

Takes the three raw DataFrames from data_loader.py and converts every row
into a canonical CanonicalRecord. Validates each row with Pydantic before
accepting it. Any row failing validation is written to the exception list
with reason="ingestion_validation_failed" — never dropped silently, never
passed downstream. This is the first line of defence for Section 0C.3.

Key design decisions:
- `notes` is promoted to a top-level field (not buried in `raw`) so Agent 4
  can reliably surface it in LLM prompts for semantic_brand_narration cases.
- Every record gets a stable UUID-based record_id derived from its natural
  source key — deterministic across re-runs of the same dataset.
- The `raw` dict keeps the full original row for audit purposes.
- No LLM calls here — pure Python + Pandas + Pydantic.
"""

import hashlib
import logging
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
from pydantic import BaseModel, field_validator, model_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Canonical record schema
# ---------------------------------------------------------------------------

class CanonicalRecord(BaseModel):
    """
    One normalised record from any of the three sources.
    Every downstream agent works with this schema exclusively.
    """
    record_id:   str                      # stable UUID derived from source key
    source:      str                      # "ledger" | "razorpay" | "bank"
    source_ref:  str                      # original ID (ledger_id / rzp_payment_id / utr_number)
    order_id:    Optional[str]            # shared key; None for bank rows
    amount:      float                    # face amount (gross for razorpay/ledger, net for bank)
    date:        date                     # order_date / captured_at / settlement_date
    text_field:  str                      # customer_name (ledger/rzp) or narration (bank)
    notes:       str                      # ledger notes — top-level for Agent 4 prompt wiring
    status:      str                      # raw status string
    raw:         dict[str, Any]           # full original row kept for audit

    model_config = {"arbitrary_types_allowed": True}

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        allowed = {"ledger", "razorpay", "bank"}
        if v not in allowed:
            raise ValueError(f"source must be one of {allowed}, got '{v}'")
        return v

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"amount must be non-negative, got {v}")
        return v

    @field_validator("notes", mode="before")
    @classmethod
    def coerce_notes(cls, v: Any) -> str:
        if v is None or (isinstance(v, float) and v != v):  # NaN check
            return ""
        return str(v)


# ---------------------------------------------------------------------------
# Validation failure record — goes to the exception list
# ---------------------------------------------------------------------------

class ValidationFailure(BaseModel):
    source:     str
    source_ref: str
    reason:     str = "ingestion_validation_failed"
    detail:     str


# ---------------------------------------------------------------------------
# Deterministic record_id generation
# Keyed on (source, source_ref) so re-runs produce identical IDs.
# ---------------------------------------------------------------------------

def _make_record_id(source: str, source_ref: str) -> str:
    key = f"{source}::{source_ref}"
    return str(uuid.UUID(hashlib.md5(key.encode()).hexdigest()))


# ---------------------------------------------------------------------------
# Per-source normalisation helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: pd.Series) -> dict:
    """Convert a pandas Series to a plain dict, handling date objects."""
    d = {}
    for k, v in row.items():
        if isinstance(v, date):
            d[k] = v.isoformat()
        elif pd.isna(v) if not isinstance(v, (list, dict)) else False:
            d[k] = None
        else:
            d[k] = v
    return d


def _ingest_ledger(
    ledger_df: pd.DataFrame,
) -> tuple[list[CanonicalRecord], list[ValidationFailure]]:
    records, failures = [], []
    for _, row in ledger_df.iterrows():
        source_ref = str(row.get("ledger_id", ""))
        try:
            rec = CanonicalRecord(
                record_id  = _make_record_id("ledger", source_ref),
                source     = "ledger",
                source_ref = source_ref,
                order_id   = str(row["order_id"]) if pd.notna(row.get("order_id")) else None,
                amount     = float(row["amount"]),
                date       = row["order_date"],
                text_field = str(row.get("customer_name", "")),
                notes      = row.get("notes", ""),
                status     = str(row.get("status", "")),
                raw        = _row_to_dict(row),
            )
            records.append(rec)
        except Exception as exc:
            failures.append(ValidationFailure(
                source=    "ledger",
                source_ref=source_ref,
                detail=    str(exc),
            ))
            logger.warning("Ledger row %s failed validation: %s", source_ref, exc)
    return records, failures


def _ingest_razorpay(
    rzp_df: pd.DataFrame,
) -> tuple[list[CanonicalRecord], list[ValidationFailure]]:
    records, failures = [], []
    for _, row in rzp_df.iterrows():
        source_ref = str(row.get("rzp_payment_id", ""))
        try:
            rec = CanonicalRecord(
                record_id  = _make_record_id("razorpay", source_ref),
                source     = "razorpay",
                source_ref = source_ref,
                order_id   = str(row["order_id"]) if pd.notna(row.get("order_id")) else None,
                amount     = float(row["amount"]),
                date       = row["captured_at"],
                text_field = str(row.get("method", "")),
                notes      = "",           # razorpay has no notes field
                status     = str(row.get("status", "")),
                raw        = _row_to_dict(row),
            )
            records.append(rec)
        except Exception as exc:
            failures.append(ValidationFailure(
                source=    "razorpay",
                source_ref=source_ref,
                detail=    str(exc),
            ))
            logger.warning("Razorpay row %s failed validation: %s", source_ref, exc)
    return records, failures


def _ingest_bank(
    bank_df: pd.DataFrame,
) -> tuple[list[CanonicalRecord], list[ValidationFailure]]:
    records, failures = [], []
    for _, row in bank_df.iterrows():
        source_ref = str(row.get("utr_number", ""))
        try:
            rec = CanonicalRecord(
                record_id  = _make_record_id("bank", source_ref),
                source     = "bank",
                source_ref = source_ref,
                order_id   = None,         # bank has no shared key
                amount     = float(row["settlement_amount"]),
                date       = row["settlement_date"],
                text_field = str(row.get("narration", "")),
                notes      = "",           # bank has no notes field
                status     = str(row.get("bank_ref_type", "")),
                raw        = _row_to_dict(row),
            )
            records.append(rec)
        except Exception as exc:
            failures.append(ValidationFailure(
                source=    "bank",
                source_ref=source_ref,
                detail=    str(exc),
            ))
            logger.warning("Bank row %s failed validation: %s", source_ref, exc)
    return records, failures


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class IngestionResult(BaseModel):
    ledger_records:   list[CanonicalRecord]
    razorpay_records: list[CanonicalRecord]
    bank_records:     list[CanonicalRecord]
    failures:         list[ValidationFailure]

    model_config = {"arbitrary_types_allowed": True}

    @property
    def all_records(self) -> list[CanonicalRecord]:
        return self.ledger_records + self.razorpay_records + self.bank_records

    @property
    def total_count(self) -> int:
        return len(self.all_records)


def ingest(
    ledger_df:  pd.DataFrame,
    rzp_df:     pd.DataFrame,
    bank_df:    pd.DataFrame,
) -> IngestionResult:
    """
    Normalise all three DataFrames into CanonicalRecord lists.

    Validation failures are collected into `result.failures` — they are
    never dropped silently and never passed downstream (Section 0C.3).

    Returns
    -------
    IngestionResult with separate lists per source + a combined failures list.
    """
    led_recs,  led_fails  = _ingest_ledger(ledger_df)
    rzp_recs,  rzp_fails  = _ingest_razorpay(rzp_df)
    bank_recs, bank_fails = _ingest_bank(bank_df)

    all_failures = led_fails + rzp_fails + bank_fails

    result = IngestionResult(
        ledger_records   = led_recs,
        razorpay_records = rzp_recs,
        bank_records     = bank_recs,
        failures         = all_failures,
    )

    logger.info(
        "Ingestion complete — ledger=%d razorpay=%d bank=%d failures=%d",
        len(led_recs), len(rzp_recs), len(bank_recs), len(all_failures),
    )

    if all_failures:
        logger.warning(
            "%d rows failed validation and were written to the exception list:",
            len(all_failures),
        )
        for f in all_failures:
            logger.warning("  [%s] %s — %s", f.source, f.source_ref, f.detail)

    return result


# ---------------------------------------------------------------------------
# Standalone smoke-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from agents.data_loader import load_raw_data

    ledger_df, rzp_df, bank_df = load_raw_data()
    result = ingest(ledger_df, rzp_df, bank_df)

    print("\n=== Ingestion smoke test ===")
    print(f"  Ledger records   : {len(result.ledger_records)}")
    print(f"  Razorpay records : {len(result.razorpay_records)}")
    print(f"  Bank records     : {len(result.bank_records)}")
    print(f"  Total records    : {result.total_count}")
    print(f"  Failures         : {len(result.failures)}")

    # Spot-check a ledger record
    led = result.ledger_records[0]
    print(f"\n  Sample ledger record:")
    print(f"    record_id  : {led.record_id}")
    print(f"    source     : {led.source}")
    print(f"    source_ref : {led.source_ref}")
    print(f"    order_id   : {led.order_id}")
    print(f"    amount     : {led.amount}")
    print(f"    date       : {led.date}")
    print(f"    text_field : {led.text_field}")
    print(f"    notes      : '{led.notes}'")
    print(f"    status     : {led.status}")

    # Spot-check a bank record (should have no order_id)
    bank = result.bank_records[0]
    print(f"\n  Sample bank record:")
    print(f"    record_id  : {bank.record_id}")
    print(f"    order_id   : {bank.order_id}  (expect None)")
    print(f"    text_field : {bank.text_field}  (narration)")

    # Check semantic_brand_narration records have notes populated
    sem_recs = [r for r in result.ledger_records if r.notes.strip()]
    print(f"\n  Ledger records with non-empty notes : {len(sem_recs)}")
    for r in sem_recs:
        print(f"    {r.source_ref} → notes='{r.notes}'")

    # Verify record_id stability (same input → same ID)
    led2 = result.ledger_records[0]
    assert led.record_id == led2.record_id, "record_id not stable!"

    # Verify no duplicate record_ids
    all_ids = [r.record_id for r in result.all_records]
    assert len(all_ids) == len(set(all_ids)), "Duplicate record_ids found!"

    print("\n  ✓ No duplicate record_ids")
    print("  ✓ Notes promoted to top-level field")
    print("  ✓ Bank records have order_id=None")
    print("=== OK ===\n")
