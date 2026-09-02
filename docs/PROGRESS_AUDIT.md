# AI Finance Controller — Progress Audit
**Updated:** September 2026
**Dataset:** 110-record dev set (`data/raw_100/`)

---

## Build step status (Section 11 order)

| Step | Description | Status |
|------|-------------|--------|
| 1 | Environment setup | ✅ Complete |
| 2 | `agents/config.py` — all thresholds | ✅ Complete |
| 3 | 110-record dataset (`data/raw_100/`) | ✅ Complete |
| 4 | `agents/data_loader.py` | ✅ Complete |
| 5 | AS_OF_DATE computation + test | ✅ Complete |
| 6 | Agent 1 — ingestion + Pydantic validation | ✅ Complete |
| 7 | Agent 2 — exact match | ✅ Complete |
| 8 | Agent 8 stub — reporting + record invariant test | ✅ Complete |
| 9 | Agent 3 — fuzzy match | ✅ Complete |
| 10 | `call_llm()` wrapper — caching + rate limiter | ✅ Complete |
| 11 | Agent 4 — LLM reasoning | ✅ Complete |
| 12 | Agent 5 — verifier | ✅ Complete |
| 13 | Agent 6 — router + three-state tests + skip condition tests | ✅ Complete |
| 14 | Agent 7 — audit logger | ✅ Complete |
| 15 | Human review queue UI + dashboard | ✅ Complete |
| 16 | Agent 9 — Q&A agent | ✅ Complete |
| 17 | Scale to 550-record full dataset | ⬜ Not started |
| 18 | Dev-set threshold tuning | ⬜ Not started |
| 19 | Final held-out test run | ⬜ Not started |

---

## Test suite status

**28 tests, all passing** (`pytest tests -q`)

| File | Tests | Guards |
|---|---|---|
| `test_as_of_date.py` | 2 | AS_OF_DATE never uses wall clock (Section 0C.2) |
| `test_record_count_invariant.py` | 4 | No record silently dropped or duplicated (Section 0C.3) |
| `test_three_state_output.py` | 6 | Status always MATCHED/PARTIAL/UNRESOLVED, never boolean (Section 0C.1) |
| `test_agent5_skip_condition.py` | 8 | Skip requires BOTH high confidence AND low amount — never confidence alone |
| `test_agent_disagreement.py` | 8 | Agent disagreement correctly routes to UNRESOLVED; combined_confidence formula verified |

---

## Pipeline results on 110-record dataset

```
Records processed  : 110
Reconciled         : 97  (88.2%)
In Progress        : 8
Needs Review       : 5
Processing time    : ~83s (first run, live LLM calls)
                   : ~1s  (subsequent runs, from LLM cache)

Sub-reasons:
  no_action_needed         5   failed_payment_orphan
  awaiting_settlement      5   pending_settlement
  unidentified_bank_credit 5   unidentified_bank_credit
  no_ledger_record         3   missing_from_ledger
```

**Known issue — Record 5 (delayed_settlement, 8-day lag):**
A4 confidence 0.80, A5 confidence 0.88, combined = 0.84.
Threshold is 0.85 → routes to UNRESOLVED/low_confidence.
Ground truth expects MATCHED.
**Decision: keep threshold at 0.85 (Option A).**
Reasoning: one record out of 110. Adjusting a global threshold to rescue a single known dev-set case is overfitting. The router did exactly what it was designed to do — flagged a genuinely lower-confidence match for human review instead of forcing it through. Will appear as one false negative in Agent 8 scoring. If the 550-record dataset shows a systematic cluster of delayed_settlement records near the 0.85 threshold, revisit then.

---

## Exact logic implemented per agent

### Config values (agents/config.py)

```python
FUZZY_MATCH_WEIGHTS          = {"amount": 0.55, "date": 0.35, "text": 0.10}
FUZZY_AUTO_MATCH_THRESHOLD   = 0.79
FUZZY_MIN_CANDIDATE_THRESHOLD = 0.50
SETTLEMENT_DATE_TOLERANCE_DAYS = 10
OVERDUE_SETTLEMENT_DAYS       = 10
AMOUNT_TOLERANCE_RUPEES       = 5.0
HIGH_VALUE_REVIEW_THRESHOLD_RUPEES = 50_000
LLM_CONFIDENCE_AUTO_CONFIRM   = 0.85
SKIP_VERIFICATION_CONFIDENCE  = 0.95
SKIP_VERIFICATION_MAX_AMOUNT  = 10_000
LLM_MAX_TOKENS_PER_CALL       = 700
LLM_REASONING_EFFORT          = "low"    # caps reasoning tokens for gpt-oss-20b
```

**Deviations from spec defaults:**
- `FUZZY_MATCH_WEIGHTS`: spec suggests 0.45/0.30/0.25 — changed to 0.55/0.35/0.10. With original weights, max possible score without text signal = 0.75, below auto-match threshold. Reweighted so amount+date dominates; text is a tiebreaker.
- `FUZZY_AUTO_MATCH_THRESHOLD`: spec suggests 0.90 — changed to 0.79. At 0.90, nothing auto-matches under the new weights (1-day lag gives 0.865 max). 0.79 captures clean/garbled/adversarial/refund cases while keeping delayed and semantic in the LLM band.

### Agent 3 — Fuzzy match scoring

```
predicted_settlement = rzp_amount - rzp_fee              (from actual column)
                     - refund_amount                      (if partially_refunded)

amount_score: 1.0 if diff ≤ ₹5, 0.0 if diff > ₹20, linear decay between
date_score:   1.0 if lag=0, 0.0 if lag≥10, linear decay between; 0.0 if lag<0
text_score:   max(token_sort_ratio, partial_ratio) / 100  via rapidfuzz
              uses ledger customer_name as primary text, falls back to rzp method
composite:    0.55 × amount + 0.35 × date + 0.10 × text

Routing:
  composite ≥ 0.79 AND narration NOT a merchant alias → auto-matched
  composite ≥ 0.79 AND narration IS a merchant alias  → forced to LLM
  0.50 ≤ composite < 0.79                             → LLM candidate
  composite < 0.25                                    → not assigned
```

### combined_confidence() formula (agents/config.py)

```python
def combined_confidence(a4_conf, a5_conf):
    return round((a4_conf + a5_conf) / 2.0, 4)
```
Returns 0.0 on disagreement (disagreement always routes to UNRESOLVED regardless of individual confidence).

---

## Hardcoding audit — confirmed clean

Audited 2026-09-01. All violations found and fixed:
- `_FEW_SHOT` Example 3 in `llm_reasoning_agent.py` now uses `{brand_name}`, `{registered_legal_name}`, `{ex3_alias}` placeholders filled from `MERCHANT_PROFILE` at prompt-build time
- Smoke-test `__main__` blocks use `MERCHANT_PROFILE["narration_aliases"]` not literal strings
- `llm_provider.py` retry/sleep values now from config (`LLM_TPM_SLEEP_SECONDS`, `LLM_RETRY_BACKOFF_MIN/MAX`)
- `"GYM"` removed from fallback stopword set in `_build_merchant_keywords()`
- No case_type branching in any agent outside `reporting_agent.py`
- No hardcoded record IDs or order IDs in agent logic

---

## Agent 5 calibration — confirmed genuine

After prompt revision, Agent 5 shows:
- delayed_settlement mean confidence: 0.886 (range 0.85–0.96)
- semantic_brand_narration mean confidence: 0.960 (range 0.96–0.96)
- Gap of 0.074 — semantic records (where narration corroborates via merchant alias) correctly scored higher than delayed records (where narration is generic and provides no signal)

This confirms the rewritten prompt produces calibrated output, not anchored output.

---

## LLM provider notes

| Agent | Model | Notes |
|---|---|---|
| Agent 4 (reasoning) | `openai/gpt-oss-20b` on Groq | `reasoning_effort="low"` required to prevent reasoning tokens exhausting completion budget |
| Agent 5 (verifier) | `openai/gpt-oss-120b` on Groq | Same `reasoning_effort="low"` applied |
| Agent 9 (Q&A) | `openai/gpt-oss-20b` on Groq | Grounded summarization only |

`llama-3.3-70b-versatile` (spec original) returned 404 on this account. `llama-3.1-8b-instant` also 404. Available models on this account: `openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `qwen/qwen3.6-27b`, `qwen/qwen3.8-27b`.

LLM response cache: `db/llm_cache.db`, keyed on `(record_id, SHA256(prompt)[:16])`. Only successful, schema-validated responses are cached. Cache hits bypass rate limiter and TPM sleep entirely.

---

## Pending items before final submission

1. **Agent 8 Phase B scoring** — `score_against_ground_truth()` is wired but not activated. Needs `case_id → record_id` map built into the pipeline output.
2. **Agent 8 reporting requirement** — per-case-type confidence distribution, not just match/miss counts. Required to evaluate the 0.85 threshold decision at 550-record scale.
3. **Section 11 steps 17–19** — scale to 550 records, dev-set tuning, held-out test run.
4. **Frontend Q&A tab** — Agent 9 integration into the React dashboard.
5. **FastAPI backend** — `api/` folder is empty; needed to connect the React frontend to the Python pipeline for live data rather than mock data.
