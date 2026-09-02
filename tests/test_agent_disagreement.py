"""
tests/test_agent_disagreement.py
Guards the agent_disagreement routing path in Agent 6 (router.py).

This path has never been triggered by real data (all 13 live candidates agreed),
so it needs explicit synthetic coverage. Tests:
1. A4=match, A5=no_match  → UNRESOLVED / agent_disagreement
2. A4=match, A5=uncertain → UNRESOLVED / agent_disagreement
3. A4=no_match, A5=match  → UNRESOLVED / agent_disagreement
4. A4=match, A5=match     → MATCHED (agreement baseline — not disagreement)
5. A4=match, A5=match but combined_confidence < threshold → UNRESOLVED / low_confidence
6. combined_confidence() formula is symmetric and correct
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.config import (
    BASE_DATE, DAY_SPAN, LLM_CONFIDENCE_AUTO_CONFIRM,
    SKIP_VERIFICATION_CONFIDENCE, SKIP_VERIFICATION_MAX_AMOUNT,
    combined_confidence,
)
from agents.ingestion_agent import CanonicalRecord
from agents.fuzzy_match_agent import FuzzyMatchPair, FuzzyScores
from agents.llm_reasoning_agent import Agent4Result
from agents.verifier_agent import Agent5Result, VerificationResult
from agents.router import route_llm_result

FIXED_AS_OF = BASE_DATE + timedelta(days=DAY_SPAN)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_rzp(record_id="rzp-test", amount=5000.0):
    return CanonicalRecord(
        record_id=record_id, source="razorpay", source_ref="pay_test001",
        order_id="ORD_TEST", amount=amount,
        date=FIXED_AS_OF - timedelta(days=2),
        text_field="card", notes="", status="captured",
        raw={"rzp_fee": 118.0, "refund_amount": 0.0},
    )


def _make_bank(record_id="bank-test", amount=4882.0):
    return CanonicalRecord(
        record_id=record_id, source="bank", source_ref="UTR_TEST001",
        order_id=None, amount=amount,
        date=FIXED_AS_OF - timedelta(days=1),
        text_field="IMPS 999", notes="", status="NEFT", raw={},
    )


def _make_ledger(record_id="led-test", amount=5000.0):
    return CanonicalRecord(
        record_id=record_id, source="ledger", source_ref="LED_TEST001",
        order_id="ORD_TEST", amount=amount,
        date=FIXED_AS_OF - timedelta(days=3),
        text_field="Test Customer", notes="", status="paid",
        raw={"refund_amount": 0.0, "status": "paid"},
    )


def _make_pair(rzp_amount=5000.0):
    rzp = _make_rzp(amount=rzp_amount)
    return FuzzyMatchPair(
        rzp_record=rzp,
        bank_record=_make_bank(),
        predicted_settlement=4882.0,
        scores=FuzzyScores(amount_score=1.0, date_score=0.90, text_score=0.0, composite=0.865),
        ledger_record=_make_ledger(),
        refund_amount=0.0,
    )


def _make_a4(decision="match", confidence=0.90, sem_sim=0.75):
    return Agent4Result(
        record_id="rzp-test",
        candidate_ids=["rzp-test", "bank-test"],
        semantic_similarity=sem_sim,
        decision=decision,
        confidence=confidence,
        reasoning="Test reasoning.",
        risk_flags=[],
    )


def _make_a5(decision="match", confidence=0.85):
    return Agent5Result(
        record_id="rzp-test",
        independent_decision=decision,
        independent_confidence=confidence,
        agrees_with_agent_4=(decision == "match"),
        verifier_notes="Test verifier note.",
    )


def _make_vr(a4_decision="match", a4_conf=0.90,
             a5_decision="match", a5_conf=0.85,
             skipped=False, amount=5000.0):
    """Build a VerificationResult with the given A4/A5 decisions."""
    pair = _make_pair(rzp_amount=amount)
    a4   = _make_a4(decision=a4_decision, confidence=a4_conf)
    if skipped:
        return VerificationResult(
            pair=pair, agent4_result=a4, agent5_result=None,
            skipped=True, agrees=True, combined_confidence=a4_conf,
        )
    a5     = _make_a5(decision=a5_decision, confidence=a5_conf)
    agrees = (a5_decision == a4_decision)
    comb   = combined_confidence(a4_conf, a5_conf) if agrees else 0.0
    return VerificationResult(
        pair=pair, agent4_result=a4, agent5_result=a5,
        skipped=False, agrees=agrees, combined_confidence=comb,
    )


# ---------------------------------------------------------------------------
# Test 1-3: disagreement cases → UNRESOLVED / agent_disagreement
# ---------------------------------------------------------------------------

def test_a4_match_a5_no_match_routes_to_agent_disagreement():
    """A4 says match, A5 says no_match — classic disagreement."""
    vr = _make_vr(a4_decision="match", a5_decision="no_match")
    result = route_llm_result(vr, FIXED_AS_OF)

    assert result.status == "UNRESOLVED"
    assert result.sub_reason == "agent_disagreement"
    assert result.confidence == 0.0
    assert "Agent 4" in result.explanation.checklist[0].label
    assert "Agent 5" in result.explanation.checklist[1].label
    assert not result.explanation.checklist[0].passed
    assert not result.explanation.checklist[1].passed


def test_a4_match_a5_uncertain_routes_to_agent_disagreement():
    """A4 says match, A5 says uncertain — still a disagreement."""
    vr = _make_vr(a4_decision="match", a5_decision="uncertain")
    result = route_llm_result(vr, FIXED_AS_OF)

    assert result.status == "UNRESOLVED"
    assert result.sub_reason == "agent_disagreement"
    assert result.confidence == 0.0


def test_a4_no_match_a5_match_routes_to_agent_disagreement():
    """Reversed disagreement — A4 no_match, A5 match."""
    vr = _make_vr(a4_decision="no_match", a5_decision="match")
    result = route_llm_result(vr, FIXED_AS_OF)

    assert result.status == "UNRESOLVED"
    assert result.sub_reason == "agent_disagreement"


# ---------------------------------------------------------------------------
# Test 4: agreement with sufficient confidence → MATCHED
# ---------------------------------------------------------------------------

def test_a4_match_a5_match_routes_to_matched():
    """Both agree at match — combined above threshold → MATCHED."""
    # combined = (0.90 + 0.85) / 2 = 0.875 > 0.85 threshold
    vr = _make_vr(a4_decision="match", a4_conf=0.90,
                  a5_decision="match", a5_conf=0.85)
    result = route_llm_result(vr, FIXED_AS_OF)

    assert result.status == "MATCHED"
    assert result.sub_reason is None
    assert result.confidence == pytest.approx(0.875, abs=0.001)


# ---------------------------------------------------------------------------
# Test 5: agreement but combined < threshold → UNRESOLVED / low_confidence
# ---------------------------------------------------------------------------

def test_agreement_but_low_combined_confidence_routes_to_low_confidence():
    """Both say match but individual confidences are low — combined below 0.85."""
    # combined = (0.75 + 0.80) / 2 = 0.775 < 0.85 threshold
    vr = _make_vr(a4_decision="match", a4_conf=0.75,
                  a5_decision="match", a5_conf=0.80)
    result = route_llm_result(vr, FIXED_AS_OF)

    assert result.status == "UNRESOLVED"
    assert result.sub_reason == "low_confidence"
    assert result.confidence == pytest.approx(0.775, abs=0.001)


# ---------------------------------------------------------------------------
# Test 6: combined_confidence() formula is correct and symmetric
# ---------------------------------------------------------------------------

def test_combined_confidence_formula():
    """Verify the named formula from config is (a4 + a5) / 2, symmetric."""
    assert combined_confidence(0.90, 0.80) == pytest.approx(0.85, abs=0.001)
    assert combined_confidence(0.80, 0.90) == pytest.approx(0.85, abs=0.001)  # symmetric
    assert combined_confidence(0.95, 0.95) == pytest.approx(0.95, abs=0.001)
    assert combined_confidence(0.70, 0.70) == pytest.approx(0.70, abs=0.001)
    # At exactly the threshold
    assert combined_confidence(0.85, 0.85) == pytest.approx(0.85, abs=0.001)


# ---------------------------------------------------------------------------
# Test 7: high-value gate overrides agreement (already in router, belt-and-suspenders)
# ---------------------------------------------------------------------------

def test_high_value_overrides_agreement():
    """Even when A4 and A5 both agree match, high value forces UNRESOLVED."""
    vr = _make_vr(a4_decision="match", a4_conf=0.92,
                  a5_decision="match", a5_conf=0.95,
                  amount=75_000.0)   # above HIGH_VALUE_REVIEW_THRESHOLD
    result = route_llm_result(vr, FIXED_AS_OF)

    assert result.status == "UNRESOLVED"
    assert result.sub_reason == "high_value_review_required"


# ---------------------------------------------------------------------------
# Test 8: explanation object is fully populated for agent_disagreement
# ---------------------------------------------------------------------------

def test_agent_disagreement_explanation_is_complete():
    """Explanation must show both agents' decisions — used in the review queue UI."""
    vr = _make_vr(a4_decision="match", a4_conf=0.88,
                  a5_decision="no_match", a5_conf=0.82)
    result = route_llm_result(vr, FIXED_AS_OF)

    exp = result.explanation
    assert exp.headline == "UNRESOLVED — AI reasoning conflict"
    assert exp.recommendation is not None and len(exp.recommendation) > 0
    assert len(exp.checklist) == 2
    # Both checklist items should show the respective agent's output
    labels = " ".join(item.label for item in exp.checklist)
    assert "match" in labels.lower()
    assert "no_match" in labels.lower() or "no match" in labels.lower()
    assert "agent_disagreement" in exp.risk_flags
