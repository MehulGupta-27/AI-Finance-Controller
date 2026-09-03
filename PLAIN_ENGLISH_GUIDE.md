# Plain English Explanations — Before & After

Every transaction explanation is now written so anyone can understand it — no technical jargon, no system internals, just clear language about what happened and what to do.

---

## Example 1: Fuzzy Match (Amount + Date)

**Before (Technical):**
```
Headline: MATCHED — 89% confidence
Checklist:
  ✓ Amount matches: Rs.5,842.87 vs Rs.5,842.87
  ✓ Settlement date: 1-day lag
  ✗ Text similarity: 22% (narration: 'PG SETL 189')
```

**After (Plain English):**
```
Headline: MATCHED — 89% sure this is correct
Checklist:
  ✓ Amount matches: Rs.5,842.87 vs Rs.5,842.87
  ✓ Bank deposit arrived 1 day after payment — normal timing
  ✗ Bank description: 'PG SETL 189' doesn't match (matched on amount and date instead)
```

**What changed:**
- "89% confidence" → "89% sure this is correct"
- "Settlement date: 1-day lag" → "Bank deposit arrived 1 day after payment — normal timing"
- "Text similarity: 22%" → removed (too technical)
- Added explanation for why low text score is OK: "(matched on amount and date instead)"

---

## Example 2: AI Reasoning (LLM Match)

**Before (Technical):**
```
Headline: MATCHED — 85% confidence
Checklist:
  ✓ Semantic similarity: 67%
  ✓ Agent 4 reasoning: Amount and settlement date match...
  ✓ Agent 5 independent verification: match (85%)
```

**After (Plain English):**
```
Headline: MATCHED — 85% sure this is correct
Checklist:
  ✓ Why this matched: Amount and settlement date match within acceptable lag...
  ✓ Independently verified by a second check (85% confidence)
```

**What changed:**
- "Semantic similarity: 67%" → removed entirely
- "Agent 4 reasoning" → "Why this matched"
- "Agent 5 independent verification" → "Independently verified by a second check"
- System internals (Agent 4/5) hidden from user

---

## Example 3: Waiting for Bank Deposit

**Before (Technical):**
```
Status: PARTIAL
Headline: Awaiting settlement (8 days)
Checklist:
  ✓ Ledger confirms order
  ✓ Gateway (Razorpay) confirms payment captured
  ✗ Bank settlement pending (8/10 days elapsed)
What to do: will auto-resolve on next pipeline run once bank settles
```

**After (Plain English):**
```
Status: IN PROGRESS
Headline: Waiting for bank deposit (8 days so far) — 90% sure this is correct
Checklist:
  ✓ Your order book confirms the sale
  ✓ Razorpay confirms payment was captured
  ✗ Bank deposit hasn't arrived yet (8 out of 10 day window)
What to do: No action needed — banks typically take 1-5 days. This will auto-close once the deposit arrives.
```

**What changed:**
- "PARTIAL" → "IN PROGRESS"
- "Awaiting settlement" → "Waiting for bank deposit"
- Added confidence: "90% sure this is correct"
- "Ledger confirms" → "Your order book confirms"
- "Gateway (Razorpay)" → "Razorpay"
- "8/10 days elapsed" → "8 out of 10 day window"
- Action is now conversational: "No action needed — banks typically take 1-5 days"

---

## Example 4: No Order on Record

**Before (Technical):**
```
Status: PARTIAL
Headline: No ledger record
Checklist:
  ✓ Gateway confirms capture: Rs.7,320.07
  ✓ Bank confirms settlement: Rs.7,147.32
  ✗ No matching ledger entry found
What to do: Flag for ops: check integration/webhook logs
```

**After (Plain English):**
```
Status: IN PROGRESS
Headline: Money received, but no order on record — 85% sure this is correct
Checklist:
  ✓ Razorpay shows payment was captured: Rs.7,320.07
  ✓ Bank deposit arrived: Rs.7,147.32
  ✗ No matching order found in your system
What to do: Check if this was a manual payment through Razorpay dashboard, or an offline order that wasn't logged
```

**What changed:**
- "No ledger record" → "Money received, but no order on record"
- Added confidence display
- "Gateway confirms capture" → "Razorpay shows payment was captured"
- "Bank confirms settlement" → "Bank deposit arrived"
- "No matching ledger entry" → "No matching order found in your system"
- Action explains WHY this happens: "manual payment through dashboard, or offline order"

---

## Example 5: Low Confidence Match

**Before (Technical):**
```
Status: UNRESOLVED
Headline: 84% confidence
Checklist:
  ✗ Combined confidence 84% below threshold 85%
  ✗ Agent 4 reasoning: Amount and settlement date match...
What to do: Review and confirm or reject the suggested match
```

**After (Plain English):**
```
Status: NEEDS REVIEW
Headline: Looks like a match, but we're not 100% sure
Checklist:
  ✗ We're only 84% confident this is correct (we need at least 85% to approve automatically)
  ✗ Why it might be a match: Amount and settlement date match...
What to do: Take a quick look and click 'Yes, this is a match' if it looks right
```

**What changed:**
- "UNRESOLVED" → "NEEDS REVIEW"
- "84% confidence" → "Looks like a match, but we're not 100% sure"
- "Combined confidence below threshold" → "We're only 84% confident... (we need at least 85%)"
- "Agent 4 reasoning" → "Why it might be a match"
- Action is actionable: "Take a quick look and click 'Yes, this is a match'"

---

## Example 6: Unexplained Bank Credit

**Before (Technical):**
```
Status: UNRESOLVED
Headline: Unidentified credit
Checklist:
  ✗ No matching ledger entry
  ✗ No matching gateway record
  ✓ Amount Rs.1,452.71 on 2026-02-07 narration: 'BANK REVERSAL FEES'
What to do: Identify the source of this credit
```

**After (Plain English):**
```
Status: NEEDS REVIEW
Headline: Money received with no matching customer payment — needs investigation
Checklist:
  ✗ No matching order found in your system
  ✗ No matching Razorpay transaction
  ✓ Bank credited Rs.1,452.71 on 2026-02-07 with description: 'BANK REVERSAL FEES'
What to do: Contact your bank to identify the source — could be interest, a fee reversal, or a misdirected transfer
```

**What changed:**
- "Unidentified credit" → "Money received with no matching customer payment"
- "No matching ledger entry" → "No matching order found in your system"
- "No matching gateway record" → "No matching Razorpay transaction"
- "narration:" → "with description:"
- Action explains possibilities: "could be interest, a fee reversal, or misdirected transfer"

---

## Frontend Display (Table View)

**Category Labels (sub_reason):**

| Before | After |
|--------|-------|
| `no_action_needed` | Payment failed — no money moved |
| `awaiting_settlement` | Waiting for bank to deposit the money |
| `no_ledger_record` | Money received — but no order was recorded in your system |
| `overdue_settlement` | Bank deposit is late |
| `agent_disagreement` | System checks disagreed — need your call |
| `low_confidence` | Looks like a match — but not 100% sure |
| `high_value_review_required` | Large amount — needs your sign-off |
| `unidentified_bank_credit` | Money appeared in bank — source unknown |
| `no_candidates_found` | No match found in any system |

**Confidence Display:**

Before: Just a progress bar
After: Progress bar + "84% sure" text

---

## Key Principles

1. **No technical terms:** Gateway → Razorpay, Ledger → Your order book, Settlement → Bank deposit
2. **No system internals:** Agent 4/5 → "Why this matched" / "Second check"
3. **Confidence always shown:** Every headline now says how sure we are
4. **Explain the "why":** Don't just say what failed — explain what that means
5. **Actionable recommendations:** Not "Flag for ops" but "Check if this was a manual payment"
6. **Conversational tone:** "Take a quick look" not "Review and confirm"
7. **Status labels:** PARTIAL → IN PROGRESS, UNRESOLVED → NEEDS REVIEW

Every explanation answers three questions:
1. **What happened?** (headline + checklist)
2. **How sure are we?** (confidence score)
3. **What should I do?** (recommendation)
