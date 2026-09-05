"""
agents/verifier_agent.py  —  Agent 5
Verifier Agent (independent second opinion).

Spec requirements (Section 5, Agent 5):
- Independent LLM call reviewing the same underlying data Agent 4 saw.
  NOT Agent 4's output or reasoning — to avoid biasing toward agreement.
- MERCHANT_PROFILE included in every prompt, identical context to Agent 4.
  Missing it would make semantic_brand_narration cases look like disagreements
  (Agent 4 matches, Agent 5 lacks the merchant-name fact → Agent 5 says uncertain
  → false agent_disagreement routed to UNRESOLVED). This is a missing-context bug,
  not a genuine disagreement — must be prevented by giving both agents identical context.
- Skip condition (BOTH required — never confidence alone):
    skip = confidence >= SKIP_VERIFICATION_CONFIDENCE (0.95)
           AND amount < SKIP_VERIFICATION_MAX_AMOUNT (₹10,000)
  High-value transactions always verified regardless of confidence.
  The naive version (skip when confidence is high) defeats the purpose of having
  a verifier — it removes the safety net exactly when overconfidence is most dangerous.
- Uses GROQ_VERIFIER_MODEL (openai/gpt-oss-120b) — same or stronger than Agent 4.
- On LLMError: treated as disagreement → routes to UNRESOLVED (safe failure).
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
    GROQ_VERIFIER_MODEL,
    SKIP_VERIFICATION_CONFIDENCE,
    SKIP_VERIFICATION_MAX_AMOUNT,
    combined_confidence,
)
from agents.core.fuzzy_match_agent import FuzzyMatchPair
from agents.core.ingestion_agent import CanonicalRecord
from agents.core.llm_reasoning_agent import Agent4Result
from agents.utils.llm_provider import call_llm, call_llm_batch, LLMError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Skip condition — Section 5, Agent 5
# Both conditions required. Never skip on confidence alone.
# ---------------------------------------------------------------------------

def should_skip_verification(confidence: float, amount: float) -> bool:
    """
    Return True only when BOTH conditions hold:
      1. confidence >= SKIP_VERIFICATION_CONFIDENCE (0.95)
      2. amount < SKIP_VERIFICATION_MAX_AMOUNT (₹10,000)

    High-value transactions are always verified regardless of confidence.
    Mid-confidence transactions are always verified regardless of amount.
    Skipping on confidence alone would remove the safety net precisely where
    an overconfident wrong match is most dangerous.
    """
    return confidence >= SKIP_VERIFICATION_CONFIDENCE and amount < SKIP_VERIFICATION_MAX_AMOUNT


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

class Agent5Result(BaseModel):
    record_id:            str
    independent_decision: str   = Field(description="match | no_match | uncertain")
    independent_confidence: float = Field(ge=0.0, le=1.0)
    agrees_with_agent_4:  bool
    verifier_notes:       str

    @field_validator("independent_confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v):
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            mapping = {"high": 0.90, "medium": 0.65, "low": 0.35,
                       "very high": 0.95, "very low": 0.20}
            return mapping.get(v.lower().strip(), 0.50)
        return float(v)


class VerificationResult(BaseModel):
    """Full result for one record through Agent 4 + Agent 5."""
    pair:           FuzzyMatchPair
    agent4_result:  Agent4Result
    agent5_result:  Optional[Agent5Result]   # None if skipped
    skipped:        bool                     # True if skip condition met
    agrees:         bool                     # True if both agree OR skipped
    combined_confidence: float               # used by Agent 6

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Prompt — identical data context to Agent 4, independent reasoning
# ---------------------------------------------------------------------------

_VERIFIER_PROMPT = """
You are an independent financial reconciliation auditor. Your job is to verify
whether a Razorpay payment and a bank settlement are the same transaction.

You have NOT seen any prior analysis. Reason from the data yourself.

Signals to consider:
- AMOUNT: expected_settlement = rzp_amount - rzp_fee - refund. A diff of Rs.0 is
  strong evidence. A diff > Rs.20 is a concern worth noting.
- DATE: Normal settlement lag is 1-3 days. Delayed but valid: 4-9 days.
  Outside 0-10 days is unusual and should lower your confidence.
- NARRATION: Generic codes (IMPS, UPI, NEFT, PG SETL) are common and carry no
  matching signal either way — neither confirming nor denying.
  If the narration contains a known merchant alias (see profile below), that IS
  a positive confirmation signal.
- NOTES: Ledger notes describe what was purchased. They should be consistent with
  the merchant's type of business.

Be calibrated: if all signals are strong, high confidence is appropriate.
If a signal is weak or missing (e.g. generic narration, long lag), reflect that
in a lower confidence rather than ignoring it. Reserve 0.95+ for cases where
all signals align clearly. 0.70-0.90 is appropriate when amount and date match
but narration provides no corroboration. Below 0.70 means genuine uncertainty.

MERCHANT PROFILE:
  Brand: {brand_name}
  Registered name: {registered_legal_name}
  Known narration aliases: {narration_aliases}

CANDIDATE PAIR:
{candidate_block}

Respond with JSON only. Fields:
  record_id (string), independent_decision (match/no_match/uncertain),
  independent_confidence (0.0-1.0), agrees_with_agent_4 (true/false — does your
  conclusion agree with decision="{agent4_decision}"?), verifier_notes (one sentence
  explaining what drove your confidence level).
""".strip()


def _build_verifier_prompt(
    rzp_record:    CanonicalRecord,
    bank_record:   CanonicalRecord,
    ledger_record: Optional[CanonicalRecord],
    agent4_decision: str,
) -> str:
    led_customer = ledger_record.text_field if ledger_record else "N/A"
    led_notes    = ledger_record.notes      if ledger_record else ""
    led_status   = ledger_record.status     if ledger_record else "N/A"
    rzp_fee      = rzp_record.raw.get("rzp_fee", 0.0)
    refund_amt   = ledger_record.raw.get("refund_amount", 0.0) if ledger_record else 0.0

    expected = round(float(rzp_record.amount) - float(rzp_fee or 0) - float(refund_amt or 0), 2)
    diff     = round(abs(expected - bank_record.amount), 2)
    lag      = (bank_record.date - rzp_record.date).days

    # Truncate to stay within token budget
    def trunc(s, n=60): return str(s)[:n] if s else ""

    candidate_block = (
        f"  Razorpay ID     : {rzp_record.source_ref}\n"
        f"  Bank UTR        : {bank_record.source_ref}\n"
        f"  Rzp amount      : Rs.{rzp_record.amount:,.2f}  (captured {rzp_record.date})\n"
        f"  Rzp fee         : Rs.{rzp_fee:,.2f}\n"
        f"  Refund amount   : Rs.{refund_amt:,.2f}\n"
        f"  Expected settle : Rs.{expected:,.2f}\n"
        f"  Bank amount     : Rs.{bank_record.amount:,.2f}  (settled {bank_record.date})\n"
        f"  Amount diff     : Rs.{diff:,.2f}  (lag {lag} days)\n"
        f"  Customer        : {trunc(led_customer)}\n"
        f"  Ledger notes    : {trunc(led_notes)!r}\n"
        f"  Bank narration  : {trunc(bank_record.text_field)!r}\n"
        f"  Ledger status   : {led_status}\n"
        f"\n  record_id: {rzp_record.record_id}"
    )

    return _VERIFIER_PROMPT.format(
        brand_name            = MERCHANT_PROFILE["brand_name"],
        registered_legal_name = MERCHANT_PROFILE["registered_legal_name"],
        narration_aliases     = ", ".join(MERCHANT_PROFILE.get("narration_aliases", [])),
        candidate_block       = candidate_block,
        agent4_decision       = agent4_decision,
    )


# ---------------------------------------------------------------------------
# Core verification logic
# ---------------------------------------------------------------------------

def verify_batch(
    pairs_and_agent4: list[tuple[FuzzyMatchPair, Agent4Result]],
) -> list[VerificationResult]:
    """
    Run Agent 5 on all pairs that don't meet the skip condition.

    Parameters
    ----------
    pairs_and_agent4 : list of (FuzzyMatchPair, Agent4Result) — only call this
                       for pairs where Agent 4 succeeded (no LLMError).

    Returns
    -------
    list of VerificationResult in same order as input.
    """
    results = []
    llm_items: list[tuple[int, str, str]] = []  # (original_idx, record_id, prompt)

    # First pass: determine which need verification
    for idx, (pair, a4) in enumerate(pairs_and_agent4):
        amount = float(pair.rzp_record.amount)
        if should_skip_verification(a4.confidence, amount):
            # Skip — treat as agreement at Agent 4's confidence
            logger.info(
                "Agent5 SKIP: %s  conf=%.2f  amount=%.0f  (both conditions met)",
                pair.rzp_record.source_ref, a4.confidence, amount,
            )
            results.append(VerificationResult(
                pair                 = pair,
                agent4_result        = a4,
                agent5_result        = None,
                skipped              = True,
                agrees               = True,
                combined_confidence  = a4.confidence,
            ))
        else:
            prompt = _build_verifier_prompt(
                rzp_record      = pair.rzp_record,
                bank_record     = pair.bank_record,
                ledger_record   = pair.ledger_record,
                agent4_decision = a4.decision,
            )
            llm_items.append((idx, pair.rzp_record.record_id, prompt))
            results.append(None)  # placeholder

    # Second pass: run LLM for non-skipped records
    if llm_items:
        idxs      = [i for i, _, _ in llm_items]
        llm_input = [(rid, prompt) for _, rid, prompt in llm_items]

        raw = call_llm_batch(
            items       = llm_input,
            schema      = Agent5Result,
            model       = GROQ_VERIFIER_MODEL,
            max_workers = 1,  # sequential — TPM constraint
        )

        for (orig_idx, _, _), (rid, outcome) in zip(llm_items, raw):
            pair, a4 = pairs_and_agent4[orig_idx]

            if isinstance(outcome, LLMError):
                # Treat LLM failure as disagreement — safe default
                logger.warning(
                    "Agent5 failed for %s: %s — treating as disagreement (UNRESOLVED)",
                    pair.rzp_record.source_ref, outcome,
                )
                results[orig_idx] = VerificationResult(
                    pair                = pair,
                    agent4_result       = a4,
                    agent5_result       = None,
                    skipped             = False,
                    agrees              = False,
                    combined_confidence = 0.0,
                )
            else:
                agrees = (outcome.independent_decision == a4.decision)
                # Combined confidence: named formula from config.combined_confidence()
                # Returns 0.0 on disagreement — disagreement always routes to UNRESOLVED
                combined = (
                    combined_confidence(a4.confidence, outcome.independent_confidence)
                    if agrees else 0.0
                )
                logger.info(
                    "Agent5: %s → %s  conf=%.2f  agrees=%s  combined=%.2f",
                    pair.rzp_record.source_ref,
                    outcome.independent_decision,
                    outcome.independent_confidence,
                    agrees,
                    combined,
                )
                results[orig_idx] = VerificationResult(
                    pair                = pair,
                    agent4_result       = a4,
                    agent5_result       = outcome,
                    skipped             = False,
                    agrees              = agrees,
                    combined_confidence = combined,
                )

    return results


# ---------------------------------------------------------------------------
# Smoke-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import time
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s %(message)s")

    from agents.utils.data_loader import load_raw_data
    from agents.core.ingestion_agent import ingest
    from agents.core.exact_match_agent import run_exact_match
    from agents.utils.as_of_date import compute_as_of_date
    from agents.core.fuzzy_match_agent import run_fuzzy_match
    from agents.core.llm_reasoning_agent import reason_batch

    ledger_df, rzp_df, bank_df = load_raw_data()
    as_of  = compute_as_of_date(ledger_df, rzp_df, bank_df)
    ing    = ingest(ledger_df, rzp_df, bank_df)
    exact  = run_exact_match(ing.ledger_records, ing.razorpay_records, ing.bank_records)
    ledger_by_order = {r.order_id: r for r in ing.ledger_records if r.order_id}
    fuzzy  = run_fuzzy_match(exact, ledger_by_order, as_of)

    # Agent 4 (uses cached results from previous run)
    a4_results = reason_batch(fuzzy.llm_candidates)

    # Filter to successful Agent 4 results
    valid = [(pair, res) for pair, res in a4_results if not isinstance(res, LLMError)]
    print(f"\n=== Agent 5 smoke test — {len(valid)} candidates ===\n")

    t0 = time.time()
    v_results = verify_batch(valid)
    elapsed = time.time() - t0

    skipped    = [r for r in v_results if r.skipped]
    agrees_all = [r for r in v_results if r.agrees]
    disagrees  = [r for r in v_results if not r.agrees]

    print(f"  Skipped (both conditions met) : {len(skipped)}")
    print(f"  Verified + agree              : {len(agrees_all) - len(skipped)}")
    print(f"  Disagree → UNRESOLVED         : {len(disagrees)}")
    print(f"  Total elapsed                 : {elapsed:.1f}s")
    print()

    for r in v_results:
        a4_dec  = r.agent4_result.decision
        a4_conf = r.agent4_result.confidence
        rzp_ref = r.pair.rzp_record.source_ref
        amt     = r.pair.rzp_record.amount
        if r.skipped:
            print(f"  SKIP  {rzp_ref}  a4={a4_dec}({a4_conf:.2f})  amt=Rs.{amt:.0f}  combined={r.combined_confidence:.2f}")
        elif r.agent5_result:
            a5_dec  = r.agent5_result.independent_decision
            a5_conf = r.agent5_result.independent_confidence
            agree_str = "AGREE" if r.agrees else "DISAGREE"
            notes_safe = r.agent5_result.verifier_notes[:70].encode('ascii','replace').decode('ascii')
            print(f"  {agree_str:8s} {rzp_ref}  a4={a4_dec}({a4_conf:.2f})  a5={a5_dec}({a5_conf:.2f})  combined={r.combined_confidence:.2f}")
            print(f"           notes: {notes_safe}")
        else:
            print(f"  LLM_FAIL {rzp_ref}  → UNRESOLVED")

    # Verify skip condition logic explicitly
    print("\n--- Skip condition unit checks ---")
    assert should_skip_verification(0.97, 5_000)  is True,  "high conf + low value should skip"
    assert should_skip_verification(0.97, 75_000) is False, "high conf + HIGH value must NOT skip"
    assert should_skip_verification(0.80, 5_000)  is False, "low conf must NOT skip"
    assert should_skip_verification(0.80, 75_000) is False, "low conf + high value must NOT skip"
    print("  ✓ should_skip_verification(0.97, 5_000)  = True")
    print("  ✓ should_skip_verification(0.97, 75_000) = False  (high value always verified)")
    print("  ✓ should_skip_verification(0.80, 5_000)  = False  (confidence alone not sufficient)")
    print("  ✓ should_skip_verification(0.80, 75_000) = False")
    print("\n=== OK ===\n")
