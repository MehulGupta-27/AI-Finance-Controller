# How LLM Agents (4 & 5) Make Decisions

## Your Questions:

1. **What does LLM analyze if amount/date are already checked by fuzzy match?**
2. **Do Agent 4 and Agent 5 see each other's responses?**
3. **Are they run in sequence or parallel?**

---

## Question 1: What Does the LLM Analyze?

### The Problem: Fuzzy Match Found a Match But It's Uncertain

Fuzzy match found a bank record that:
- ✅ **Amount matches perfectly** (Rs.2,154.58 = Rs.2,154.58)
- ✅ **Date is close** (Jan 10 → Jan 17, 7 days later)
- ❌ **BUT confidence < 85%** (too uncertain to auto-approve)

**Why is confidence low?** One of these reasons:

1. **Delayed settlement** (5-9 days) - longer than normal 1-3 days
2. **Garbled narration** - bank description is generic/unreadable
3. **Merchant name mismatch** - brand name vs legal name

Fuzzy match can't handle these - it needs **human-like reasoning**.

---

## What Agent 4 (LLM) Analyzes

### Agent 4 Gets These EXTRA Signals:

```python
# What Agent 4 sees that fuzzy match didn't consider:
{
  "merchant_profile": {
    "brand_name": "FitZone Gym",
    "registered_legal_name": "FITZONE WELLNESS PVT LTD",
    "known_aliases": ["FITZONE", "FITZONE GYM", "FZ WELLNESS"]
  },
  
  "ledger_notes": "Monthly gym membership for Rajesh Kumar",
  
  "bank_narration": "FITZONE WELLNESS PVT LTD NEFT",
  
  "settlement_lag": "7 days",
  
  "expected_settlement_range": "1-10 days (NEFT transfers)",
  
  "risk_context": "HIGH VALUE TRANSACTION" (if > Rs.50,000)
}
```

### Agent 4 Analyzes These 4 Things:

#### 1. **Semantic Similarity (Text Meaning)**

**Question:** Do the descriptions refer to the same business?

**Example Case:**
```
Ledger notes: "Monthly gym membership"
Bank narration: "FITZONE WELLNESS PVT LTD"
Merchant profile: Brand = "FitZone Gym", Legal name = "FITZONE WELLNESS PVT LTD"

LLM Reasoning:
"Bank narration shows the merchant's registered legal name 'FITZONE WELLNESS PVT LTD'
which matches the merchant profile. The ledger notes 'gym membership' are consistent
with this being a gym business. This is the same merchant."

semantic_similarity: 0.95 (very high)
decision: match
confidence: 0.88
```

**What fuzzy match can't do:** Fuzzy match only checks if text strings are similar character-by-character. It doesn't understand that "FitZone Gym" and "FITZONE WELLNESS PVT LTD" refer to the same business.

---

#### 2. **Settlement Delay Reasoning (Time Context)**

**Question:** Is this delay acceptable or suspicious?

**Example Case:**
```
Payment captured: Jan 10
Bank deposit: Jan 17 (7 days later)
Fuzzy confidence: 82% (below 85% threshold)

LLM Reasoning:
"7-day lag is longer than the typical 1-3 days but falls within the normal
1-10 day window for NEFT bank transfers. The amount matches perfectly and
the merchant name is confirmed. This is a valid delayed settlement."

decision: match
confidence: 0.85
risk_flags: ["delayed_settlement_valid"]
```

**What fuzzy match can't do:** Fuzzy match just penalizes delays with a math formula (date_score decreases with lag). It doesn't understand banking norms like "NEFT takes 1-10 days, RTGS is same-day."

---

#### 3. **Garbled Narration Handling (Context Clues)**

**Question:** Bank description is unreadable - can we match on other signals?

**Example Case:**
```
Bank narration: "IMPS NEFT PG SETL TXN6622"  ← Generic code, no merchant name
Amount: Rs.2,154.58 ✅ Perfect match
Date: Jan 12 → Jan 14 (2 days) ✅ Normal
Ledger notes: "Website subscription renewal for Acme Corp"

LLM Reasoning:
"Bank narration is generic payment gateway code with no merchant identifier.
However, amount matches exactly and timing is normal (2 days). The ledger
notes show a business transaction consistent with the amount. With no 
contradictory evidence, this is likely a match, but confidence reduced due
to lack of narration confirmation."

decision: match
confidence: 0.78
risk_flags: ["garbled_narration"]
```

**What fuzzy match can't do:** Fuzzy match gives up when bank narration doesn't contain useful text. It can't weigh "amount + date perfect" against "narration unhelpful" like a human would.

---

#### 4. **Merchant Alias Recognition (Business Knowledge)**

**Question:** Is "ABC PVT LTD" the same as "ABC Store"?

**Example Case:**
```
Merchant profile:
  Brand: "FitZone Gym"
  Legal name: "FITZONE WELLNESS PRIVATE LIMITED"
  Aliases: ["FITZONE", "FZ WELLNESS", "FITZONE GYM"]

Bank narration: "FZ WELLNESS NEFT"  ← Matches alias list
Ledger customer: "Rajesh Kumar"
Ledger notes: "Gym membership renewal"

LLM Reasoning:
"Bank narration 'FZ WELLNESS' matches a known merchant alias from the profile.
The ledger notes 'gym membership' are consistent with a fitness business.
This is clearly the merchant's own settlement."

semantic_similarity: 0.92
decision: match
confidence: 0.90
```

**What fuzzy match can't do:** Fuzzy match doesn't have business knowledge. It doesn't know that companies have multiple names (brand vs legal name vs aliases).

---

## The Actual Prompt Sent to Agent 4

Here's what the LLM actually sees:

```
You are a financial reconciliation analyst. Determine if a Razorpay payment 
and a bank settlement refer to the same transaction.

Rules:
- A false match is worse than an honest uncertain. Be conservative.
- Amount must match expected settlement (rzp_amount - rzp_fee - refund_amount).
- Lag 1-10 days is normal. Lag 5-9 days is delayed but still valid.
- Bank narrations are often garbled or abbreviated - focus on amount and date.
- If narration matches this merchant's registered name or aliases, 
  it IS this merchant's own settlement.

MERCHANT PROFILE:
  Brand: FitZone Gym
  Registered name: FITZONE WELLNESS PVT LTD
  Known settlement narration aliases: FITZONE, FZ WELLNESS, FITZONE GYM

CANDIDATE PAIR:
  Razorpay ID     : pay_R4xK2pLmN
  Bank UTR        : HDFC4523891
  Rzp amount      : Rs.2,154.58  (captured 2024-01-10)
  Rzp fee         : Rs.45.42
  Refund amount   : Rs.0.00
  Expected settle : Rs.2,109.16
  Bank amount     : Rs.2,109.16  (settled 2024-01-17)
  Amount diff     : Rs.0.00  (lag 7 days)
  Customer        : Rajesh Kumar
  Ledger notes    : 'Monthly gym membership renewal'
  Bank narration  : 'FITZONE WELLNESS PVT LTD NEFT'
  Ledger status   : captured
  Fuzzy scores    : amount=1.00  date=0.70  text=0.45  composite=0.82
  
  record_id to use in response: led_001
  candidate_ids to use: ["led_001", "bank_012"]

Respond with JSON only. Fields: 
  record_id, candidate_ids, semantic_similarity (0.0-1.0), 
  decision (match/no_match/uncertain), confidence (0.0-1.0), 
  reasoning (one sentence), risk_flags (array)
```

### Agent 4's Response:

```json
{
  "record_id": "led_001",
  "candidate_ids": ["led_001", "bank_012"],
  "semantic_similarity": 0.95,
  "decision": "match",
  "confidence": 0.87,
  "reasoning": "Bank narration matches merchant legal name from profile, 7-day lag is within normal NEFT window, amount matches perfectly after fee deduction.",
  "risk_flags": ["delayed_settlement_valid"]
}
```

---

## Question 2: Do Agent 4 and Agent 5 See Each Other's Responses?

### Answer: **NO - They Work Independently**

**Agent 4's Prompt:**
- ❌ Does NOT see Agent 5's opinion
- ✅ Sees raw transaction data
- ✅ Sees merchant profile
- ✅ Sees fuzzy match scores (for context)

**Agent 5's Prompt:**
- ❌ Does NOT see Agent 4's reasoning
- ❌ Does NOT see Agent 4's confidence score
- ✅ Sees exact same raw transaction data
- ✅ Sees merchant profile (identical context)
- ✅ Only knows Agent 4's final decision ("match" or "no_match")

### Why Independent?

**Goal:** Prevent "confirmation bias"

**Bad scenario (if Agent 5 saw Agent 4's reasoning):**
```
Agent 4: "This is a match because the legal name matches"
Agent 5 reads this: "Oh, Agent 4 already found a reason. I'll agree."
```

**Good scenario (current system):**
```
Agent 4: "match" (confidence 0.85)
Agent 5: Gets same data, reasons independently
  - If sees same evidence → "match" (confidence 0.83)
  - If spots a problem → "uncertain" (confidence 0.60)
  
Both agree → Combined confidence: 0.84
They disagree → Combined confidence: 0.0 → NEEDS REVIEW
```

---

## The Actual Prompt Sent to Agent 5

```
You are an independent financial reconciliation auditor. Your job is to verify
whether a Razorpay payment and a bank settlement are the same transaction.

You have NOT seen any prior analysis. Reason from the data yourself.

[Same rules and merchant profile as Agent 4]

CANDIDATE PAIR:
[Same transaction details as Agent 4]

Respond with JSON only. Fields:
  record_id, independent_decision (match/no_match/uncertain),
  independent_confidence (0.0-1.0), 
  agrees_with_agent_4 (true/false — does your conclusion agree with decision="match"?),
  verifier_notes (one sentence explaining what drove your confidence level)
```

**Key Difference:** Agent 5's prompt says:
- "You have NOT seen any prior analysis"
- "Reason from the data yourself"
- "agrees_with_agent_4" field - Agent 5 must check if its decision matches Agent 4's (but NOT why)

---

## Question 3: Sequential or Parallel?

### Answer: **Sequential (Agent 4 → Agent 5)**

```python
# From pipeline.py (actual code):

# Step 1: Run Agent 4 on all uncertain fuzzy matches
a4_results = reason_batch(fuzzy.llm_candidates)  # 10 records

# Step 2: Filter successful Agent 4 results
a4_valid = [result for result in a4_results if not isinstance(result, LLMError)]

# Step 3: Run Agent 5 ONLY on successful Agent 4 results
ver_results = verify_batch(a4_valid)  # 8 records (2 had LLM errors)
```

### Why Sequential?

1. **Agent 5 needs Agent 4's decision** to check agreement
2. **No point running Agent 5 if Agent 4 failed** (LLM error)
3. **Skip logic depends on Agent 4's confidence:**
   ```python
   if agent4_confidence >= 0.95 AND amount < Rs.10,000:
       skip_agent_5()  # High confidence + low value = trust Agent 4
   else:
       run_agent_5()   # Get second opinion
   ```

### Timeline:

```
T=0s    Agent 4 starts (10 records)
T=5s    Agent 4 finishes
        ↓
        Filter successful results (8 pass, 2 errors)
        ↓
        Check skip condition (2 skipped, 6 need verification)
        ↓
T=5s    Agent 5 starts (6 records)
T=10s   Agent 5 finishes
```

**Total:** ~10 seconds for 10 records (sequential LLM calls due to rate limits)

---

## Real Example: Complete Flow

### Case: Delayed Settlement with Legal Name

**Input Data:**
```
Razorpay payment: Rs.2,154.58 on Jan 10 (order_Nx1V4TxDK)
Bank deposit: Rs.2,109.16 on Jan 17 (UTR: HDFC8372910)
Bank narration: "FITZONE WELLNESS PVT LTD NEFT"
Ledger customer: Rajesh Kumar
Ledger notes: "Gym membership renewal"
```

---

### Stage 1: Exact Match (Agent 2)
```
Search for "order_Nx1V4TxDK" in bank narration
Bank narration: "FITZONE WELLNESS PVT LTD NEFT"
Result: ❌ NOT FOUND (no order ID)
→ Send to Fuzzy Match
```

---

### Stage 2: Fuzzy Match (Agent 3)
```
Amount match: Rs.2,109.16 = Rs.2,109.16 ✅ Perfect (score: 1.00)
Date match: Jan 10 → Jan 17 (7 days) ⚠️ Delayed (score: 0.70)
Text match: "Rajesh Kumar" vs "FITZONE WELLNESS" ❌ No overlap (score: 0.10)

Composite score: 0.82 (weighted average)
Threshold: 0.85
Result: ❌ Below threshold (0.82 < 0.85)
→ Send to LLM
```

---

### Stage 3: Agent 4 (LLM Reasoning)
```
LLM Sees:
- Amount: Perfect match after fee deduction
- Date: 7 days lag (delayed but within 1-10 day NEFT window)
- Bank narration: "FITZONE WELLNESS PVT LTD" ← Matches merchant legal name!
- Ledger notes: "Gym membership" ← Consistent with gym business
- Merchant aliases: ["FITZONE", "FZ WELLNESS", "FITZONE GYM"]

LLM Reasons:
"Bank deposit shows merchant's registered legal name 'FITZONE WELLNESS PVT LTD'
from the merchant profile. The 7-day lag is longer than typical but within
normal NEFT settlement range (1-10 days). Amount matches perfectly. This is
a valid delayed settlement from the merchant's bank account."

Output:
{
  "semantic_similarity": 0.95,  ← High! Legal name matched
  "decision": "match",
  "confidence": 0.87,
  "reasoning": "Legal name match + valid NEFT delay",
  "risk_flags": ["delayed_settlement_valid"]
}
```

---

### Stage 4: Agent 5 (Second Opinion)

**Skip Check:**
```python
should_skip = (confidence >= 0.95 AND amount < Rs.10,000)
            = (0.87 >= 0.95 AND 2154.58 < 10000)
            = (False AND True)
            = False
→ Run Agent 5 (confidence not high enough)
```

**Agent 5 Analysis:**
```
LLM Sees (same data as Agent 4, but fresh reasoning):
- Amount: Perfect match ✅
- Date: 7 days (on the edge of normal) ⚠️
- Bank narration: Contains "FITZONE WELLNESS PVT LTD" ✅
- Known aliases: Matches legal name from profile ✅

LLM Reasons (independently):
"Amount and merchant name match strongly. The 7-day lag is at the upper
end of normal but still acceptable for NEFT. No red flags detected."

Output:
{
  "independent_decision": "match",
  "independent_confidence": 0.85,
  "agrees_with_agent_4": true,  ← Both said "match"
  "verifier_notes": "Legal name confirmed, delay acceptable"
}
```

---

### Stage 5: Agreement Check
```python
Agent 4: "match", confidence: 0.87
Agent 5: "match", confidence: 0.85
Agree: YES ✅

Combined confidence = (0.87 + 0.85) / 2 = 0.86

Final status: MATCHED
Sub-reason: "semantic_brand_narration"
Confidence: 86%
```

---

### Stage 6: Plain English Explanation (Router)
```
Headline:
"AUTO-APPROVED — Merchant name verified in bank statement — 86% sure this is correct"

Checklist:
✅ Amount matches perfectly after payment gateway fees
✅ Bank statement shows your registered business name 'FITZONE WELLNESS PVT LTD'
⚠️ Deposit arrived 7 days after payment (longer than usual 1-3 days, but within normal 1-10 day window)

Recommendation:
"No action needed — this is a confirmed match. The delay is within banking norms for NEFT transfers."

Risk Flags:
• Delayed settlement (7 days)
```

---

## What If Agents Disagree?

### Example: Agent Disagreement

**Same case, but Agent 5 spots an issue:**

```
Agent 4: "match", confidence: 0.78
Agent 5: "uncertain", confidence: 0.55

Reasoning: "While legal name matches, the unusually long 7-day lag combined
with the fact that this is a high-value transaction warrants human review
to rule out a coincidental amount match with a different business."

Agree: NO ❌
Combined confidence: 0.0 (disagreement always = 0)

Final status: UNRESOLVED
Sub-reason: "agent_disagreement"
Explanation:
"NEEDS REVIEW — Our AI agents disagreed on this match — 0% confidence

Checklist:
✅ Amount matches perfectly
✅ Bank shows business name 'FITZONE WELLNESS PVT LTD'
⚠️ Two AI reviewers came to different conclusions

Recommendation:
Check if this bank deposit is genuinely from your business or a coincidental
amount match. Verify the bank account holder name matches your registered
business account."
```

---

## Summary Table

| What | Agent 2 (Exact) | Agent 3 (Fuzzy) | Agent 4 (LLM) | Agent 5 (Second Opinion) |
|------|----------------|----------------|---------------|-------------------------|
| **Checks order ID** | ✅ Yes | ❌ No | ❌ No | ❌ No |
| **Checks amount** | ❌ No | ✅ Yes (exact) | ✅ Yes (context) | ✅ Yes (context) |
| **Checks date** | ❌ No | ✅ Yes (±3 days) | ✅ Yes (1-10 day norms) | ✅ Yes (1-10 day norms) |
| **Checks text similarity** | ❌ No | ✅ Yes (character match) | ✅ Yes (semantic meaning) | ✅ Yes (semantic meaning) |
| **Understands merchant aliases** | ❌ No | ❌ No | ✅ Yes | ✅ Yes |
| **Reasons about delays** | ❌ No | ❌ No | ✅ Yes | ✅ Yes |
| **Handles garbled text** | ❌ No | ❌ No | ✅ Yes | ✅ Yes |
| **Sees Agent 4's reasoning** | N/A | N/A | N/A | ❌ No (independent) |
| **Run timing** | First | Second | Third | Fourth (after Agent 4) |

---

## Key Takeaways

1. **LLM doesn't re-check amount/date math** - it analyzes WHY a fuzzy match is uncertain
2. **Agent 4 & 5 are independent** - Agent 5 never sees Agent 4's reasoning
3. **Sequential execution** - Agent 5 runs after Agent 4, only on successful Agent 4 results
4. **LLM adds business intelligence** - understanding merchant aliases, banking delays, garbled text
5. **Disagreement = automatic review** - if agents disagree, confidence drops to 0%

The LLM layer exists to handle the **nuanced, human-judgment cases** that simple rules can't resolve!
