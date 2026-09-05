"""
agents/llm_reasoning_agent.py  —  Agent 4
LLM Reasoning Agent (Semantic Matching layer).

Handles the genuinely ambiguous slice left after Agents 2–3.
Target: under 15-20% of total records (on the 110-record dev set: 13 candidates).

Key spec requirements (Section 5, Agent 4):
- Always includes MERCHANT_PROFILE in every prompt — not conditionally.
  For semantic_brand_narration records it's the deciding fact; for others
  it's simply unused context. Never omit it.
- Explicitly surfaces the ledger `notes` field in candidate context —
  this is the other half of the signal for semantic_brand_narration.
- structured output enforced via Pydantic / JSON mode — never plain-text JSON instruction.
- semantic_similarity is a SEPARATE scored field (0.0-1.0), not folded into reasoning.
- Few-shot prompt includes the exact semantic_brand_narration example from the spec.
- Risk asymmetry stated explicitly: false match > false non-match.
- All calls go through call_llm() — no direct SDK calls here.
- On LLMError: record routes to UNRESOLVED, pipeline never crashes.
"""

import logging
import sys
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.utils.config import (
    MERCHANT_PROFILE,
    GROQ_REASONING_MODEL,
    HIGH_VALUE_REVIEW_THRESHOLD_RUPEES,
)
from agents.core.fuzzy_match_agent import FuzzyMatchPair
from agents.core.ingestion_agent import CanonicalRecord
from agents.utils.llm_provider import call_llm, call_llm_batch, LLMError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output schema — semantic_similarity is a required top-level field
# ---------------------------------------------------------------------------

class Agent4Result(BaseModel):
    record_id:          str
    candidate_ids:      list[str]
    semantic_similarity: float = Field(
        ge=0.0, le=1.0,
        description="0-1: do these descriptions refer to the same real-world event?",
    )
    decision:    str   = Field(description="match | no_match | uncertain")
    confidence:  float = Field(ge=0.0, le=1.0)
    reasoning:   str   = Field(description="1-2 sentence explanation")
    risk_flags:  list[str] = Field(default_factory=list)

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v):
        """LLM sometimes returns string labels instead of floats — coerce them."""
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            mapping = {"high": 0.90, "medium": 0.65, "low": 0.35,
                       "very high": 0.95, "very low": 0.20}
            return mapping.get(v.lower().strip(), 0.50)
        return float(v)

    @field_validator("semantic_similarity", mode="before")
    @classmethod
    def coerce_semantic_similarity(cls, v):
        """Same coercion for semantic_similarity."""
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            mapping = {"high": 0.85, "medium": 0.55, "low": 0.20,
                       "very high": 0.95, "very low": 0.10}
            return mapping.get(v.lower().strip(), 0.50)
        return float(v)


# ---------------------------------------------------------------------------
# PII masking — strip/truncate before prompting
# ---------------------------------------------------------------------------

def _mask_pii(text: str, max_len: int = 80) -> str:
    """Truncate long strings and mask obvious PII patterns."""
    if not text:
        return ""
    # Truncate
    t = text[:max_len]
    return t


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_FEW_SHOT = """
You are a financial reconciliation analyst. Determine if a Razorpay payment and a bank settlement refer to the same transaction.

Rules:
- A false match is worse than an honest uncertain. Be conservative.
- Amount must match expected settlement (rzp_amount - rzp_fee - refund_amount).
- Lag 1-10 days is normal. Lag 5-9 days is delayed but still valid.
- Bank narrations are often garbled or abbreviated - focus on amount and date.
- If narration matches this merchant's registered name or aliases, it IS this merchant's own settlement.

MERCHANT PROFILE:
  Brand: {brand_name}
  Registered name: {registered_legal_name}
  Known settlement narration aliases: {narration_aliases}

CANDIDATE PAIR:
{candidate_block}

Respond with JSON only. Fields: record_id (string), candidate_ids (array of 2 strings), semantic_similarity (0.0-1.0), decision (match/no_match/uncertain), confidence (0.0-1.0), reasoning (one sentence), risk_flags (array of strings).
""".strip()


def _build_prompt(
    rzp_record:   CanonicalRecord,
    bank_record:  CanonicalRecord,
    ledger_record: Optional[CanonicalRecord],
    fuzzy_scores: Optional[object] = None,
) -> str:
    """Construct the full prompt for a single (Rzp, Bank) candidate pair."""

    # --- candidate block ---
    led_customer = ledger_record.text_field if ledger_record else "N/A"
    led_notes    = ledger_record.notes      if ledger_record else ""
    led_amount   = ledger_record.amount     if ledger_record else "N/A"
    led_status   = ledger_record.status     if ledger_record else "N/A"
    rzp_fee      = rzp_record.raw.get("rzp_fee", 0.0)
    refund_amt   = ledger_record.raw.get("refund_amount", 0.0) if ledger_record else 0.0

    expected_settlement = round(float(rzp_record.amount) - float(rzp_fee or 0), 2)
    if refund_amt:
        expected_settlement = round(expected_settlement - float(refund_amt), 2)

    amount_diff = round(abs(expected_settlement - bank_record.amount), 2)
    lag_days    = (bank_record.date - rzp_record.date).days

    score_line = ""
    if fuzzy_scores:
        score_line = (
            f"  Fuzzy scores    : amount={fuzzy_scores.amount_score:.2f}  "
            f"date={fuzzy_scores.date_score:.2f}  "
            f"text={fuzzy_scores.text_score:.2f}  "
            f"composite={fuzzy_scores.composite:.3f}\n"
        )

    high_value = float(rzp_record.amount) >= HIGH_VALUE_REVIEW_THRESHOLD_RUPEES
    hv_note = "  ⚠ HIGH VALUE TRANSACTION — extra care required\n" if high_value else ""

    # Mask PII before sending
    customer_masked = _mask_pii(led_customer)
    notes_masked    = _mask_pii(led_notes)
    narration_masked = _mask_pii(bank_record.text_field)

    candidate_block = (
        f"  Razorpay ID     : {rzp_record.source_ref}\n"
        f"  Bank UTR        : {bank_record.source_ref}\n"
        f"  Rzp amount      : Rs.{rzp_record.amount:,.2f}  (captured {rzp_record.date})\n"
        f"  Rzp fee         : Rs.{rzp_fee:,.2f}\n"
        f"  Refund amount   : Rs.{refund_amt:,.2f}\n"
        f"  Expected settle : Rs.{expected_settlement:,.2f}\n"
        f"  Bank amount     : Rs.{bank_record.amount:,.2f}  (settled {bank_record.date})\n"
        f"  Amount diff     : Rs.{amount_diff:,.2f}  (lag {lag_days} days)\n"
        f"  Customer        : {customer_masked}\n"
        f"  Ledger notes    : {notes_masked!r}\n"
        f"  Bank narration  : {narration_masked!r}\n"
        f"  Ledger status   : {led_status}\n"
        f"{score_line}"
        f"{hv_note}"
        f"\n  record_id to use in response: {rzp_record.record_id}"
        f"\n  candidate_ids to use: [\"{rzp_record.record_id}\", \"{bank_record.record_id}\"]"
    )

    return _FEW_SHOT.format(
        brand_name            = MERCHANT_PROFILE["brand_name"],
        registered_legal_name = MERCHANT_PROFILE["registered_legal_name"],
        narration_aliases     = ", ".join(MERCHANT_PROFILE.get("narration_aliases", [])),
        candidate_block       = candidate_block,
    )


# ---------------------------------------------------------------------------
# Single-record reasoning
# ---------------------------------------------------------------------------

def reason_single(pair: FuzzyMatchPair) -> Agent4Result:
    """
    Run Agent 4 on one FuzzyMatchPair.
    Returns Agent4Result on success.
    Raises LLMError on failure — caller routes to UNRESOLVED.
    """
    prompt = _build_prompt(
        rzp_record    = pair.rzp_record,
        bank_record   = pair.bank_record,
        ledger_record = pair.ledger_record,
        fuzzy_scores  = pair.scores,
    )

    result = call_llm(
        prompt    = prompt,
        schema    = Agent4Result,
        record_id = pair.rzp_record.record_id,
        model     = GROQ_REASONING_MODEL,
    )

    logger.info(
        "Agent4: %s → decision=%s  confidence=%.2f  sem_sim=%.2f  flags=%s",
        pair.rzp_record.source_ref,
        result.decision,
        result.confidence,
        result.semantic_similarity,
        result.risk_flags,
    )
    return result


# ---------------------------------------------------------------------------
# Batch reasoning — concurrent, rate-limited
# ---------------------------------------------------------------------------

def reason_batch(
    pairs: list[FuzzyMatchPair],
) -> list[tuple[FuzzyMatchPair, Agent4Result | LLMError]]:
    """
    Run Agent 4 on a list of FuzzyMatchPairs concurrently.

    Returns list of (pair, result_or_error) in the same order as input.
    LLMError entries must be routed to UNRESOLVED by the caller.
    """
    if not pairs:
        return []

    # Build (record_id, prompt) list
    items = []
    for pair in pairs:
        prompt = _build_prompt(
            rzp_record    = pair.rzp_record,
            bank_record   = pair.bank_record,
            ledger_record = pair.ledger_record,
            fuzzy_scores  = pair.scores,
        )
        items.append((pair.rzp_record.record_id, prompt))

    raw_results = call_llm_batch(
        items      = items,
        schema     = Agent4Result,
        model      = GROQ_REASONING_MODEL,
        max_workers = 1,   # sequential — TPM limit on free tier makes concurrency unsafe
    )

    # raw_results is list of (record_id, result_or_error) in input order
    output = []
    for pair, (rid, outcome) in zip(pairs, raw_results):
        if isinstance(outcome, LLMError):
            logger.warning(
                "Agent4 failed for %s: %s — routing to UNRESOLVED",
                pair.rzp_record.source_ref, outcome,
            )
        else:
            logger.info(
                "Agent4: %s → decision=%s  confidence=%.2f  sem_sim=%.2f",
                pair.rzp_record.source_ref,
                outcome.decision,
                outcome.confidence,
                outcome.semantic_similarity,
            )
        output.append((pair, outcome))

    return output


# ---------------------------------------------------------------------------
# Smoke-test against the 13 LLM candidates from the dev set
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import time
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from agents.utils.data_loader import load_raw_data
    from agents.core.ingestion_agent import ingest
    from agents.core.exact_match_agent import run_exact_match
    from agents.utils.as_of_date import compute_as_of_date
    from agents.core.fuzzy_match_agent import run_fuzzy_match

    ledger_df, rzp_df, bank_df = load_raw_data()
    as_of = compute_as_of_date(ledger_df, rzp_df, bank_df)
    ing   = ingest(ledger_df, rzp_df, bank_df)
    exact = run_exact_match(ing.ledger_records, ing.razorpay_records, ing.bank_records)
    ledger_by_order = {r.order_id: r for r in ing.ledger_records if r.order_id}
    fuzzy = run_fuzzy_match(exact, ledger_by_order, as_of)

    candidates = fuzzy.llm_candidates
    print(f"\n=== Agent 4 smoke test — {len(candidates)} candidates ===\n")

    t0 = time.time()
    results = reason_batch(candidates)
    elapsed = time.time() - t0

    matched   = [(p, r) for p, r in results if not isinstance(r, LLMError) and r.decision == "match"]
    uncertain = [(p, r) for p, r in results if not isinstance(r, LLMError) and r.decision == "uncertain"]
    no_match  = [(p, r) for p, r in results if not isinstance(r, LLMError) and r.decision == "no_match"]
    errors    = [(p, r) for p, r in results if isinstance(r, LLMError)]

    print(f"  match={len(matched)}  uncertain={len(uncertain)}  no_match={len(no_match)}  errors={len(errors)}")
    print(f"  Total elapsed: {elapsed:.1f}s")
    print()

    for pair, res in results:
        if isinstance(res, LLMError):
            print(f"  ERROR  {pair.rzp_record.source_ref}: {res}")
            continue
        flag = " *** SEMANTIC ***" if any("semantic" in f.lower() or "brand" in f.lower()
                                          or any(kw in pair.bank_record.text_field.upper()
                                                 for kw in MERCHANT_PROFILE.get("narration_aliases", []))
                                          for f in res.risk_flags) else ""
        print(
            f"  {res.decision:10s} sem={res.semantic_similarity:.2f}  "
            f"conf={res.confidence:.2f}  "
            f"rzp={pair.rzp_record.source_ref}  "
            f"narr={pair.bank_record.text_field[:25]!r}{flag}"
        )
        reasoning_safe = res.reasoning[:100].encode("ascii", "replace").decode("ascii")
        print(f"             {reasoning_safe}")

    # Verify: semantic records should get high semantic_similarity
    sem_results = [
        (p, r) for p, r in results
        if not isinstance(r, LLMError)
        and any(kw.upper() in p.bank_record.text_field.upper()
                for kw in MERCHANT_PROFILE.get("narration_aliases", []))
    ]
    print(f"\n  Semantic brand narration results ({len(sem_results)} records):")
    for pair, res in sem_results:
        print(f"    narr={pair.bank_record.text_field!r}")
        print(f"    decision={res.decision}  sem_sim={res.semantic_similarity:.2f}  conf={res.confidence:.2f}")
        print(f"    reasoning: {res.reasoning}")

    from agents.utils.llm_provider import cache_stats
    print(f"\n  Cache: {cache_stats()}")
    print("\n=== OK ===\n")
