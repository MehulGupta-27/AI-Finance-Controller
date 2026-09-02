"""
tests/test_agent5_skip_condition.py
Guards Section 5, Agent 5's skip logic.

The skip condition requires BOTH:
  1. confidence >= SKIP_VERIFICATION_CONFIDENCE (0.95)
  2. amount < SKIP_VERIFICATION_MAX_AMOUNT (Rs.10,000)

Never skips on confidence alone. This is an easy mistake to reintroduce
(the naive, backwards version is the more "obvious" thing to write), so
it gets its own dedicated test per Section 0D.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.verifier_agent import should_skip_verification
from agents.config import SKIP_VERIFICATION_CONFIDENCE, SKIP_VERIFICATION_MAX_AMOUNT


# ---------------------------------------------------------------------------
# The four canonical cases from Section 0D exactly as specified
# ---------------------------------------------------------------------------

def test_high_confidence_high_value_still_verifies():
    """
    High value must always be verified, no matter how confident Agent 4 is.
    This is the exact case the naive confidence-band approach gets wrong.
    """
    assert should_skip_verification(confidence=0.97, amount=75_000) is False


def test_high_confidence_low_value_skips():
    """Both conditions met — this is the only case that should skip."""
    assert should_skip_verification(confidence=0.97, amount=5_000) is True


def test_moderate_confidence_low_value_still_verifies():
    """Confidence alone is not sufficient — both conditions are required."""
    assert should_skip_verification(confidence=0.80, amount=5_000) is False


def test_moderate_confidence_high_value_still_verifies():
    """Neither condition met — must verify."""
    assert should_skip_verification(confidence=0.80, amount=75_000) is False


# ---------------------------------------------------------------------------
# Boundary tests — exactly at the threshold values
# ---------------------------------------------------------------------------

def test_exactly_at_confidence_threshold_low_value():
    """confidence == SKIP_VERIFICATION_CONFIDENCE exactly should skip (>= not >)."""
    assert should_skip_verification(
        confidence=SKIP_VERIFICATION_CONFIDENCE,
        amount=SKIP_VERIFICATION_MAX_AMOUNT - 1,
    ) is True


def test_just_below_confidence_threshold():
    """Just below threshold should NOT skip."""
    assert should_skip_verification(
        confidence=SKIP_VERIFICATION_CONFIDENCE - 0.01,
        amount=1_000,
    ) is False


def test_exactly_at_amount_threshold():
    """amount == SKIP_VERIFICATION_MAX_AMOUNT exactly should NOT skip (< not <=)."""
    assert should_skip_verification(
        confidence=SKIP_VERIFICATION_CONFIDENCE,
        amount=SKIP_VERIFICATION_MAX_AMOUNT,
    ) is False


def test_just_below_amount_threshold():
    """Just below the amount threshold with high confidence — should skip."""
    assert should_skip_verification(
        confidence=SKIP_VERIFICATION_CONFIDENCE,
        amount=SKIP_VERIFICATION_MAX_AMOUNT - 0.01,
    ) is True
