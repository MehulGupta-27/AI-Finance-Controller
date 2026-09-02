"""
agents/data_loader.py
Loads and lightly validates the three raw CSV files.

Responsibilities:
- Read internal_ledger.csv, razorpay_export.csv, bank_statement.csv
- Enforce column presence (fail fast if a column is missing)
- Parse date columns to datetime.date (not datetime — keeps comparisons clean)
- Ensure refund_amount and rzp_fee are numeric (float64)
- Return plain DataFrames — no Pydantic here, that's Agent 1's job
- Supports both the 110-record dev set (data/raw_100/) and the full set (data/raw/)

Usage:
    from agents.data_loader import load_raw_data
    ledger_df, rzp_df, bank_df = load_raw_data()          # defaults to raw_100
    ledger_df, rzp_df, bank_df = load_raw_data("data/raw") # full set
"""

import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]

# Expected columns per file — fail immediately if any are absent
LEDGER_COLUMNS = {
    "ledger_id", "order_id", "customer_name", "amount", "currency",
    "order_date", "payment_method", "status", "refund_amount", "notes",
}
RZP_COLUMNS = {
    "rzp_payment_id", "order_id", "amount", "currency",
    "rzp_fee", "captured_at", "method", "status",
}
BANK_COLUMNS = {
    "utr_number", "settlement_amount", "settlement_date",
    "narration", "bank_ref_type",
}


def _check_columns(df: pd.DataFrame, expected: set, filename: str) -> None:
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(
            f"[data_loader] {filename} is missing required columns: {sorted(missing)}"
        )


def _parse_date_col(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Parse a date column to datetime.date objects (not full datetime)."""
    df = df.copy()
    df[col] = pd.to_datetime(df[col], errors="raise").dt.date
    return df


def load_raw_data(data_dir: str = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Load the three source CSVs from *data_dir*.

    Parameters
    ----------
    data_dir : str or None
        Path to the folder containing the three CSVs.
        Defaults to <project_root>/data/raw_100 (the 110-record dev set).

    Returns
    -------
    (ledger_df, rzp_df, bank_df) — all DataFrames with:
        - date columns as datetime.date
        - refund_amount and rzp_fee as float64
        - notes NaN filled with ""
    """
    if data_dir is None:
        data_path = ROOT / "data" / "raw_100"
    else:
        data_path = Path(data_dir)
        if not data_path.is_absolute():
            data_path = ROOT / data_path

    if not data_path.exists():
        raise FileNotFoundError(f"[data_loader] Data directory not found: {data_path}")

    # ------------------------------------------------------------------ ledger
    ledger_path = data_path / "internal_ledger.csv"
    if not ledger_path.exists():
        raise FileNotFoundError(f"[data_loader] Missing file: {ledger_path}")
    ledger_df = pd.read_csv(ledger_path)
    _check_columns(ledger_df, LEDGER_COLUMNS, "internal_ledger.csv")
    ledger_df = _parse_date_col(ledger_df, "order_date")
    ledger_df["refund_amount"] = pd.to_numeric(ledger_df["refund_amount"], errors="raise").astype(float)
    ledger_df["amount"]        = pd.to_numeric(ledger_df["amount"],        errors="raise").astype(float)
    ledger_df["notes"]         = ledger_df["notes"].fillna("").astype(str)
    logger.debug("Loaded ledger: %d rows", len(ledger_df))

    # ---------------------------------------------------------------- razorpay
    rzp_path = data_path / "razorpay_export.csv"
    if not rzp_path.exists():
        raise FileNotFoundError(f"[data_loader] Missing file: {rzp_path}")
    rzp_df = pd.read_csv(rzp_path)
    _check_columns(rzp_df, RZP_COLUMNS, "razorpay_export.csv")
    rzp_df = _parse_date_col(rzp_df, "captured_at")
    rzp_df["rzp_fee"] = pd.to_numeric(rzp_df["rzp_fee"], errors="raise").astype(float)
    rzp_df["amount"]  = pd.to_numeric(rzp_df["amount"],  errors="raise").astype(float)
    logger.debug("Loaded razorpay: %d rows", len(rzp_df))

    # -------------------------------------------------------------------- bank
    bank_path = data_path / "bank_statement.csv"
    if not bank_path.exists():
        raise FileNotFoundError(f"[data_loader] Missing file: {bank_path}")
    bank_df = pd.read_csv(bank_path)
    _check_columns(bank_df, BANK_COLUMNS, "bank_statement.csv")
    bank_df = _parse_date_col(bank_df, "settlement_date")
    bank_df["settlement_amount"] = pd.to_numeric(bank_df["settlement_amount"], errors="raise").astype(float)
    bank_df["narration"]         = bank_df["narration"].fillna("").astype(str)
    logger.debug("Loaded bank: %d rows", len(bank_df))

    return ledger_df, rzp_df, bank_df


# ---------------------------------------------------------------------------
# Quick smoke-test when run directly
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    data_dir = sys.argv[1] if len(sys.argv) > 1 else None
    ledger_df, rzp_df, bank_df = load_raw_data(data_dir)

    print("\n=== data_loader smoke test ===")
    print(f"  Ledger rows   : {len(ledger_df)}")
    print(f"  Razorpay rows : {len(rzp_df)}")
    print(f"  Bank rows     : {len(bank_df)}")
    print()
    print("  Ledger dtypes:")
    for col, dtype in ledger_df.dtypes.items():
        print(f"    {col:<20} {dtype}")
    print()
    print("  Razorpay dtypes:")
    for col, dtype in rzp_df.dtypes.items():
        print(f"    {col:<20} {dtype}")
    print()
    print("  Bank dtypes:")
    for col, dtype in bank_df.dtypes.items():
        print(f"    {col:<20} {dtype}")
    print()
    print("  Sample ledger row:")
    print("   ", ledger_df.iloc[0].to_dict())
    print()
    print("  Sample razorpay row:")
    print("   ", rzp_df.iloc[0].to_dict())
    print()
    print("  Sample bank row:")
    print("   ", bank_df.iloc[0].to_dict())
    print()
    # Verify refund_amount is float, not string
    assert ledger_df["refund_amount"].dtype == float, "refund_amount must be float"
    assert rzp_df["rzp_fee"].dtype == float, "rzp_fee must be float"
    print("  ✓ refund_amount is float64")
    print("  ✓ rzp_fee is float64")
    print("  ✓ All date columns parsed to datetime.date")
    print("=== OK ===\n")
