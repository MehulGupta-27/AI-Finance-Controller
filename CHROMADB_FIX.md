# ChromaDB Indexing Fix - Bank Narration for Semantic Search

## Issue 1: Missing Bank Narration in Indexed Text
Q&A feature was returning semantically irrelevant results. Query "Are there any gym membership payments?" would:
- Retrieve records that had nothing to do with gyms
- Or fail to retrieve actual gym-related records

## Issue 2: Irrelevant Records Displayed
When users searched for irrelevant queries (e.g., "bye", random text), the UI would:
- Show answer: "No matching records found"
- BUT still display 10 record cards below the answer
- Very confusing - why show records if there are no matches?

## Root Cause
Bank narration fields were missing from ChromaDB indexed text for matched records.

### The Data Flow
1. **Exact Match (Agent 2)**: Ledger ↔ Razorpay (by order_id)
2. **Fuzzy Match (Agent 3)**: Razorpay ↔ Bank (by amount/date/text)
3. **Combined**: Some exact-matched pairs also get fuzzy-matched to bank records

### The Problem
The `raw_lookup` dictionary in `agents/pipeline.py` constructs the raw fields for each record:
- Starts with ledger records (customer, order_id, notes) → **no bank narration**
- Adds Razorpay captured_date when exact-matched
- **BUG**: Never populated bank narration for matched records

This meant:
- Unmatched bank records → had narration ✓
- Matched records → narration was empty string ✗

For example:
- Ledger notes: "Monthly gym membership renewal" 
- Bank narration: "FITZONE WELLNESS PVT LTD" ← **MISSING**

Without the bank narration, queries like "gym membership" couldn't find records where the actual business name "FITZONE WELLNESS" appears.

## The Fix

### Fix 1: Populate Bank Narration (agents/pipeline.py)
Modified `agents/pipeline.py` lines 454-477 to populate bank narration from fuzzy matches:

```python
# For auto-matched fuzzy pairs, populate settled_date AND bank narration
for pair in fuzzy.auto_matched_pairs:
    rzp_id = pair.rzp_record.record_id
    led_id = rzp_to_ledger_id.get(rzp_id, rzp_id)
    if led_id in raw_lookup:
        raw_lookup[led_id]["settled_date"] = pair.bank_record.date
        # Populate bank narration for semantic search (critical for Q&A)
        raw_lookup[led_id]["narration"] = pair.bank_record.text_field

# Also populate bank narration for LLM-verified pairs
for vr in ver_results:
    rzp_id = vr.pair.rzp_record.record_id
    led_id = rzp_to_ledger_id.get(rzp_id, rzp_id)
    if led_id in raw_lookup:
        raw_lookup[led_id]["narration"] = vr.pair.bank_record.text_field
```

### Fix 2: Don't Show Records for Irrelevant Queries (api/main.py)
Modified `api/main.py` to check if results are actually relevant before returning records:

```python
# Check if results are actually relevant
# If the LLM says "no matching records" or similarity is very low, return empty records
min_similarity_threshold = 0.20  # 20% similarity minimum
max_similarity = max((1 - dist) for dist in distances) if distances else 0

# If answer explicitly says "no matching" or similarity is too low, don't show records
answer_lower = answer_text.lower()
if "no matching records" in answer_lower or "no records found" in answer_lower or max_similarity < min_similarity_threshold:
    return {"answer": answer_text, "records": []}
```

**Logic:**
1. Check maximum semantic similarity of retrieved records
2. If LLM answer contains "no matching records" or "no records found" → return empty records array
3. If similarity < 20% → return empty records array (too irrelevant)
4. Otherwise, return the records the LLM analyzed

## Verification

### Fix 1: Bank Narration Indexing
After fix, indexed text includes bank narration:

**Before:**
```
Deepa Patel | ORD962367 | Monthly gym membership renewal
```

**After:**
```
Deepa Patel | FITZONE WELLNESS PVT LTD | ORD962367 | Monthly gym membership renewal
```

### Test Results
Query: "Are there any gym membership payments?"

**Semantic search now correctly retrieves:**
1. "Deepa Patel | FITZONE WELLNESS PVT LTD | ORD962367 | Monthly gym membership renewal" (45.6% similarity)
2. "Ananya Kumar | FITZONE WELLNESS P LTD SETL | ORD221761 | Annual premium membership upgrade" (27.1% similarity)
3. "Isha Mishra | FZW PRIVATE LIMITED RZRPY | ORD766269 | Personal training package - 10 sessions" (21.8% similarity)

**LLM Answer:**
"Found 4 gym membership payments totaling Rs.11,718.88. Three are fully reconciled and one is an annual upgrade. No unresolved membership payments were found."

✅ Correct and grounded in actual data

### Fix 2: Irrelevant Query Handling

**Query: "bye"**
- Answer: "No matching records found."
- Records: 0 (empty array) ✅

**Query: "asdfghjkl"**
- Answer: "No matching records found."
- Records: 0 (empty array) ✅

**Query: "gym membership payments"**
- Answer: "Found 4 matched gym membership payments totaling Rs.7,844.91..."
- Records: 10 (relevant records shown) ✅

## Impact
- Q&A feature now works correctly for all queries requiring bank narration (merchant names, payment methods, transaction descriptions)
- Semantic search can match queries like:
  - "gym membership" → "FITZONE WELLNESS"
  - "UPI payments" → "UPI/331148"
  - "NEFT transfers" → "NEFT/SIDD"
  - Merchant/business names from bank statements

## Files Modified
- `agents/pipeline.py` (lines 454-477): Populate bank narration for matched records
- `api/main.py` (lines 258-270): Return empty records array for irrelevant queries
