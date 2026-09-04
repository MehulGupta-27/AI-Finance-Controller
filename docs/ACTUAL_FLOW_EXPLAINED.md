# AI Finance Controller - Actual Data Flow Explained

## Your Question: What happens to unmatched records at each stage?

Here's the **exact flow** based on the actual code:

---

## Stage 1: Data Ingestion (Agent 1)

**Input:** 3 CSV files (Razorpay, Bank, Internal Ledger)  
**Output:** Normalized records in memory

```
110 total records loaded:
- Razorpay: ~90 payment records
- Bank: ~85 deposit records
- Ledger: ~90 accounting records
```

**No filtering yet** - everything moves to next stage.

---

## Stage 2: Exact Match (Agent 2)

**Input:** All 110 records  
**Logic:** Search for order ID in bank narration using regex

```python
# Example: Bank narration = "IMPS RZP order_Nx1V4TxDK" 
# Matches Razorpay order_id = "order_Nx1V4TxDK"
```

**Output:**
```
✅ MATCHED: 68 pairs (order ID found in bank statement)
   → These are RESOLVED (high confidence)
   → Status: "exact_match_gateway" 
   → Confidence: 98%

❌ UNMATCHED: 42 Razorpay records (no order ID in bank narration)
   → These move to Stage 3 (Fuzzy Match)

ℹ️  Bank records: All 85 kept for fuzzy matching
```

**Key Point:** The 68 matched pairs are **not done yet**. They get a final route based on their status:

- **If payment failed** → Route to "Payment failed — no deposit expected" (PARTIAL)
- **If payment captured** → Continue to fuzzy match to find bank deposit

---

## Stage 3: Fuzzy Match (Agent 3)

**Input:** 
- 42 unmatched Razorpay records (from Stage 2)
- 85 bank records (all of them)

**Logic:**
1. Match **amount exactly** (±₹0.01)
2. Match **date within ±3 days**
3. Calculate confidence score based on date proximity

```python
# Example:
# Razorpay: Rs.2,154.58 on Jan 15
# Bank: Rs.2,154.58 on Jan 17 (2 days later)
# → Confidence: 92% (amount perfect, date close)
```

**Output:**
```
✅ AUTO-MATCHED: 14 pairs (confidence ≥ 85%)
   → Status: "fuzzy_auto_match"
   → Confidence: 85-94%
   → These are RESOLVED

⚠️  LLM CANDIDATES: 10 pairs (confidence < 85%)
   → These move to Stage 4 (LLM Reasoning)
   → Too uncertain for auto-approval
   → Examples:
     - Date gap is 5-7 days (longer than normal)
     - Bank narration is garbled/generic
     - Merchant name mismatch

❌ UNMATCHED: 18 Razorpay records (no bank match found)
   → These are "Pending Settlement" (waiting for bank deposit)
   → Status: PARTIAL - "awaiting_settlement"

❌ UNMATCHED BANK: 3 bank deposits (no Razorpay match)
   → These are "Unidentified Credits"
   → Status: UNRESOLVED - "unidentified_bank_credit"
```

**Key Decision Point:** The fuzzy match agent uses **FUZZY_AUTO_APPROVE_THRESHOLD = 0.85**

- **≥85% confidence** → Auto-approved, no LLM needed
- **<85% confidence** → Sent to LLM for deeper analysis

---

## Stage 4: LLM Reasoning (Agent 4)

**Input:** 10 low-confidence fuzzy pairs (from Stage 3)

**Logic:** Send transaction details to AI (Groq llama-3.3-70b) to analyze:
- Delayed settlements (5-9 day lag - is this OK?)
- Garbled bank narrations (generic codes like "IMPS", "NEFT", "PG SETL")
- Merchant name mismatches (bank shows legal name vs brand name)

```python
# Example case sent to LLM:
# Razorpay: Rs.2,154.58 on Jan 10 from "FitZone Gym"
# Bank: Rs.2,154.58 on Jan 17 (7 days later)
# Bank narration: "FITZONE WELLNESS PVT LTD"
#
# LLM reasoning:
# "The 7-day lag is within normal 1-10 day NEFT window. 
#  Bank shows registered legal name 'FITZONE WELLNESS PVT LTD' 
#  which matches merchant profile. This is a valid match."
#
# Decision: match, confidence: 0.87
```

**Output:**
```
✅ LLM SAYS MATCH: 6 cases
   → Move to Stage 5 (Second Opinion)

❌ LLM SAYS NO MATCH: 2 cases
   → Status: UNRESOLVED
   → Need human review

⚠️  LLM UNCERTAIN: 2 cases
   → Status: UNRESOLVED
   → Need human review
```

**Important:** Even LLM matches go to Stage 5 for verification!

---

## Stage 5: Second Opinion / Verification (Agent 5)

**Input:** 6 LLM matches (from Stage 4)

**Logic:** 
1. **Skip condition check:**
   ```python
   if confidence ≥ 95% AND amount < Rs.10,000:
       skip_verification = True  # Trust Agent 4
   else:
       get_second_opinion = True  # High-value or uncertain
   ```

2. **Independent LLM call** (same AI, different prompt):
   - Does NOT see Agent 4's reasoning
   - Makes independent decision from raw data
   - Checks if both agents agree

```python
# Example:
# Agent 4: "match", confidence: 0.87
# Agent 5: "match", confidence: 0.85
# Result: AGREE → Combined confidence: 0.86
#
# OR
#
# Agent 4: "match", confidence: 0.78
# Agent 5: "uncertain", confidence: 0.55
# Result: DISAGREE → Combined confidence: 0.0 → UNRESOLVED
```

**Output:**
```
✅ BOTH AGREE (high confidence): 4 cases
   → Status: MATCHED
   → Sub-reason: "semantic_brand_narration" or "delayed_settlement_valid"
   → Confidence: 75-90%

❌ AGENTS DISAGREE: 2 cases
   → Status: UNRESOLVED
   → Sub-reason: "agent_disagreement"
   → Confidence: 0.0
   → Need human review
```

**Skip Logic:**
- **Skipped verification:** 2 cases (low-value + high confidence)
- **Ran verification:** 4 cases (high-value OR uncertain)

---

## Stage 6: Router / Decision Agent (Agents 6+7)

**Input:** All results from Stages 2-5

**Logic:** Route each record to final category:

1. **AUTO_APPROVED (Status: MATCHED)**
   - Exact matches (98% confidence)
   - Fuzzy auto-matches (85-94% confidence)
   - LLM matches with agreement (75-90% confidence)

2. **IN PROGRESS (Status: PARTIAL)**
   - Pending settlement (waiting for bank deposit)
   - Failed payments (no deposit expected)

3. **NEEDS REVIEW (Status: UNRESOLVED)**
   - Agent disagreement
   - Low confidence LLM matches
   - Unidentified bank credits
   - Missing ledger entries

**Also generates plain English explanations:**
- Headline: "AUTO-APPROVED — Perfect match found — 98% sure"
- Checklist: What passed/failed
- Recommendation: What to do next

---

## Final Summary: What Happens to Unmatched Records?

Here's the **waterfall** of unmatched records:

```
START: 110 records total

Stage 2 (Exact Match):
  ✅ 68 matched → RESOLVED
  ❌ 42 unmatched → Go to Stage 3

Stage 3 (Fuzzy Match):
  ✅ 14 auto-matched (≥85% confidence) → RESOLVED
  ⚠️  10 low-confidence → Go to Stage 4 (LLM)
  ❌ 18 no bank match → "Pending Settlement" (PARTIAL - IN PROGRESS)

Stage 4 (LLM Reasoning):
  ✅ 6 LLM says match → Go to Stage 5 (Verification)
  ❌ 4 LLM says no/uncertain → UNRESOLVED (NEEDS REVIEW)

Stage 5 (Second Opinion):
  ✅ 4 both agree → RESOLVED
  ❌ 2 disagree → UNRESOLVED (NEEDS REVIEW)

FINAL RESULTS:
  ✅ AUTO_APPROVED: 86 records (68+14+4)
  ⚠️  IN PROGRESS: 18 records (pending settlement)
  ❌ NEEDS REVIEW: 6 records (4 LLM failed + 2 disagreements)

Total: 110 records
```

---

## Key Insights

### 1. **Not ALL unmatched records go to LLM**

```
Stage 2 → Unmatched (42)
  ↓
Stage 3 → Auto-matched: 14
          Pending: 18
          LLM candidates: 10  ← Only these 10 go to LLM
```

**Why?** 
- 18 have **no bank record at all** → Obviously pending settlement, no LLM needed
- 14 have **high confidence match** (≥85%) → Auto-approve, no LLM needed
- Only 10 are **uncertain** → Need AI reasoning

### 2. **Unmatched ≠ Needs Review**

There are 3 types of "unmatched":

| Type | Category | Needs Human? |
|------|----------|--------------|
| **Pending settlement** | IN PROGRESS | ❌ No - just waiting |
| **Failed payment** | IN PROGRESS | ❌ No - expected behavior |
| **Unidentified credit** | NEEDS REVIEW | ✅ Yes - investigate |

### 3. **The Confidence Threshold Matters**

```python
FUZZY_AUTO_APPROVE_THRESHOLD = 0.85

# If we set it to 0.90:
# - Fewer auto-approvals
# - More LLM calls (slower, more expensive)
# - More conservative (fewer false positives)

# If we set it to 0.75:
# - More auto-approvals
# - Fewer LLM calls (faster, cheaper)
# - More aggressive (risk of false positives)
```

### 4. **LLM is ONLY for Complex Cases**

**LLM handles:**
- ✅ Delayed settlements (5-9 day lag)
- ✅ Garbled narrations (generic bank codes)
- ✅ Merchant name mismatches (legal name vs brand)

**LLM does NOT handle:**
- ❌ Exact matches (regex is faster/cheaper)
- ❌ High-confidence fuzzy (deterministic rule works)
- ❌ Pending settlements (obvious - no bank record)

This keeps costs low and speed high!

---

## Visual Flow Diagram

```
┌─────────────────────┐
│   110 Records       │
│   Loaded            │
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  EXACT MATCH        │
│  (Agent 2)          │
│  Regex: order_id    │
└──────────┬──────────┘
           │
    68 matched │ 42 unmatched
           │              │
           ↓              ↓
    ┌──────────┐   ┌─────────────────────┐
    │ RESOLVED │   │   FUZZY MATCH       │
    │  (98%)   │   │   (Agent 3)         │
    └──────────┘   │   Amount + Date     │
                   └──────────┬──────────┘
                              │
                   14 auto │ 10 uncertain │ 18 no bank
                       │        │              │
                       ↓        ↓              ↓
                 ┌──────┐  ┌────────┐   ┌──────────┐
                 │RESOLVED│ │  LLM   │   │ PENDING  │
                 │(85-94%)│ │(Agent4)│   │SETTLEMENT│
                 └────────┘ └───┬────┘   └──────────┘
                                │
                         6 match │ 4 no/uncertain
                                │        │
                                ↓        ↓
                        ┌──────────┐  ┌──────────┐
                        │2ND OPINION│  │UNRESOLVED│
                        │ (Agent 5) │  │  REVIEW  │
                        └─────┬─────┘  └──────────┘
                              │
                       4 agree │ 2 disagree
                              │        │
                              ↓        ↓
                        ┌──────────┐  ┌──────────┐
                        │ RESOLVED │  │UNRESOLVED│
                        │(75-90%)  │  │  REVIEW  │
                        └──────────┘  └──────────┘
```

---

## Answer to Your Question

**Q: "Data which is not matched after fuzzy - marked for human review OR goes to LLM?"**

**A:** It depends on **why** it's unmatched:

1. **No bank record found at all?**  
   → ❌ **NOT sent to LLM**  
   → Marked as "Pending Settlement" (IN PROGRESS)  
   → No review needed - just waiting for bank deposit

2. **Bank record exists but low confidence match (<85%)?**  
   → ✅ **SENT to LLM** (Agent 4)  
   → LLM analyzes if it's a valid delayed/garbled/misnamed match  
   → Then second opinion (Agent 5) validates it  
   → If agents agree → AUTO-APPROVED  
   → If agents disagree or LLM uncertain → NEEDS REVIEW

3. **Unidentified bank credit (bank deposit with no Razorpay record)?**  
   → ❌ **NOT sent to LLM**  
   → Directly marked NEEDS REVIEW  
   → Possible refund/chargeback/error - needs human investigation

**In short:**
- **~10 records** go to LLM (uncertain fuzzy matches)
- **~18 records** marked IN PROGRESS (pending settlement)
- **~6 records** marked NEEDS REVIEW (various issues)

Not all unmatched records need LLM or human review!

---

**Last Updated:** 2026-09-03  
**Based on:** `agents/pipeline.py` actual code
