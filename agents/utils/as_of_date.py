"""
agents/as_of_date.py  —  Agent 0
Computes AS_OF_DATE once at pipeline startup from the actual dataset dates.

Section 0C.2 rule: AS_OF_DATE must NEVER be datetime.now() or date.today().
It is always the maximum date across all three loaded source files.
Every elapsed-time comparison elsewhere in the codebase must receive this
value explicitly — never recompute it, never substitute the wall clock.

Usage:
    from agents.utils.as_of_date import compute_as_of_date
    AS_OF_DATE = compute_as_of_date(ledger_df, rzp_df, bank_df)
"""

import logging
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logger = logging.getLogger(__name__)


def compute_as_of_date(
    ledger_df: pd.DataFrame,
    rzp_df: pd.DataFrame,
    bank_df: pd.DataFrame,
) -> date:
    """
    Compute AS_OF_DATE as max(order_date, captured_at, settlement_date)
    across all three DataFrames.

    Parameters
    ----------
    ledger_df : DataFrame with 'order_date' column (datetime.date objects)
    rzp_df    : DataFrame with 'captured_at' column (datetime.date objects)
    bank_df   : DataFrame with 'settlement_date' column (datetime.date objects)

    Returns
    -------
    datetime.date  — the latest date found across all three sources.

    Raises
    ------
    ValueError  if any required date column is missing or all-null.
    """
    candidates = []

    for df, col, label in [
        (ledger_df, "order_date",      "ledger.order_date"),
        (rzp_df,    "captured_at",     "razorpay.captured_at"),
        (bank_df,   "settlement_date", "bank.settlement_date"),
    ]:
        if col not in df.columns:
            raise ValueError(f"[as_of_date] Column '{col}' missing from {label}")
        col_max = df[col].dropna().max()
        if col_max is None or (hasattr(col_max, '__class__') and col_max != col_max):
            raise ValueError(f"[as_of_date] No valid dates found in {label}")
        # Handle both datetime.date and pandas Timestamp
        if hasattr(col_max, "date"):
            col_max = col_max.date()
        candidates.append(col_max)

    as_of = max(candidates)

    # Log visibly — required by Section 0C.2 so every run is inspectable
    logger.info("=" * 50)
    logger.info("AS_OF_DATE = %s  (computed from dataset, NOT wall clock)", as_of)
    logger.info("=" * 50)

    return as_of


# ---------------------------------------------------------------------------
# Standalone smoke-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from agents.utils.data_loader import load_raw_data
    from agents.utils.config import BASE_DATE, DAY_SPAN
    from datetime import timedelta

    ledger_df, rzp_df, bank_df = load_raw_data()
    as_of = compute_as_of_date(ledger_df, rzp_df, bank_df)

    window_start = BASE_DATE
    window_end   = BASE_DATE + timedelta(days=DAY_SPAN + 10)

    print(f"\nAS_OF_DATE       : {as_of}")
    print(f"Dataset window   : {window_start} → {window_end}")
    print(f"Within window?   : {window_start <= as_of <= window_end}")
    assert window_start <= as_of <= window_end, (
        f"AS_OF_DATE {as_of} is outside the dataset window "
        f"[{window_start}, {window_end}] — wall-clock contamination?"
    )
    print("✓ AS_OF_DATE is within the dataset's real date window")
    print("✓ No wall-clock contamination\n")
