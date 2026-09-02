"""
agents/router.py  —  Agent 6
Confidence Router — the single, explicit, deterministic policy table.

Section 5/6B: Routes every record to exactly one of MATCHED / PARTIAL / UNRESOLVED.
Never binary. The routing table is written as literal readable conditional logic,
not a scoring function. Every AS_OF_DATE comparison uses the fixed value from Agent 0.

Also assembles the structured explanation object (Section 6D) for every record
using deterministic string templates — no additional LLM call.
"""

import logging
import sys
from datetime import date
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.config import (
    FUZZY_AUTO_MATCH_THRESHOLD,
    LLM_CONFIDENCE_AUTO_CONFIRM,
    OVERDUE_SETTLEMENT_DAYS,
    HIGH_VALUE_REVIEW_THRESHOLD_RUPEES,
    SKIP_VERIFICATION_CONFIDENCE,
    SKIP_VERIFICATION_MAX_AMOUNT,
)
from agents.exact_match_agent import ExactMatchPair, ExactMatchResult
from agents.fuzzy_match_agent import FuzzyMatchPair, FuzzyMatchResult
from agents.ingestion_agent import CanonicalRecord
from agents.llm_reasoning_agent import Agent4Result
from agents.verifier_agent import VerificationResult, should_skip_verification

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------

class ChecklistItem(BaseModel):
    passed: bool
    label:  str


class Explanation(BaseModel):
    headline:       str
    checklist:      list[ChecklistItem]
    risk_flags:     list[str]
    days_elapsed:   Optional[int]    # None unless time-based reasoning applies
    recommendation: Optional[str]   # None for MATCHED
    confidence:     float


class RouteResult(BaseModel):
    """
    Final routing decision for one logical transaction (one case).
    status is always one of: MATCHED / PARTIAL / UNRESOLVED — never a boolean.
    """
    # Primary record identifier — the Rzp record_id for LLM cases,
    # the ledger record_id for exact-match / direct cases
    record_id:   str
    status:      str             # MATCHED | PARTIAL | UNRESOLVED
    sub_reason:  Optional[str]   # populated for PARTIAL and UNRESOLVED
    confidence:  float
    explanation: Explanation
    source:      str             # "exact" | "fuzzy" | "llm" | "direct"

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Explanation builders (Section 6D) — deterministic templates, no LLM
# ---------------------------------------------------------------------------

def _conf_pct(c: float) -> str:
    return f"{int(round(c * 100))}%"


def _explain_exact(pair: ExactMatchPair) -> Explanation:
    """MATCHED via exact order_id key."""
    sub = pair.sub_reason or ""
    if sub == "no_action_needed":
        return Explanation(
            headline       = "MATCHED — No action needed",
            checklist      = [
                ChecklistItem(passed=True,  label="Payment attempt failed — no downstream record was ever expected"),
                ChecklistItem(passed=True,  label=f"Order ID: {pair.order_id} confirmed in ledger and Razorpay"),
            ],
            risk_flags     = [],
            days_elapsed   = None,
            recommendation = None,
            confidence     = 1.0,
        )
    return Explanation(
        headline       = "MATCHED — 100% confidence",
        checklist      = [
            ChecklistItem(passed=True, label=f"Order ID matches exactly: {pair.order_id}"),
            ChecklistItem(passed=True, label=f"Amount: Rs.{pair.ledger_record.amount:,.2f} confirmed in ledger and Razorpay"),
        ],
        risk_flags     = [],
        days_elapsed   = None,
        recommendation = None,
        confidence     = 1.0,
    )


def _explain_fuzzy(pair: FuzzyMatchPair) -> Explanation:
    """MATCHED via fuzzy scoring >= auto-match threshold."""
    s = pair.scores
    conf = s.composite
    amt_ok  = s.amount_score >= 0.99
    date_ok = s.date_score  >= 0.70
    text_ok = s.text_score  >= 0.30
    refund_note = f" (after Rs.{pair.refund_amount:,.2f} refund)" if pair.refund_amount > 0 else ""

    checklist = [
        ChecklistItem(passed=amt_ok,
            label=f"Amount matches{refund_note}: Rs.{pair.predicted_settlement:,.2f} vs Rs.{pair.bank_record.amount:,.2f}"
                  + (f" (diff Rs.{abs(pair.predicted_settlement - pair.bank_record.amount):.2f})" if not amt_ok else "")),
        ChecklistItem(passed=date_ok,
            label=f"Settlement date: {(pair.bank_record.date - pair.rzp_record.date).days}-day lag"),
        ChecklistItem(passed=text_ok,
            label=f"Text similarity: {_conf_pct(s.text_score)} (narration: {pair.bank_record.text_field[:30]!r})"),
    ]
    return Explanation(
        headline       = f"MATCHED — {_conf_pct(conf)} confidence",
        checklist      = checklist,
        risk_flags     = [],
        days_elapsed   = None,
        recommendation = None,
        confidence     = conf,
    )


def _explain_llm(vr: VerificationResult) -> Explanation:
    """MATCHED via Agent 4 + 5 LLM reasoning."""
    a4   = vr.agent4_result
    conf = vr.combined_confidence
    pair = vr.pair

    checklist = [
        ChecklistItem(
            passed = a4.semantic_similarity >= 0.50,
            label  = f"Semantic similarity: {_conf_pct(a4.semantic_similarity)}",
        ),
        ChecklistItem(
            passed = True,
            label  = f"Agent 4 reasoning: {a4.reasoning[:80]}",
        ),
    ]
    if vr.agent5_result and not vr.skipped:
        checklist.append(ChecklistItem(
            passed = vr.agrees,
            label  = f"Agent 5 independent verification: {vr.agent5_result.independent_decision} ({_conf_pct(vr.agent5_result.independent_confidence)})",
        ))
    elif vr.skipped:
        checklist.append(ChecklistItem(
            passed = True,
            label  = f"Verification skipped — conf≥{_conf_pct(SKIP_VERIFICATION_CONFIDENCE)} and amount<Rs.{SKIP_VERIFICATION_MAX_AMOUNT:,}",
        ))

    flags = list(a4.risk_flags)
    return Explanation(
        headline       = f"MATCHED — {_conf_pct(conf)} confidence",
        checklist      = checklist,
        risk_flags     = flags,
        days_elapsed   = None,
        recommendation = None,
        confidence     = conf,
    )


def _explain_partial_awaiting(rzp: CanonicalRecord, days: int) -> Explanation:
    return Explanation(
        headline       = f"PARTIAL — Awaiting settlement ({days} days)",
        checklist      = [
            ChecklistItem(passed=True,  label="Ledger confirms order"),
            ChecklistItem(passed=True,  label="Gateway (Razorpay) confirms payment captured"),
            ChecklistItem(passed=False, label=f"Bank settlement pending ({days}/{OVERDUE_SETTLEMENT_DAYS} days elapsed)"),
        ],
        risk_flags     = [],
        days_elapsed   = days,
        recommendation = "No action needed yet — will auto-resolve on next pipeline run once bank settles",
        confidence     = 0.90,
    )


def _explain_partial_no_ledger(rzp: CanonicalRecord, bank: CanonicalRecord) -> Explanation:
    return Explanation(
        headline       = f"PARTIAL — No ledger record",
        checklist      = [
            ChecklistItem(passed=True,  label=f"Gateway confirms capture: Rs.{rzp.amount:,.2f}"),
            ChecklistItem(passed=True,  label=f"Bank confirms settlement: Rs.{bank.amount:,.2f}"),
            ChecklistItem(passed=False, label="No matching ledger entry found"),
        ],
        risk_flags     = [],
        days_elapsed   = None,
        recommendation = "Flag for ops: check integration/webhook logs",
        confidence     = 0.85,
    )


def _explain_unresolved(sub: str, **ctx) -> Explanation:
    if sub == "overdue_settlement":
        days = ctx.get("days", 0)
        return Explanation(
            headline       = f"UNRESOLVED — Settlement overdue ({days} days)",
            checklist      = [
                ChecklistItem(passed=True,  label="Ledger confirms order"),
                ChecklistItem(passed=True,  label="Gateway confirms capture"),
                ChecklistItem(passed=False, label=f"Bank settlement overdue by {days} days (threshold: {OVERDUE_SETTLEMENT_DAYS})"),
            ],
            risk_flags     = [],
            days_elapsed   = days,
            recommendation = "Contact bank/gateway regarding the delayed settlement",
            confidence     = 0.0,
        )
    if sub == "agent_disagreement":
        a4_dec  = ctx.get("a4_decision", "?")
        a4_conf = ctx.get("a4_confidence", 0.0)
        a5_dec  = ctx.get("a5_decision", "?")
        a5_conf = ctx.get("a5_confidence", 0.0)
        return Explanation(
            headline       = "UNRESOLVED — AI reasoning conflict",
            checklist      = [
                ChecklistItem(passed=False, label=f"Agent 4: {a4_dec} ({_conf_pct(a4_conf)})"),
                ChecklistItem(passed=False, label=f"Agent 5: {a5_dec} ({_conf_pct(a5_conf)})"),
            ],
            risk_flags     = ["agent_disagreement"],
            days_elapsed   = None,
            recommendation = "Human review required — the two independent reasoning passes disagreed",
            confidence     = 0.0,
        )
    if sub == "low_confidence":
        conf    = ctx.get("confidence", 0.0)
        reason  = ctx.get("reasoning", "")
        return Explanation(
            headline       = f"UNRESOLVED — {_conf_pct(conf)} confidence",
            checklist      = [
                ChecklistItem(passed=False, label=f"Combined confidence {_conf_pct(conf)} below threshold {_conf_pct(LLM_CONFIDENCE_AUTO_CONFIRM)}"),
                ChecklistItem(passed=False, label=f"Agent 4 reasoning: {reason[:80]}"),
            ],
            risk_flags     = [],
            days_elapsed   = None,
            recommendation = "Review and confirm or reject the suggested match",
            confidence     = conf,
        )
    if sub == "high_value_review_required":
        amt  = ctx.get("amount", 0.0)
        conf = ctx.get("confidence", 0.0)
        return Explanation(
            headline       = f"UNRESOLVED — High value (Rs.{amt:,.2f})",
            checklist      = [
                ChecklistItem(passed=True,  label=f"Match confidence: {_conf_pct(conf)}"),
                ChecklistItem(passed=False, label=f"Exceeds Rs.{HIGH_VALUE_REVIEW_THRESHOLD_RUPEES:,} mandatory review threshold"),
            ],
            risk_flags     = ["high_value"],
            days_elapsed   = None,
            recommendation = "Mandatory sign-off required regardless of match confidence",
            confidence     = conf,
        )
    if sub == "unidentified_bank_credit":
        amt  = ctx.get("amount", 0.0)
        dt   = ctx.get("date", "")
        narr = ctx.get("narration", "")
        return Explanation(
            headline       = "UNRESOLVED — Unidentified credit",
            checklist      = [
                ChecklistItem(passed=False, label="No matching ledger entry"),
                ChecklistItem(passed=False, label="No matching gateway record"),
                ChecklistItem(passed=True,  label=f"Amount Rs.{amt:,.2f} on {dt} narration: {narr!r}"),
            ],
            risk_flags     = ["unidentified_credit"],
            days_elapsed   = None,
            recommendation = "Identify the source of this credit",
            confidence     = 0.0,
        )
    # no_candidates_found or catch-all
    return Explanation(
        headline       = "UNRESOLVED — No match found",
        checklist      = [ChecklistItem(passed=False, label="No plausible candidates in any source")],
        risk_flags     = [],
        days_elapsed   = None,
        recommendation = "Investigate manually — no automated signal to go on",
        confidence     = 0.0,
    )


# ---------------------------------------------------------------------------
# Routing functions — one per entry point in the pipeline
# ---------------------------------------------------------------------------

def route_exact_match(pair: ExactMatchPair) -> RouteResult:
    """
    Route an exact-matched Ledger↔Razorpay pair.
    If ledger status == 'failed' → MATCHED / no_action_needed.
    Otherwise → MATCHED (bank matching happens in fuzzy stage, not here).
    """
    sub = pair.sub_reason
    return RouteResult(
        record_id   = pair.ledger_record.record_id,
        status      = "MATCHED",
        sub_reason  = sub,
        confidence  = 1.0,
        explanation = _explain_exact(pair),
        source      = "exact",
    )


def route_fuzzy_auto_match(
    pair:       FuzzyMatchPair,
    as_of_date: date,
) -> RouteResult:
    """
    Route a fuzzy-auto-matched Rzp↔Bank pair (score >= FUZZY_AUTO_MATCH_THRESHOLD).
    High-value gate still applies even here.
    """
    amount = pair.rzp_record.amount
    conf   = pair.scores.composite
    rid    = pair.rzp_record.record_id

    # High-value gate — mandatory regardless of match confidence
    if amount >= HIGH_VALUE_REVIEW_THRESHOLD_RUPEES:
        return RouteResult(
            record_id   = rid,
            status      = "UNRESOLVED",
            sub_reason  = "high_value_review_required",
            confidence  = conf,
            explanation = _explain_unresolved("high_value_review_required", amount=amount, confidence=conf),
            source      = "fuzzy",
        )

    return RouteResult(
        record_id   = rid,
        status      = "MATCHED",
        sub_reason  = None,
        confidence  = conf,
        explanation = _explain_fuzzy(pair),
        source      = "fuzzy",
    )


def route_llm_result(
    vr:         VerificationResult,
    as_of_date: date,
) -> RouteResult:
    """
    Route a record that went through Agent 4 (+/- Agent 5).
    Routing table per Section 6B — written as explicit readable conditionals.
    """
    a4     = vr.agent4_result
    pair   = vr.pair
    amount = float(pair.rzp_record.amount)
    rid    = pair.rzp_record.record_id

    # High-value gate — always first, no exceptions
    if amount >= HIGH_VALUE_REVIEW_THRESHOLD_RUPEES:
        return RouteResult(
            record_id   = rid,
            status      = "UNRESOLVED",
            sub_reason  = "high_value_review_required",
            confidence  = vr.combined_confidence,
            explanation = _explain_unresolved("high_value_review_required",
                                               amount=amount, confidence=vr.combined_confidence),
            source      = "llm",
        )

    # Agent 4/5 disagreement — always UNRESOLVED
    if not vr.agrees and not vr.skipped:
        a5_dec  = vr.agent5_result.independent_decision if vr.agent5_result else "error"
        a5_conf = vr.agent5_result.independent_confidence if vr.agent5_result else 0.0
        return RouteResult(
            record_id   = rid,
            status      = "UNRESOLVED",
            sub_reason  = "agent_disagreement",
            confidence  = 0.0,
            explanation = _explain_unresolved("agent_disagreement",
                                               a4_decision=a4.decision, a4_confidence=a4.confidence,
                                               a5_decision=a5_dec,      a5_confidence=a5_conf),
            source      = "llm",
        )

    # Low combined confidence
    if vr.combined_confidence < LLM_CONFIDENCE_AUTO_CONFIRM:
        return RouteResult(
            record_id   = rid,
            status      = "UNRESOLVED",
            sub_reason  = "low_confidence",
            confidence  = vr.combined_confidence,
            explanation = _explain_unresolved("low_confidence",
                                               confidence=vr.combined_confidence,
                                               reasoning=a4.reasoning),
            source      = "llm",
        )

    # Agent 4 says match (or skip-verified), confidence >= threshold → MATCHED
    return RouteResult(
        record_id   = rid,
        status      = "MATCHED",
        sub_reason  = None,
        confidence  = vr.combined_confidence,
        explanation = _explain_llm(vr),
        source      = "llm",
    )


def route_pending_settlement(
    rzp_record:  CanonicalRecord,
    as_of_date:  date,
) -> RouteResult:
    """
    Razorpay row with no bank candidate — either awaiting or overdue.
    Uses AS_OF_DATE (never datetime.now()) for elapsed calculation.
    """
    days = (as_of_date - rzp_record.date).days
    rid  = rzp_record.record_id

    if days > OVERDUE_SETTLEMENT_DAYS:
        return RouteResult(
            record_id   = rid,
            status      = "UNRESOLVED",
            sub_reason  = "overdue_settlement",
            confidence  = 0.0,
            explanation = _explain_unresolved("overdue_settlement", days=days),
            source      = "direct",
        )
    else:
        return RouteResult(
            record_id   = rid,
            status      = "PARTIAL",
            sub_reason  = "awaiting_settlement",
            confidence  = 0.90,
            explanation = _explain_partial_awaiting(rzp_record, days),
            source      = "direct",
        )


def route_missing_ledger(
    pair:       FuzzyMatchPair,
    as_of_date: date,
) -> RouteResult:
    """Rzp+Bank matched but no ledger row → PARTIAL / no_ledger_record."""
    return RouteResult(
        record_id   = pair.rzp_record.record_id,
        status      = "PARTIAL",
        sub_reason  = "no_ledger_record",
        confidence  = pair.scores.composite,
        explanation = _explain_partial_no_ledger(pair.rzp_record, pair.bank_record),
        source      = "fuzzy",
    )


def route_unidentified_bank_credit(bank_record: CanonicalRecord) -> RouteResult:
    """Standalone bank row with no plausible Rzp candidate anywhere."""
    return RouteResult(
        record_id   = bank_record.record_id,
        status      = "UNRESOLVED",
        sub_reason  = "unidentified_bank_credit",
        confidence  = 0.0,
        explanation = _explain_unresolved(
            "unidentified_bank_credit",
            amount    = bank_record.amount,
            date      = str(bank_record.date),
            narration = bank_record.text_field,
        ),
        source      = "direct",
    )
