"""
agents/fuzzy_match_agent.py  —  Agent 3
Fuzzy Match Engine.

Three sub-steps, in order (Section 5, Agent 3):

3a. Candidate generation
    For each unmatched Razorpay record, compute predicted_settlement using
    the ACTUAL rzp_fee column — never a recomputed formula.
    For partial_refund_split (status=="partially_refunded"), also subtract
    the real refund_amount column from the ledger row.
    Filter bank rows to a shortlist: settlement_date within
    captured_at + 0..SETTLEMENT_DATE_TOLERANCE_DAYS AND settlement_amount
    within ±AMOUNT_TOLERANCE_RUPEES of predicted_settlement.

3b. Composite scoring
    score = w_amount * amount_score + w_date * date_score + w_text * text_score
    Weights from config.FUZZY_MATCH_WEIGHTS.

3c. Global one-to-one assignment via Hungarian algorithm
    scipy.optimize.linear_sum_assignment over the full candidate cost matrix.
    Guarantees the mathematically optimal one-to-one assignment — correctly
    handles duplicate_capture and adversarial_near_miss where greedy fails.

Early exit:
    score >= FUZZY_AUTO_MATCH_THRESHOLD (0.90) → MATCHED (stop, no LLM)
    score < FUZZY_MIN_CANDIDATE_THRESHOLD (0.50) → route directly to Agent 6
    0.50 <= score < 0.90 → send to Agent 4 (LLM reasoning)

Input:
    Unmatched Razorpay+Bank records from Agent 2 PLUS all bank records
    (including those that may match missing_from_ledger Rzp rows).

Output:
    FuzzyMatchResult with:
    - auto_matched_pairs   : score >= 0.90 → MATCHED
    - llm_candidates       : 0.50 <= score < 0.90 → needs Agent 4
    - unmatched_rzp        : no bank candidate at all, or best score < 0.50
    - unmatched_bank       : bank rows that matched nothing (includes
                             unidentified_bank_credit)
    - missing_ledger_pairs : Rzp+Bank matched but no ledger row
"""

import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.optimize import linear_sum_assignment
from rapidfuzz import fuzz
from pydantic import BaseModel

_ROOT = Path(__file__).resolve().parents[2]  # Go up 2 levels now (core -> agents -> root)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.utils.config import (
    FUZZY_MATCH_WEIGHTS,
    FUZZY_AUTO_MATCH_THRESHOLD,
    FUZZY_MIN_CANDIDATE_THRESHOLD,
    SETTLEMENT_DATE_TOLERANCE_DAYS,
    AMOUNT_TOLERANCE_RUPEES,
    MERCHANT_PROFILE,
)
from agents.core.ingestion_agent import CanonicalRecord
from agents.core.exact_match_agent import ExactMatchResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Merchant-name narration detector
# Bank narrations that look like the merchant's registered legal name MUST
# route to Agent 4 (semantic reasoning), even if amount+date scores are high.
# Agent 3 cannot distinguish "FITZONE WELLNESS PVT LTD" from an ordinary
# narration by character overlap alone — that's exactly Agent 4's job.
# ---------------------------------------------------------------------------
_MERCHANT_KEYWORDS: list[str] = []

def _build_merchant_keywords() -> list[str]:
    """
    Build keyword list from MERCHANT_PROFILE for narration detection.
    Uses explicit narration_aliases when present (preferred — these come
    from real onboarding/KYC data, not algorithmic derivation).
    Falls back to extracting significant tokens from the registered legal name.
    """
    # Explicit aliases are the authoritative source
    aliases = MERCHANT_PROFILE.get("narration_aliases", [])
    if aliases:
        return [a.upper() for a in aliases]
    # Fallback: extract tokens from registered legal name
    legal = MERCHANT_PROFILE.get("registered_legal_name", "")
    skip  = {"WITH", "FROM", "THAT", "THIS", "PRIVATE", "LIMITED"}
    return [w.upper() for w in legal.split() if len(w) >= 4 and w.upper() not in skip]

_MERCHANT_KEYWORDS = _build_merchant_keywords()


def _is_merchant_name_narration(narration: str) -> bool:
    """
    Return True if the bank narration looks like the merchant's own
    registered legal name — these records must route to Agent 4 regardless
    of amount/date score, because they require semantic reasoning + MERCHANT_PROFILE.
    """
    narr_upper = narration.upper()
    return any(kw in narr_upper for kw in _MERCHANT_KEYWORDS)




class FuzzyScores(BaseModel):
    amount_score: float   # 0–1
    date_score:   float   # 0–1
    text_score:   float   # 0–1
    composite:    float   # weighted sum


class FuzzyMatchPair(BaseModel):
    """One Razorpay↔Bank candidate pair with full scoring."""
    rzp_record:          CanonicalRecord
    bank_record:         CanonicalRecord
    predicted_settlement: float
    scores:              FuzzyScores
    ledger_record:       Optional[CanonicalRecord] = None   # if available from exact match
    refund_amount:       float = 0.0

    model_config = {"arbitrary_types_allowed": True}


class FuzzyMatchResult(BaseModel):
    auto_matched_pairs:   list[FuzzyMatchPair]   # score >= 0.90 → MATCHED
    llm_candidates:       list[FuzzyMatchPair]   # 0.50 <= score < 0.90 → Agent 4
    unmatched_rzp:        list[CanonicalRecord]  # no bank candidate / score < 0.50
    unmatched_bank:       list[CanonicalRecord]  # bank rows that matched nothing
    missing_ledger_pairs: list[FuzzyMatchPair]   # Rzp+Bank matched, no ledger row

    model_config = {"arbitrary_types_allowed": True}

    def summary(self) -> str:
        return (
            f"FuzzyMatch: {len(self.auto_matched_pairs)} auto-matched | "
            f"{len(self.llm_candidates)} → LLM | "
            f"{len(self.unmatched_rzp)} unmatched_rzp | "
            f"{len(self.unmatched_bank)} unmatched_bank | "
            f"{len(self.missing_ledger_pairs)} missing_ledger"
        )


# ---------------------------------------------------------------------------
# 3a. Predicted settlement value
# ---------------------------------------------------------------------------

def _predicted_settlement(
    rzp_record:   CanonicalRecord,
    ledger_record: Optional[CanonicalRecord],
) -> tuple[float, float]:
    """
    Compute predicted_settlement from the ACTUAL rzp_fee column.
    Never recomputes the fee formula.

    For partial_refund_split (ledger status == "partially_refunded"):
        predicted = amount - rzp_fee - refund_amount
    Otherwise:
        predicted = amount - rzp_fee

    Returns (predicted_settlement, refund_amount_used).
    """
    rzp_fee    = float(rzp_record.raw.get("rzp_fee", 0.0) or 0.0)
    rzp_amount = rzp_record.amount

    refund_amount = 0.0
    if ledger_record and ledger_record.raw.get("status") == "partially_refunded":
        refund_amount = float(ledger_record.raw.get("refund_amount", 0.0) or 0.0)

    predicted = rzp_amount - rzp_fee - refund_amount
    return round(predicted, 2), refund_amount


# ---------------------------------------------------------------------------
# 3b. Individual score components
# ---------------------------------------------------------------------------

def _amount_score(predicted: float, bank_amount: float) -> float:
    """
    1.0 for exact match, decays linearly to 0.0 at ±AMOUNT_TOLERANCE_RUPEES*4.
    Uses a tight window so near-miss adversarial cases don't score too high.
    """
    diff = abs(predicted - bank_amount)
    # Full tolerance window (±5): score = 1.0
    # Beyond 4× tolerance: score = 0.0
    hard_limit = AMOUNT_TOLERANCE_RUPEES * 4
    if diff <= AMOUNT_TOLERANCE_RUPEES:
        return 1.0
    if diff >= hard_limit:
        return 0.0
    return round(1.0 - (diff - AMOUNT_TOLERANCE_RUPEES) / (hard_limit - AMOUNT_TOLERANCE_RUPEES), 4)


def _date_score(rzp_date: date, bank_date: date) -> float:
    """
    1.0 for same day, decays linearly to 0.0 at SETTLEMENT_DATE_TOLERANCE_DAYS.
    Negative lag (bank before capture) always scores 0.
    """
    lag = (bank_date - rzp_date).days
    if lag < 0:
        return 0.0
    if lag == 0:
        return 1.0
    if lag >= SETTLEMENT_DATE_TOLERANCE_DAYS:
        return 0.0
    return round(1.0 - lag / SETTLEMENT_DATE_TOLERANCE_DAYS, 4)


def _text_score(
    rzp_record:    CanonicalRecord,
    bank_record:   CanonicalRecord,
    ledger_record: Optional[CanonicalRecord] = None,
) -> float:
    """
    Character-level similarity via rapidfuzz.
    Uses the ledger customer_name as the primary text signal when available
    — it's far more useful than the Rzp payment method ("card", "upi").

    For hard_garbled_narration ("UPI-9284") and semantic_brand_narration
    ("FITZONE WELLNESS PVT LTD") this will score near-zero — intentional.
    Those cases are designed to fail Agent 3 and route to Agent 4.
    Text is a tiebreaker (weight 0.10), not a primary signal.
    """
    # Prefer ledger customer_name; fall back to rzp text_field (payment method)
    text_a = (ledger_record.text_field if ledger_record else None) or rzp_record.text_field or ""
    text_b = bank_record.text_field or ""

    if not text_a or not text_b:
        return 0.0

    r1 = fuzz.token_sort_ratio(text_a, text_b) / 100.0
    r2 = fuzz.partial_ratio(text_a, text_b) / 100.0
    return round(max(r1, r2), 4)


def _composite_score(amount_s: float, date_s: float, text_s: float) -> float:
    w = FUZZY_MATCH_WEIGHTS
    return round(
        w["amount"] * amount_s + w["date"] * date_s + w["text"] * text_s,
        4,
    )


# ---------------------------------------------------------------------------
# 3c. Hungarian algorithm assignment
# ---------------------------------------------------------------------------

def _hungarian_assignment(
    rzp_records:   list[CanonicalRecord],
    bank_records:  list[CanonicalRecord],
    score_matrix:  np.ndarray,            # shape (n_rzp, n_bank)
) -> list[tuple[int, int, float]]:
    """
    Solve the one-to-one assignment problem optimally.
    Returns list of (rzp_idx, bank_idx, score) for the best assignment.

    scipy.optimize.linear_sum_assignment minimises cost → we negate scores
    to turn it into a maximisation problem.
    """
    if score_matrix.size == 0:
        return []

    cost_matrix = 1.0 - score_matrix   # negate: higher score = lower cost
    row_ind, col_ind = linear_sum_assignment(cost_matrix)

    assignments = []
    for r, c in zip(row_ind, col_ind):
        score = score_matrix[r, c]
        assignments.append((int(r), int(c), float(score)))
    return assignments


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_fuzzy_match(
    exact_result:    ExactMatchResult,
    ledger_by_order: dict[str, CanonicalRecord],   # order_id → ledger record
    as_of_date:      date,
) -> FuzzyMatchResult:
    """
    Run the full three-step fuzzy matching pipeline.

    Parameters
    ----------
    exact_result     : output from Agent 2 — provides unmatched_rzp,
                       unmatched_bank (= all_bank at this stage), plus
                       already-matched pairs we don't touch.
    ledger_by_order  : maps order_id → ledger CanonicalRecord, used to
                       look up refund_amount for partial_refund_split cases.
    as_of_date       : AS_OF_DATE from Agent 0 — not used for matching here
                       but passed through for traceability; Agent 6 uses it.

    Returns
    -------
    FuzzyMatchResult
    """
    # Candidates from Agent 2:
    # - unmatched_rzp: Razorpay rows whose order_id had NO ledger match
    #   (missing_from_ledger) — they still need a bank match
    # - all_bank: every bank row (none have been consumed yet)
    #
    # We also need to match the *captured* Rzp rows that DID match a ledger
    # (from exact_result.matched_pairs) against bank rows. Those pairs are
    # already Ledger+Rzp confirmed; we now find their bank settlement.

    # Build the full set of Rzp records that need a bank match:
    # 1. From exact_match pairs — use only the *captured* Rzp row
    rzp_for_bank: list[CanonicalRecord] = []
    rzp_ledger_map: dict[str, CanonicalRecord] = {}   # rzp record_id → ledger record

    for pair in exact_result.matched_pairs:
        # For duplicate_capture: pick the captured row, ignore the failed one
        captured = [r for r in pair.rzp_records if r.status == "captured"]
        failed   = [r for r in pair.rzp_records if r.status == "failed"]

        # failed_payment_orphan: ledger=failed, rzp=failed → no bank match needed
        if pair.is_failed:
            continue

        if captured:
            rzp_rec = captured[0]
            rzp_for_bank.append(rzp_rec)
            rzp_ledger_map[rzp_rec.record_id] = pair.ledger_record

    # 2. From missing_from_ledger: Rzp rows with no ledger counterpart
    missing_ledger_rzp_ids: set[str] = {r.record_id for r in exact_result.unmatched_rzp}
    for rzp_rec in exact_result.unmatched_rzp:
        rzp_for_bank.append(rzp_rec)
        # No ledger record for these

    bank_records: list[CanonicalRecord] = exact_result.all_bank

    logger.debug(
        "Fuzzy match: %d Rzp records seeking bank match | %d bank rows available",
        len(rzp_for_bank), len(bank_records),
    )

    if not rzp_for_bank or not bank_records:
        return FuzzyMatchResult(
            auto_matched_pairs   = [],
            llm_candidates       = [],
            unmatched_rzp        = rzp_for_bank,
            unmatched_bank       = bank_records,
            missing_ledger_pairs = [],
        )

    # ------------------------------------------------------------------
    # 3a. Build score matrix
    # ------------------------------------------------------------------
    n_rzp  = len(rzp_for_bank)
    n_bank = len(bank_records)
    score_matrix = np.zeros((n_rzp, n_bank), dtype=float)

    # Also store individual scores and predicted values for later reporting
    detail: dict[tuple[int,int], dict] = {}

    for i, rzp_rec in enumerate(rzp_for_bank):
        ledger_rec = rzp_ledger_map.get(rzp_rec.record_id)
        predicted, refund_used = _predicted_settlement(rzp_rec, ledger_rec)

        for j, bank_rec in enumerate(bank_records):
            # Date window pre-filter: bank_date must be within [captured, captured+10]
            lag = (bank_rec.date - rzp_rec.date).days
            if lag < 0 or lag > SETTLEMENT_DATE_TOLERANCE_DAYS:
                score_matrix[i, j] = 0.0
                continue

            # Amount pre-filter: within 4× tolerance (hard cutoff for candidate gen)
            if abs(predicted - bank_rec.amount) > AMOUNT_TOLERANCE_RUPEES * 4:
                score_matrix[i, j] = 0.0
                continue

            a_s = _amount_score(predicted, bank_rec.amount)
            d_s = _date_score(rzp_rec.date, bank_rec.date)
            t_s = _text_score(rzp_rec, bank_rec, ledger_rec)
            c_s = _composite_score(a_s, d_s, t_s)

            score_matrix[i, j] = c_s
            detail[(i, j)] = {
                "amount_score": a_s,
                "date_score":   d_s,
                "text_score":   t_s,
                "composite":    c_s,
                "predicted":    predicted,
                "refund_amount": refund_used,
            }

    # ------------------------------------------------------------------
    # 3c. Hungarian assignment
    # ------------------------------------------------------------------
    assignments = _hungarian_assignment(rzp_for_bank, bank_records, score_matrix)

    assigned_rzp_idxs  = set()
    assigned_bank_idxs = set()

    auto_matched_pairs:   list[FuzzyMatchPair] = []
    llm_candidates:       list[FuzzyMatchPair] = []
    missing_ledger_pairs: list[FuzzyMatchPair] = []

    for rzp_idx, bank_idx, score in assignments:
        rzp_rec    = rzp_for_bank[rzp_idx]
        bank_rec   = bank_records[bank_idx]
        ledger_rec = rzp_ledger_map.get(rzp_rec.record_id)

        d = detail.get((rzp_idx, bank_idx), {})
        scores = FuzzyScores(
            amount_score = d.get("amount_score", 0.0),
            date_score   = d.get("date_score",   0.0),
            text_score   = d.get("text_score",   0.0),
            composite    = score,
        )
        predicted  = d.get("predicted", rzp_rec.amount)
        refund_amt = d.get("refund_amount", 0.0)

        # Skip assignments with score=0 (no valid candidate)
        if score < FUZZY_MIN_CANDIDATE_THRESHOLD / 2:
            continue

        pair = FuzzyMatchPair(
            rzp_record           = rzp_rec,
            bank_record          = bank_rec,
            predicted_settlement = predicted,
            scores               = scores,
            ledger_record        = ledger_rec,
            refund_amount        = refund_amt,
        )

        assigned_rzp_idxs.add(rzp_idx)
        assigned_bank_idxs.add(bank_idx)

        is_missing_ledger = rzp_rec.record_id in missing_ledger_rzp_ids

        # Force merchant-name narrations to LLM regardless of score —
        # Agent 3 cannot resolve semantic_brand_narration by character overlap.
        force_llm = _is_merchant_name_narration(bank_rec.text_field)

        if score >= FUZZY_AUTO_MATCH_THRESHOLD and not force_llm:
            if is_missing_ledger:
                missing_ledger_pairs.append(pair)
            else:
                auto_matched_pairs.append(pair)
            logger.debug(
                "AUTO-MATCH: rzp=%s bank=%s score=%.3f predicted=%.2f bank_amt=%.2f",
                rzp_rec.source_ref, bank_rec.source_ref, score, predicted, bank_rec.amount,
            )
        elif score >= FUZZY_MIN_CANDIDATE_THRESHOLD or force_llm:
            if is_missing_ledger:
                # missing_from_ledger with mid-confidence match — still a PARTIAL candidate
                missing_ledger_pairs.append(pair)
            else:
                llm_candidates.append(pair)
            logger.debug(
                "LLM candidate: rzp=%s bank=%s score=%.3f",
                rzp_rec.source_ref, bank_rec.source_ref, score,
            )
        else:
            # Below threshold — don't mark as assigned
            assigned_rzp_idxs.discard(rzp_idx)
            assigned_bank_idxs.discard(bank_idx)

    # Records that got no good assignment
    unmatched_rzp = [
        rzp_for_bank[i] for i in range(n_rzp)
        if i not in assigned_rzp_idxs
    ]
    unmatched_bank = [
        bank_records[j] for j in range(n_bank)
        if j not in assigned_bank_idxs
    ]

    result = FuzzyMatchResult(
        auto_matched_pairs   = auto_matched_pairs,
        llm_candidates       = llm_candidates,
        unmatched_rzp        = unmatched_rzp,
        unmatched_bank       = unmatched_bank,
        missing_ledger_pairs = missing_ledger_pairs,
    )

    logger.info(result.summary())
    return result


# ---------------------------------------------------------------------------
# Standalone smoke-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from agents.utils.data_loader import load_raw_data
    from agents.core.ingestion_agent import ingest
    from agents.core.exact_match_agent import run_exact_match
    from agents.utils.as_of_date import compute_as_of_date

    ledger_df, rzp_df, bank_df = load_raw_data()
    as_of = compute_as_of_date(ledger_df, rzp_df, bank_df)

    ing          = ingest(ledger_df, rzp_df, bank_df)
    exact_result = run_exact_match(ing.ledger_records, ing.razorpay_records, ing.bank_records)

    # Build ledger_by_order lookup
    ledger_by_order = {r.order_id: r for r in ing.ledger_records if r.order_id}

    fuzzy_result = run_fuzzy_match(exact_result, ledger_by_order, as_of)

    print("\n=== Fuzzy Match smoke test ===")
    print(f"  Auto-matched (score>=0.90) : {len(fuzzy_result.auto_matched_pairs)}")
    print(f"  LLM candidates (0.50–0.90) : {len(fuzzy_result.llm_candidates)}")
    print(f"  Missing ledger pairs       : {len(fuzzy_result.missing_ledger_pairs)}")
    print(f"  Unmatched Rzp              : {len(fuzzy_result.unmatched_rzp)}")
    print(f"  Unmatched bank             : {len(fuzzy_result.unmatched_bank)}")

    print("\n  Score distribution (auto-matched):")
    for p in sorted(fuzzy_result.auto_matched_pairs, key=lambda x: x.scores.composite, reverse=True)[:5]:
        print(f"    rzp={p.rzp_record.source_ref} bank={p.bank_record.source_ref} "
              f"score={p.scores.composite:.3f} "
              f"amt_s={p.scores.amount_score:.2f} "
              f"date_s={p.scores.date_score:.2f} "
              f"text_s={p.scores.text_score:.2f} "
              f"predicted={p.predicted_settlement:.2f} actual={p.bank_record.amount:.2f}")

    if fuzzy_result.llm_candidates:
        print("\n  LLM candidates (mid-confidence):")
        for p in fuzzy_result.llm_candidates:
            print(f"    rzp={p.rzp_record.source_ref} bank={p.bank_record.source_ref} "
                  f"score={p.scores.composite:.3f}")

    if fuzzy_result.missing_ledger_pairs:
        print("\n  Missing ledger pairs (Rzp+Bank matched, no ledger):")
        for p in fuzzy_result.missing_ledger_pairs:
            print(f"    rzp={p.rzp_record.source_ref} bank={p.bank_record.source_ref} "
                  f"score={p.scores.composite:.3f}")

    print("\n  Unmatched bank (includes unidentified_bank_credit):")
    for r in fuzzy_result.unmatched_bank:
        print(f"    {r.source_ref}  narration='{r.text_field}'  amount={r.amount}")

    # Verify: partial_refund_split cases should all auto-match (refund_amount used)
    refund_pairs = [p for p in fuzzy_result.auto_matched_pairs if p.refund_amount > 0]
    print(f"\n  Partial refund pairs auto-matched : {len(refund_pairs)}  (expect 5)")
    for p in refund_pairs:
        print(f"    rzp={p.rzp_record.source_ref}  refund={p.refund_amount:.2f}  "
              f"predicted={p.predicted_settlement:.2f}  actual={p.bank_record.amount:.2f}  "
              f"score={p.scores.composite:.3f}")

    print("\n  ✓ Smoke test complete")
    print("=== OK ===\n")
