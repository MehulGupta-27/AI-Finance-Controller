# agents/config.py
# Single source of truth for every tunable value in the pipeline.
# Section 6C — no magic numbers anywhere else in the codebase.
# All agents import from here; never restate a value in another file.

# ---------------------------------------------------------------------------
# Fuzzy matching (Agent 3)
# ---------------------------------------------------------------------------
FUZZY_MATCH_WEIGHTS = {"amount": 0.55, "date": 0.35, "text": 0.10}
FUZZY_AUTO_MATCH_THRESHOLD = 0.79          # score >= this → MATCHED, skip LLM
FUZZY_MIN_CANDIDATE_THRESHOLD = 0.50       # score < this → skip LLM, go to router directly
SETTLEMENT_DATE_TOLERANCE_DAYS = 10        # candidate window: captured_at + 0..10 days
OVERDUE_SETTLEMENT_DAYS = 10              # (AS_OF_DATE - captured_at).days > this → overdue
AMOUNT_TOLERANCE_RUPEES = 5.0             # bank amount within ±₹5 of predicted_settlement

# ---------------------------------------------------------------------------
# LLM / Agent 4+5 (Section 6A)
# ---------------------------------------------------------------------------
LLM_CONFIDENCE_AUTO_CONFIRM = 0.85        # combined confidence >= this → MATCHED
SKIP_VERIFICATION_CONFIDENCE = 0.95       # Agent 5 skip requires BOTH this AND low value
SKIP_VERIFICATION_MAX_AMOUNT = 10_000     # Agent 5 skip requires BOTH this AND high confidence

# Combined confidence formula: simple average of Agent 4 and Agent 5 when they agree.
# Named here so the formula driving MATCHED vs UNRESOLVED decisions is visible in one
# place (Section 6C). Used in verifier_agent.py and tested in test_agent_disagreement.py.
def combined_confidence(a4_conf: float, a5_conf: float) -> float:
    """Average of Agent 4 and Agent 5 confidence when both agree."""
    return round((a4_conf + a5_conf) / 2.0, 4)
LLM_MAX_TOKENS_PER_CALL = 700
LLM_TIMEOUT_SECONDS = 20
LLM_MAX_RETRIES = 1
LLM_RATE_LIMIT_RPM = 25                   # requests/min ceiling sent to Groq (30 is hard limit)
LLM_TPM_SLEEP_SECONDS = 4.5              # inter-call sleep to stay under 8K TPM on free tier
LLM_RETRY_BACKOFF_MIN = 5                # tenacity wait_exponential min seconds
LLM_RETRY_BACKOFF_MAX = 30               # tenacity wait_exponential max seconds
LLM_REASONING_EFFORT = "low"             # caps internal reasoning tokens for gpt-oss-20b

# ---------------------------------------------------------------------------
# Business rules (Agent 6 router)
# ---------------------------------------------------------------------------
HIGH_VALUE_REVIEW_THRESHOLD_RUPEES = 50_000   # always human-reviewed regardless of confidence
PARTIAL_REFUND_TOLERANCE_PCT = 0.50           # sanity guard: refund can't exceed 50% of amount

# ---------------------------------------------------------------------------
# Merchant identity — required context for Agent 4 AND Agent 5 (Section 3 / 6C)
# Never conditionalize on case type — pass to every prompt every call.
# For most records it's unused; for semantic_brand_narration it's the deciding fact.
# ---------------------------------------------------------------------------
MERCHANT_PROFILE = {
    "brand_name": "FitZone Gym",
    "registered_legal_name": "FitZone Wellness Private Limited",
    # Known settlement narration aliases — as they appear in bank statements.
    # Real systems store these from payment aggregator onboarding/KYC records.
    "narration_aliases": ["FITZONE", "FZW", "WELLNESS"],
}

# ---------------------------------------------------------------------------
# LLM model selection (Section 6A)
# ---------------------------------------------------------------------------
GROQ_REASONING_MODEL = "openai/gpt-oss-20b"          # Agent 4 — requires reasoning_effort="low" (see LLM_REASONING_EFFORT)
GROQ_VERIFIER_MODEL  = "openai/gpt-oss-120b"         # Agent 5 — strongest free-tier option
GROQ_QA_MODEL        = "openai/gpt-oss-20b"          # Agent 9
OLLAMA_MODEL         = "llama3.1:8b-instruct"         # dev only, never for reported numbers

# ---------------------------------------------------------------------------
# Dataset constants — imported by tests so they don't hardcode date ranges
# (Section 0D: test_as_of_date.py imports these, not duplicates them)
# ---------------------------------------------------------------------------
from datetime import date, timedelta
BASE_DATE = date(2026, 1, 1)    # dataset window starts here
DAY_SPAN = 90                   # dataset spans 90 days from BASE_DATE
