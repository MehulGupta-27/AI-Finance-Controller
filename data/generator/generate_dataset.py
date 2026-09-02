"""
data/generator/generate_dataset.py
Generates the 110-record development dataset (data/raw_100/) with all 11 case types
at the exact counts specified in Section 3 of the build spec.

Run:
    python data/generator/generate_dataset.py --dataset 110
    python data/generator/generate_dataset.py --dataset 550  (Section 11 step 17 only)

Key rules enforced here:
- refund_amount is a real numeric column (0.0 except partial_refund_split)
- rzp_fee is computed once and stored — never re-derived later
- settlement_amount = amount - rzp_fee  (or - refund_amount for partial refunds)
- semantic_brand_narration uses MERCHANT_PROFILE registered legal name variants
- unidentified_bank_credit rows have NO ledger/Razorpay counterpart
- ground_truth.json uses three-state statuses, never booleans
- Seed 42 for full reproducibility
"""

import sys
import os
import json
import uuid
import random
import argparse
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

# Make sure we can import from agents/
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agents.config import (
    BASE_DATE, DAY_SPAN, MERCHANT_PROFILE,
    SETTLEMENT_DATE_TOLERANCE_DAYS,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RNG = random.Random(42)

FEE_RATE = 0.02          # Razorpay 2%
GST_RATE = 0.18          # 18% GST on the fee
# effective fee = amount * FEE_RATE * (1 + GST_RATE) = amount * 0.0236

PAYMENT_METHODS = ["upi", "card", "netbanking", "wallet"]
CURRENCIES = ["INR"]

# Indian first names for synthetic customers
FIRST_NAMES = [
    "Priya", "Rohan", "Meera", "Arjun", "Sneha", "Vikram", "Ananya",
    "Rahul", "Pooja", "Kiran", "Siddharth", "Divya", "Amit", "Kavya",
    "Nikhil", "Riya", "Suresh", "Neha", "Raj", "Shreya", "Aditya",
    "Tanya", "Manish", "Deepa", "Vivek", "Swati", "Harsh", "Nandini",
    "Gaurav", "Isha", "Tushar", "Pallavi", "Sachin", "Madhuri",
]
LAST_NAMES = [
    "Sharma", "Patel", "Singh", "Kumar", "Gupta", "Mehta", "Joshi",
    "Reddy", "Nair", "Iyer", "Desai", "Shah", "Verma", "Rao", "Mishra",
]

# Bank narration noise templates for non-semantic records
NARRATION_TEMPLATES = [
    "RAZORPAY*{suffix}",
    "UPI/{order_frag}",
    "NEFT/{name_frag}",
    "{name_frag}/RZRPY",
    "PG SETL {suffix}",
    "IMPS {suffix}",
    "{name_frag} UPI",
]

# Narrations for unidentified_bank_credit — must NOT reference any customer or order
UNIDENTIFIED_NARRATIONS = [
    "INT CREDIT QTR",
    "BANK CHG REVERSAL",
    "NEFT MISC CREDIT",
    "INTEREST CREDIT",
    "SWEEP ACCOUNT CREDIT",
    "BANK REVERSAL FEES",
    "MISC CR ADJ",
]

# semantic_brand_narration: registered legal name variants (Section 3)
SEMANTIC_NARRATIONS = [
    "FITZONE WELLNESS PVT LTD",
    "FZW PRIVATE LIMITED RZRPY",
    "FITZONE WELLNESS P LTD SETL",
]

# semantic_brand_narration: ledger notes (plain purchase descriptions)
SEMANTIC_NOTES = [
    "Monthly gym membership renewal",
    "Personal training package - 10 sessions",
    "Annual premium membership upgrade",
]

# hard_garbled_narration: truly opaque bank strings
GARBLED_NARRATIONS = [
    "UPI-{n}",
    "NEFT{n}",
    "CR-{n}",
    "SETL{n}",
    "PGW{n}",
    "IMPS{n}",
    "UTR{n}",
    "TXN{n}",
    "REF{n}",
    "PMT{n}",
]

BANK_REF_TYPES = ["NEFT", "IMPS", "UPI", "RTGS"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def rand_date(base: date = BASE_DATE, span: int = DAY_SPAN) -> date:
    return base + timedelta(days=RNG.randint(0, span - 1))

def rand_amount(lo: float = 200.0, hi: float = 8000.0) -> float:
    return round(RNG.uniform(lo, hi), 2)

def rand_name() -> str:
    return f"{RNG.choice(FIRST_NAMES)} {RNG.choice(LAST_NAMES)}"

def rzp_fee(amount: float) -> float:
    """Compute Razorpay fee: 2% + 18% GST on that 2%."""
    return round(amount * FEE_RATE * (1 + GST_RATE), 2)

def new_order_id() -> str:
    return f"ORD{RNG.randint(100000, 999999)}"

def new_rzp_id() -> str:
    return f"pay_{uuid.uuid4().hex[:14]}"

def new_utr() -> str:
    return f"UTR{RNG.randint(10**11, 10**12 - 1)}"

def new_ledger_id() -> str:
    return f"LED{RNG.randint(10000, 99999)}"

def garbled_narration() -> str:
    tpl = RNG.choice(GARBLED_NARRATIONS)
    return tpl.format(n=RNG.randint(1000, 9999))

def normal_narration(customer_name: str, order_id: str) -> str:
    """Non-semantic, non-garbled narration — partial name/order fragment."""
    tpl = RNG.choice(NARRATION_TEMPLATES)
    name_parts = customer_name.split()
    name_frag = name_parts[0][:4].upper() if name_parts else "CUST"
    order_frag = order_id[-6:]
    suffix = f"{RNG.randint(100, 999)}"
    return tpl.format(suffix=suffix, name_frag=name_frag, order_frag=order_frag)


# ---------------------------------------------------------------------------
# Row builders — each returns (ledger_row, rzp_rows, bank_rows, gt_entry)
# gt_entry: {case_id, case_type, expected_status, expected_sub_reason,
#             ledger_ids, rzp_ids, utr_numbers}
# ---------------------------------------------------------------------------

def build_clean(case_idx: int):
    """clean_triple_match: normal 1–3 day settlement lag."""
    order_id = new_order_id()
    customer = rand_name()
    amount = rand_amount()
    fee = rzp_fee(amount)
    settlement = round(amount - fee, 2)
    order_date = rand_date()
    captured_at = order_date
    lag = RNG.randint(1, 3)
    settlement_date = captured_at + timedelta(days=lag)
    method = RNG.choice(PAYMENT_METHODS)
    ledger_id = new_ledger_id()
    rzp_id = new_rzp_id()
    utr = new_utr()

    ledger = dict(
        ledger_id=ledger_id, order_id=order_id, customer_name=customer,
        amount=amount, currency="INR", order_date=order_date.isoformat(),
        payment_method=method, status="paid", refund_amount=0.0,
        notes="",
    )
    rzp = dict(
        rzp_payment_id=rzp_id, order_id=order_id, amount=amount,
        currency="INR", rzp_fee=fee, captured_at=captured_at.isoformat(),
        method=method, status="captured",
    )
    bank = dict(
        utr_number=utr, settlement_amount=settlement,
        settlement_date=settlement_date.isoformat(),
        narration=normal_narration(customer, order_id),
        bank_ref_type=RNG.choice(BANK_REF_TYPES),
    )
    gt = dict(
        case_id=f"case_{case_idx:04d}", case_type="clean_triple_match",
        expected_status="MATCHED", expected_sub_reason=None,
        ledger_ids=[ledger_id], rzp_ids=[rzp_id], utr_numbers=[utr],
    )
    return ledger, [rzp], [bank], gt


def build_delayed(case_idx: int):
    """delayed_settlement: 5–9 day settlement lag."""
    order_id = new_order_id()
    customer = rand_name()
    amount = rand_amount()
    fee = rzp_fee(amount)
    settlement = round(amount - fee, 2)
    order_date = rand_date(span=DAY_SPAN - 9)  # leave room for lag
    captured_at = order_date
    lag = RNG.randint(5, 9)
    settlement_date = captured_at + timedelta(days=lag)
    method = RNG.choice(PAYMENT_METHODS)
    ledger_id = new_ledger_id()
    rzp_id = new_rzp_id()
    utr = new_utr()

    ledger = dict(
        ledger_id=ledger_id, order_id=order_id, customer_name=customer,
        amount=amount, currency="INR", order_date=order_date.isoformat(),
        payment_method=method, status="paid", refund_amount=0.0,
        notes="",
    )
    rzp = dict(
        rzp_payment_id=rzp_id, order_id=order_id, amount=amount,
        currency="INR", rzp_fee=fee, captured_at=captured_at.isoformat(),
        method=method, status="captured",
    )
    bank = dict(
        utr_number=utr, settlement_amount=settlement,
        settlement_date=settlement_date.isoformat(),
        narration=normal_narration(customer, order_id),
        bank_ref_type=RNG.choice(BANK_REF_TYPES),
    )
    gt = dict(
        case_id=f"case_{case_idx:04d}", case_type="delayed_settlement",
        expected_status="MATCHED", expected_sub_reason=None,
        ledger_ids=[ledger_id], rzp_ids=[rzp_id], utr_numbers=[utr],
    )
    return ledger, [rzp], [bank], gt


def build_hard_garbled(case_idx: int):
    """hard_garbled_narration: garbled bank narration, must match on amount/date."""
    order_id = new_order_id()
    customer = rand_name()
    amount = rand_amount()
    fee = rzp_fee(amount)
    settlement = round(amount - fee, 2)
    order_date = rand_date()
    captured_at = order_date
    lag = RNG.randint(1, 3)
    settlement_date = captured_at + timedelta(days=lag)
    method = RNG.choice(PAYMENT_METHODS)
    ledger_id = new_ledger_id()
    rzp_id = new_rzp_id()
    utr = new_utr()

    ledger = dict(
        ledger_id=ledger_id, order_id=order_id, customer_name=customer,
        amount=amount, currency="INR", order_date=order_date.isoformat(),
        payment_method=method, status="paid", refund_amount=0.0,
        notes="",
    )
    rzp = dict(
        rzp_payment_id=rzp_id, order_id=order_id, amount=amount,
        currency="INR", rzp_fee=fee, captured_at=captured_at.isoformat(),
        method=method, status="captured",
    )
    bank = dict(
        utr_number=utr, settlement_amount=settlement,
        settlement_date=settlement_date.isoformat(),
        narration=garbled_narration(),
        bank_ref_type=RNG.choice(BANK_REF_TYPES),
    )
    gt = dict(
        case_id=f"case_{case_idx:04d}", case_type="hard_garbled_narration",
        expected_status="MATCHED", expected_sub_reason=None,
        ledger_ids=[ledger_id], rzp_ids=[rzp_id], utr_numbers=[utr],
    )
    return ledger, [rzp], [bank], gt


def build_duplicate_capture(case_idx: int):
    """duplicate_capture: two Razorpay rows for same order (one failed, one captured)."""
    order_id = new_order_id()
    customer = rand_name()
    amount = rand_amount()
    fee = rzp_fee(amount)
    settlement = round(amount - fee, 2)
    order_date = rand_date()
    captured_at = order_date
    lag = RNG.randint(1, 3)
    settlement_date = captured_at + timedelta(days=lag)
    method = RNG.choice(PAYMENT_METHODS)
    ledger_id = new_ledger_id()
    rzp_id_real = new_rzp_id()
    rzp_id_dup = new_rzp_id()
    utr = new_utr()

    ledger = dict(
        ledger_id=ledger_id, order_id=order_id, customer_name=customer,
        amount=amount, currency="INR", order_date=order_date.isoformat(),
        payment_method=method, status="paid", refund_amount=0.0,
        notes="",
    )
    # Real capture
    rzp_real = dict(
        rzp_payment_id=rzp_id_real, order_id=order_id, amount=amount,
        currency="INR", rzp_fee=fee, captured_at=captured_at.isoformat(),
        method=method, status="captured",
    )
    # Failed duplicate attempt — same order_id, different payment_id, 0 fee
    rzp_dup = dict(
        rzp_payment_id=rzp_id_dup, order_id=order_id, amount=amount,
        currency="INR", rzp_fee=0.0, captured_at=captured_at.isoformat(),
        method=method, status="failed",
    )
    bank = dict(
        utr_number=utr, settlement_amount=settlement,
        settlement_date=settlement_date.isoformat(),
        narration=normal_narration(customer, order_id),
        bank_ref_type=RNG.choice(BANK_REF_TYPES),
    )
    gt = dict(
        case_id=f"case_{case_idx:04d}", case_type="duplicate_capture",
        expected_status="MATCHED", expected_sub_reason=None,
        ledger_ids=[ledger_id], rzp_ids=[rzp_id_real, rzp_id_dup],
        utr_numbers=[utr],
    )
    return ledger, [rzp_real, rzp_dup], [bank], gt


def build_partial_refund(case_idx: int):
    """partial_refund_split: refund_amount is a real numeric field.
    predicted_settlement = amount - rzp_fee - refund_amount (Section 5, Agent 3)
    """
    order_id = new_order_id()
    customer = rand_name()
    gross_amount = rand_amount(lo=1000.0, hi=5000.0)
    # Refund is 20–40% of gross
    refund_pct = RNG.uniform(0.20, 0.40)
    refund_amount = round(gross_amount * refund_pct, 2)
    fee = rzp_fee(gross_amount)
    # Bank settles: gross - fee - refund
    settlement = round(gross_amount - fee - refund_amount, 2)
    order_date = rand_date()
    captured_at = order_date
    lag = RNG.randint(1, 3)
    settlement_date = captured_at + timedelta(days=lag)
    method = RNG.choice(PAYMENT_METHODS)
    ledger_id = new_ledger_id()
    rzp_id = new_rzp_id()
    utr = new_utr()

    # Ledger shows net amount (what customer actually paid after refund)
    net_amount = round(gross_amount - refund_amount, 2)

    ledger = dict(
        ledger_id=ledger_id, order_id=order_id, customer_name=customer,
        amount=net_amount, currency="INR", order_date=order_date.isoformat(),
        payment_method=method, status="partially_refunded",
        refund_amount=refund_amount,
        notes=f"Partial refund of ₹{refund_amount} applied",
    )
    # Razorpay shows original gross charge
    rzp = dict(
        rzp_payment_id=rzp_id, order_id=order_id, amount=gross_amount,
        currency="INR", rzp_fee=fee, captured_at=captured_at.isoformat(),
        method=method, status="captured",
    )
    bank = dict(
        utr_number=utr, settlement_amount=settlement,
        settlement_date=settlement_date.isoformat(),
        narration=normal_narration(customer, order_id),
        bank_ref_type=RNG.choice(BANK_REF_TYPES),
    )
    gt = dict(
        case_id=f"case_{case_idx:04d}", case_type="partial_refund_split",
        expected_status="MATCHED", expected_sub_reason=None,
        ledger_ids=[ledger_id], rzp_ids=[rzp_id], utr_numbers=[utr],
    )
    return ledger, [rzp], [bank], gt


def build_pending_settlement(case_idx: int, as_of_date: date):
    """pending_settlement: Razorpay captured but bank hasn't settled yet (within 10 days)."""
    order_id = new_order_id()
    customer = rand_name()
    amount = rand_amount()
    fee = rzp_fee(amount)
    order_date = rand_date()
    # Captured recently enough that (as_of_date - captured_at) <= 10 days
    max_lag = SETTLEMENT_DATE_TOLERANCE_DAYS
    days_ago = RNG.randint(1, max_lag - 1)
    captured_at = as_of_date - timedelta(days=days_ago)
    # Keep within dataset window
    if captured_at < BASE_DATE:
        captured_at = BASE_DATE + timedelta(days=1)
    method = RNG.choice(PAYMENT_METHODS)
    ledger_id = new_ledger_id()
    rzp_id = new_rzp_id()

    ledger = dict(
        ledger_id=ledger_id, order_id=order_id, customer_name=customer,
        amount=amount, currency="INR", order_date=order_date.isoformat(),
        payment_method=method, status="paid", refund_amount=0.0,
        notes="",
    )
    rzp = dict(
        rzp_payment_id=rzp_id, order_id=order_id, amount=amount,
        currency="INR", rzp_fee=fee, captured_at=captured_at.isoformat(),
        method=method, status="captured",
    )
    # No bank row — settlement genuinely hasn't happened yet
    gt = dict(
        case_id=f"case_{case_idx:04d}", case_type="pending_settlement",
        expected_status="PARTIAL", expected_sub_reason="awaiting_settlement",
        ledger_ids=[ledger_id], rzp_ids=[rzp_id], utr_numbers=[],
    )
    return ledger, [rzp], [], gt


def build_failed_payment(case_idx: int):
    """failed_payment_orphan: declined payment, zero money moved."""
    order_id = new_order_id()
    customer = rand_name()
    amount = rand_amount()
    order_date = rand_date()
    method = RNG.choice(PAYMENT_METHODS)
    ledger_id = new_ledger_id()
    rzp_id = new_rzp_id()

    ledger = dict(
        ledger_id=ledger_id, order_id=order_id, customer_name=customer,
        amount=amount, currency="INR", order_date=order_date.isoformat(),
        payment_method=method, status="failed", refund_amount=0.0,
        notes="Payment declined",
    )
    # Razorpay logs the failed attempt — no capture, no fee
    rzp = dict(
        rzp_payment_id=rzp_id, order_id=order_id, amount=amount,
        currency="INR", rzp_fee=0.0, captured_at=order_date.isoformat(),
        method=method, status="failed",
    )
    # No bank row — nothing settled
    gt = dict(
        case_id=f"case_{case_idx:04d}", case_type="failed_payment_orphan",
        expected_status="MATCHED", expected_sub_reason="no_action_needed",
        ledger_ids=[ledger_id], rzp_ids=[rzp_id], utr_numbers=[],
    )
    return ledger, [rzp], [], gt


def build_missing_from_ledger(case_idx: int):
    """missing_from_ledger: real money moved (Rzp+Bank) but no ledger entry."""
    order_id = new_order_id()
    customer = rand_name()
    amount = rand_amount()
    fee = rzp_fee(amount)
    settlement = round(amount - fee, 2)
    order_date = rand_date()
    captured_at = order_date
    lag = RNG.randint(1, 3)
    settlement_date = captured_at + timedelta(days=lag)
    method = RNG.choice(PAYMENT_METHODS)
    rzp_id = new_rzp_id()
    utr = new_utr()

    # NO ledger row
    rzp = dict(
        rzp_payment_id=rzp_id, order_id=order_id, amount=amount,
        currency="INR", rzp_fee=fee, captured_at=captured_at.isoformat(),
        method=method, status="captured",
    )
    bank = dict(
        utr_number=utr, settlement_amount=settlement,
        settlement_date=settlement_date.isoformat(),
        narration=normal_narration(customer, order_id),
        bank_ref_type=RNG.choice(BANK_REF_TYPES),
    )
    gt = dict(
        case_id=f"case_{case_idx:04d}", case_type="missing_from_ledger",
        expected_status="PARTIAL", expected_sub_reason="no_ledger_record",
        ledger_ids=[], rzp_ids=[rzp_id], utr_numbers=[utr],
    )
    return None, [rzp], [bank], gt


def build_adversarial_pair(case_idx_a: int, case_idx_b: int):
    """adversarial_near_miss: two real transactions with amounts within ₹1.50–4.50, same date.
    Returns two complete (ledger, rzp, bank, gt) tuples — one pair = 2 records.
    """
    order_id_a = new_order_id()
    order_id_b = new_order_id()
    customer_a = rand_name()
    customer_b = rand_name()

    # Base amount, then second within ₹1.50–4.50
    amount_a = rand_amount(lo=800.0, hi=3000.0)
    delta = round(RNG.uniform(1.50, 4.50), 2)
    amount_b = round(amount_a + delta, 2)

    fee_a = rzp_fee(amount_a)
    fee_b = rzp_fee(amount_b)
    settlement_a = round(amount_a - fee_a, 2)
    settlement_b = round(amount_b - fee_b, 2)

    # Same order date for both
    order_date = rand_date()
    captured_at = order_date
    lag_a = RNG.randint(1, 3)
    lag_b = RNG.randint(1, 3)
    settlement_date_a = captured_at + timedelta(days=lag_a)
    settlement_date_b = captured_at + timedelta(days=lag_b)
    method = RNG.choice(PAYMENT_METHODS)

    led_id_a = new_ledger_id()
    led_id_b = new_ledger_id()
    rzp_id_a = new_rzp_id()
    rzp_id_b = new_rzp_id()
    utr_a = new_utr()
    utr_b = new_utr()

    ledger_a = dict(
        ledger_id=led_id_a, order_id=order_id_a, customer_name=customer_a,
        amount=amount_a, currency="INR", order_date=order_date.isoformat(),
        payment_method=method, status="paid", refund_amount=0.0, notes="",
    )
    rzp_a = dict(
        rzp_payment_id=rzp_id_a, order_id=order_id_a, amount=amount_a,
        currency="INR", rzp_fee=fee_a, captured_at=captured_at.isoformat(),
        method=method, status="captured",
    )
    bank_a = dict(
        utr_number=utr_a, settlement_amount=settlement_a,
        settlement_date=settlement_date_a.isoformat(),
        narration=normal_narration(customer_a, order_id_a),
        bank_ref_type=RNG.choice(BANK_REF_TYPES),
    )
    gt_a = dict(
        case_id=f"case_{case_idx_a:04d}", case_type="adversarial_near_miss",
        expected_status="MATCHED", expected_sub_reason=None,
        ledger_ids=[led_id_a], rzp_ids=[rzp_id_a], utr_numbers=[utr_a],
    )

    ledger_b = dict(
        ledger_id=led_id_b, order_id=order_id_b, customer_name=customer_b,
        amount=amount_b, currency="INR", order_date=order_date.isoformat(),
        payment_method=method, status="paid", refund_amount=0.0, notes="",
    )
    rzp_b = dict(
        rzp_payment_id=rzp_id_b, order_id=order_id_b, amount=amount_b,
        currency="INR", rzp_fee=fee_b, captured_at=captured_at.isoformat(),
        method=method, status="captured",
    )
    bank_b = dict(
        utr_number=utr_b, settlement_amount=settlement_b,
        settlement_date=settlement_date_b.isoformat(),
        narration=normal_narration(customer_b, order_id_b),
        bank_ref_type=RNG.choice(BANK_REF_TYPES),
    )
    gt_b = dict(
        case_id=f"case_{case_idx_b:04d}", case_type="adversarial_near_miss",
        expected_status="MATCHED", expected_sub_reason=None,
        ledger_ids=[led_id_b], rzp_ids=[rzp_id_b], utr_numbers=[utr_b],
    )

    return (ledger_a, [rzp_a], [bank_a], gt_a), (ledger_b, [rzp_b], [bank_b], gt_b)


def build_unidentified_bank_credit(case_idx: int):
    """unidentified_bank_credit: standalone bank row with no Rzp/ledger counterpart."""
    utr = new_utr()
    amount = round(RNG.uniform(50.0, 1500.0), 2)
    credit_date = rand_date()
    narration = RNG.choice(UNIDENTIFIED_NARRATIONS)

    bank = dict(
        utr_number=utr, settlement_amount=amount,
        settlement_date=credit_date.isoformat(),
        narration=narration,
        bank_ref_type=RNG.choice(BANK_REF_TYPES),
    )
    gt = dict(
        case_id=f"case_{case_idx:04d}", case_type="unidentified_bank_credit",
        expected_status="UNRESOLVED", expected_sub_reason="unidentified_bank_credit",
        ledger_ids=[], rzp_ids=[], utr_numbers=[utr],
    )
    return None, [], [bank], gt


def build_semantic_brand(case_idx: int, semantic_idx: int):
    """semantic_brand_narration: full Ledger→Rzp→Bank chain but bank narration is
    the merchant's registered legal name — solvable only with MERCHANT_PROFILE context.
    Uses the 3 fixed narration/notes pairs from Section 3.
    """
    order_id = new_order_id()
    customer = rand_name()
    amount = rand_amount(lo=400.0, hi=3000.0)
    fee = rzp_fee(amount)
    settlement = round(amount - fee, 2)
    order_date = rand_date()
    captured_at = order_date
    lag = RNG.randint(1, 3)
    settlement_date = captured_at + timedelta(days=lag)
    method = RNG.choice(PAYMENT_METHODS)
    ledger_id = new_ledger_id()
    rzp_id = new_rzp_id()
    utr = new_utr()

    # Cycle through the 3 variants (one per semantic record)
    notes = SEMANTIC_NOTES[semantic_idx % 3]
    narration = SEMANTIC_NARRATIONS[semantic_idx % 3]

    ledger = dict(
        ledger_id=ledger_id, order_id=order_id, customer_name=customer,
        amount=amount, currency="INR", order_date=order_date.isoformat(),
        payment_method=method, status="paid", refund_amount=0.0,
        notes=notes,
    )
    rzp = dict(
        rzp_payment_id=rzp_id, order_id=order_id, amount=amount,
        currency="INR", rzp_fee=fee, captured_at=captured_at.isoformat(),
        method=method, status="captured",
    )
    bank = dict(
        utr_number=utr, settlement_amount=settlement,
        settlement_date=settlement_date.isoformat(),
        narration=narration,
        bank_ref_type=RNG.choice(BANK_REF_TYPES),
    )
    gt = dict(
        case_id=f"case_{case_idx:04d}", case_type="semantic_brand_narration",
        expected_status="MATCHED", expected_sub_reason=None,
        ledger_ids=[ledger_id], rzp_ids=[rzp_id], utr_numbers=[utr],
    )
    return ledger, [rzp], [bank], gt


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate(dataset_size: int = 110, out_dir: str = None):
    """
    dataset_size: 110 → data/raw_100/  |  550 → data/raw/
    """
    if dataset_size == 110:
        counts = dict(
            N_CLEAN=55, N_DELAYED=10, N_HARD=10, N_DUP=5, N_REFUND=5,
            N_PENDING=5, N_FAILED=5, N_MISSING_LEDGER=3,
            N_ADVERSARIAL_PAIRS=2, N_UNIDENTIFIED=5, N_SEMANTIC=3,
        )
        out_path = ROOT / "data" / "raw_100"
    elif dataset_size == 550:
        counts = dict(
            N_CLEAN=275, N_DELAYED=50, N_HARD=50, N_DUP=25, N_REFUND=25,
            N_PENDING=25, N_FAILED=25, N_MISSING_LEDGER=15,
            N_ADVERSARIAL_PAIRS=10, N_UNIDENTIFIED=25, N_SEMANTIC=15,
        )
        out_path = ROOT / "data" / "raw"
    else:
        raise ValueError(f"Unsupported dataset_size: {dataset_size}. Use 110 or 550.")

    if out_dir:
        out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    ledger_rows = []
    rzp_rows = []
    bank_rows = []
    ground_truth = []

    case_idx = 0

    # Compute as_of_date needed for pending_settlement (used in builder)
    # Base AS_OF_DATE = BASE_DATE + DAY_SPAN (the max possible date in the dataset)
    as_of = BASE_DATE + timedelta(days=DAY_SPAN)

    # 1. clean_triple_match
    for _ in range(counts["N_CLEAN"]):
        l, r, b, gt = build_clean(case_idx)
        ledger_rows.append(l); rzp_rows.extend(r); bank_rows.extend(b)
        ground_truth.append(gt); case_idx += 1

    # 2. delayed_settlement
    for _ in range(counts["N_DELAYED"]):
        l, r, b, gt = build_delayed(case_idx)
        ledger_rows.append(l); rzp_rows.extend(r); bank_rows.extend(b)
        ground_truth.append(gt); case_idx += 1

    # 3. hard_garbled_narration
    for _ in range(counts["N_HARD"]):
        l, r, b, gt = build_hard_garbled(case_idx)
        ledger_rows.append(l); rzp_rows.extend(r); bank_rows.extend(b)
        ground_truth.append(gt); case_idx += 1

    # 4. duplicate_capture
    for _ in range(counts["N_DUP"]):
        l, r, b, gt = build_duplicate_capture(case_idx)
        ledger_rows.append(l); rzp_rows.extend(r); bank_rows.extend(b)
        ground_truth.append(gt); case_idx += 1

    # 5. partial_refund_split
    for _ in range(counts["N_REFUND"]):
        l, r, b, gt = build_partial_refund(case_idx)
        ledger_rows.append(l); rzp_rows.extend(r); bank_rows.extend(b)
        ground_truth.append(gt); case_idx += 1

    # 6. pending_settlement
    for _ in range(counts["N_PENDING"]):
        l, r, b, gt = build_pending_settlement(case_idx, as_of_date=as_of)
        ledger_rows.append(l); rzp_rows.extend(r); bank_rows.extend(b)
        ground_truth.append(gt); case_idx += 1

    # 7. failed_payment_orphan
    for _ in range(counts["N_FAILED"]):
        l, r, b, gt = build_failed_payment(case_idx)
        ledger_rows.append(l); rzp_rows.extend(r); bank_rows.extend(b)
        ground_truth.append(gt); case_idx += 1

    # 8. missing_from_ledger
    for _ in range(counts["N_MISSING_LEDGER"]):
        l, r, b, gt = build_missing_from_ledger(case_idx)
        # l is None — no ledger row
        if l: ledger_rows.append(l)
        rzp_rows.extend(r); bank_rows.extend(b)
        ground_truth.append(gt); case_idx += 1

    # 9. adversarial_near_miss (each pair = 2 case records)
    for _ in range(counts["N_ADVERSARIAL_PAIRS"]):
        (la, ra, ba, gta), (lb, rb, bb, gtb) = build_adversarial_pair(case_idx, case_idx + 1)
        ledger_rows.append(la); rzp_rows.extend(ra); bank_rows.extend(ba)
        ground_truth.append(gta); case_idx += 1
        ledger_rows.append(lb); rzp_rows.extend(rb); bank_rows.extend(bb)
        ground_truth.append(gtb); case_idx += 1

    # 10. unidentified_bank_credit
    for _ in range(counts["N_UNIDENTIFIED"]):
        l, r, b, gt = build_unidentified_bank_credit(case_idx)
        # l is None, r is []
        bank_rows.extend(b)
        ground_truth.append(gt); case_idx += 1

    # 11. semantic_brand_narration
    for sem_i in range(counts["N_SEMANTIC"]):
        l, r, b, gt = build_semantic_brand(case_idx, semantic_idx=sem_i)
        ledger_rows.append(l); rzp_rows.extend(r); bank_rows.extend(b)
        ground_truth.append(gt); case_idx += 1

    # ---------------------------------------------------------------------------
    # Write CSVs
    # ---------------------------------------------------------------------------
    ledger_df = pd.DataFrame(ledger_rows, columns=[
        "ledger_id", "order_id", "customer_name", "amount", "currency",
        "order_date", "payment_method", "status", "refund_amount", "notes",
    ])
    rzp_df = pd.DataFrame(rzp_rows, columns=[
        "rzp_payment_id", "order_id", "amount", "currency",
        "rzp_fee", "captured_at", "method", "status",
    ])
    bank_df = pd.DataFrame(bank_rows, columns=[
        "utr_number", "settlement_amount", "settlement_date",
        "narration", "bank_ref_type",
    ])

    ledger_df.to_csv(out_path / "internal_ledger.csv", index=False)
    rzp_df.to_csv(out_path / "razorpay_export.csv", index=False)
    bank_df.to_csv(out_path / "bank_statement.csv", index=False)

    # ---------------------------------------------------------------------------
    # Write ground_truth.json — also goes to data/ground_truth/
    # ---------------------------------------------------------------------------
    gt_path = ROOT / "data" / "ground_truth"
    gt_path.mkdir(parents=True, exist_ok=True)

    # Determine filename suffix
    suffix = "110" if dataset_size == 110 else "550"
    gt_file = gt_path / f"ground_truth_{suffix}.json"

    with open(gt_file, "w") as f:
        json.dump(ground_truth, f, indent=2)

    # Also symlink/copy as ground_truth.json for reporting_agent convenience
    gt_main = gt_path / "ground_truth.json"
    with open(gt_main, "w") as f:
        json.dump(ground_truth, f, indent=2)

    # ---------------------------------------------------------------------------
    # Print per-case-type counts for verification
    # ---------------------------------------------------------------------------
    from collections import Counter
    ct_counts = Counter(g["case_type"] for g in ground_truth)
    total_cases = len(ground_truth)

    print(f"\n{'='*60}")
    print(f"Dataset generated → {out_path}")
    print(f"{'='*60}")
    print(f"{'Case type':<35} {'Count':>6}")
    print(f"{'-'*42}")
    for ct in [
        "clean_triple_match", "delayed_settlement", "hard_garbled_narration",
        "duplicate_capture", "partial_refund_split", "pending_settlement",
        "failed_payment_orphan", "missing_from_ledger", "adversarial_near_miss",
        "unidentified_bank_credit", "semantic_brand_narration",
    ]:
        print(f"  {ct:<33} {ct_counts.get(ct, 0):>6}")
    print(f"{'-'*42}")
    print(f"  {'TOTAL CASES':<33} {total_cases:>6}")
    print()
    print(f"  Ledger rows  : {len(ledger_df)}")
    print(f"  Razorpay rows: {len(rzp_df)}")
    print(f"  Bank rows    : {len(bank_df)}")
    print()
    print(f"  ground_truth → {gt_file}")

    # Verify expected total
    expected = dataset_size
    assert total_cases == expected, (
        f"CASE COUNT MISMATCH: expected {expected}, got {total_cases}"
    )
    print(f"\n  ✓ Case count verified: {total_cases} == {expected}")
    print(f"{'='*60}\n")

    return ledger_df, rzp_df, bank_df, ground_truth


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset", type=int, default=110,
        choices=[110, 550],
        help="110 → data/raw_100/ (default)  |  550 → data/raw/",
    )
    args = parser.parse_args()
    generate(dataset_size=args.dataset)
