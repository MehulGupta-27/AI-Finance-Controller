# AI Finance Controller - Visual Architecture Diagram Specification

## Use this specification to create a visual diagram similar to the GenW.AI reference

---

## Layout: Left-to-Right Flow

```
[DATA SOURCES] → [INGESTION] → [MATCHING PIPELINE] → [VERIFICATION] → [ROUTING] → [OUTPUT]
                                      ↓
                                [LLM AGENTS]
                                      ↓
                              [SECOND OPINION]
```

---

## Component Boxes (Color-Coded)

### 🟦 INPUT - Data Sources (Light Blue)
**Box 1: Payment Gateway**
- **Label:** Razorpay Export
- **Content:**
  - 550 online payment records
  - Order ID, Customer, Amount, Status
  - Captured timestamp
- **Example:**
  ```
  order_Nx1V4TxDK
  Deepa Patel
  Rs.2,154.58
  captured: 2026-03-02
  ```

**Box 2: Bank Statement** 
- **Label:** Bank Settlement Data
- **Content:**
  - 500 bank deposit records
  - Settlement amount, Date, Narration
  - No shared key with payment gateway
- **Example:**
  ```
  UTR: HDFC8372910
  Rs.2,109.16
  settled: 2026-03-04
  narration: "FITZONE WELLNESS PVT LTD"
  ```

**Box 3: Internal Ledger**
- **Label:** Accounting System
- **Content:**
  - 510 ledger entries
  - Order ID, Customer, Notes, Amount
  - Shared key: Order ID
- **Example:**
  ```
  order_Nx1V4TxDK
  Deepa Patel
  Rs.2,154.58
  notes: "Monthly gym membership renewal"
  ```

---

### 🟩 PROCESSING - Ingestion Agent (Green)
**Agent 1: Data Ingestion & Normalization**
- **Label:** Agent 1 - Ingestion
- **Content:**
  - Loads 3 CSV files
  - Validates data quality
  - Standardizes formats (dates, amounts, text)
  - Creates canonical records
- **Tech Stack:**
  - Python + Pandas
  - Pydantic validation
- **Output:** 
  - 1560 canonical records
  - 0 failures

**Arrow Label:** "Normalized Data" →

---

### 🟨 MATCHING - Exact Match Agent (Yellow)
**Agent 2: Exact Match (Regex)**
- **Label:** Agent 2 - Perfect Match
- **Confidence:** 95-98%
- **Content:**
  - Searches for order ID in bank narration
  - Uses regex pattern matching
  - No LLM needed
- **Example Case:**
  ```
  Razorpay: order_Nx1V4TxDK
  Bank narration: "IMPS RZP order_Nx1V4TxDK"
  ✅ MATCH FOUND
  ```
- **Result:** 
  - 510 pairs matched
  - 15 unmatched → continue to Agent 3

**Arrow Label:** "Unmatched (15 records)" →

---

### 🟧 MATCHING - Fuzzy Match Agent (Orange)
**Agent 3: Fuzzy Match (Amount + Date)**
- **Label:** Agent 3 - Approximate Match
- **Confidence:** 85-94%
- **Content:**
  - Matches on amount (±Rs.0.01)
  - Matches on date (±3 days)
  - Calculates confidence score
- **Example Case:**
  ```
  Razorpay: Rs.2,154.58 on Mar 2
  Bank: Rs.2,109.16 on Mar 4 (2 days later)
  Expected: Rs.2,109.16 (after fees)
  ✅ Amount: Perfect match
  ✅ Date: 2-day lag (normal)
  Confidence: 92%
  ```
- **Result:**
  - 395 auto-matched (confidence ≥85%)
  - 65 uncertain → continue to Agent 4

**Arrow Label:** "Uncertain Matches (65 records)" →

---

### 🟥 AI REASONING - LLM Agent (Red)
**Agent 4: AI Semantic Matching**
- **Label:** Agent 4 - LLM Reasoning
- **Confidence:** 75-90%
- **Content:**
  - Uses Groq (llama-3.3-70b)
  - Understands merchant aliases
  - Handles delayed settlements (5-9 days)
  - Interprets garbled narrations
- **Tech Stack:**
  - LLM: Groq llama-3.3-70b-versatile
  - Structured output (Pydantic)
  - Cached responses (SQLite)

**Example Cases:**

**Case A: Delayed Settlement**
```
Razorpay: Rs.2,154.58 on Jan 10
Bank: Rs.2,109.16 on Jan 17 (7 days later)
Narration: "NEFT SETL 189"

LLM Analysis:
"7-day lag is within normal 1-10 day NEFT window.
Amount matches perfectly after fee deduction.
This is a valid delayed settlement."

Decision: match
Confidence: 0.87
Risk flags: ["delayed_settlement_valid"]
```

**Case B: Semantic Brand Narration**
```
Ledger notes: "Monthly gym membership renewal"
Bank narration: "FITZONE WELLNESS PVT LTD"
Merchant profile: Brand="FitZone Gym", 
                  Legal="FITZONE WELLNESS PVT LTD"

LLM Analysis:
"Bank shows merchant's registered legal name
from merchant profile. Ledger notes describe
a gym membership, consistent with fitness business.
This is the merchant's own settlement."

Decision: match
Semantic similarity: 0.95
Confidence: 0.90
```

**Case C: Garbled Narration**
```
Razorpay: Rs.5,842.87 on Feb 15
Bank: Rs.5,702.00 on Feb 16 (1 day later)
Narration: "PG SETL 189" (generic code, no merchant info)

LLM Analysis:
"Amount matches after fees. Timing is normal.
Narration provides no confirmation but no
contradiction either. Conservative match."

Decision: match
Confidence: 0.78
Risk flags: ["garbled_narration"]
```

- **Result:**
  - 65 decisions made
  - 50 matches, 10 uncertain, 5 no-match
  - All proceed to Agent 5

**Arrow Label:** "For Verification (65 records)" →

---

### 🟪 VERIFICATION - Second Opinion Agent (Purple)
**Agent 5: Independent Review**
- **Label:** Agent 5 - Second Opinion
- **Confidence:** Cross-validated
- **Content:**
  - Independent LLM call (same model)
  - Does NOT see Agent 4's reasoning
  - Makes fresh decision from raw data
  - Checks for disagreements
- **Skip Logic:**
  ```
  Skip if: confidence ≥95% AND amount <Rs.10,000
  Always verify: High-value OR uncertain
  ```

**Example: Agreement**
```
Agent 4: "match", confidence: 0.87
Agent 5: "match", confidence: 0.85
Result: AGREE ✅
Combined confidence: 0.86
Status: MATCHED
```

**Example: Disagreement**
```
Agent 4: "match", confidence: 0.78
Agent 5: "uncertain", confidence: 0.55
Result: DISAGREE ❌
Combined confidence: 0.0
Status: UNRESOLVED (needs human review)
```

- **Result:**
  - 50 agreements → AUTO-APPROVED
  - 15 disagreements → NEEDS REVIEW

**Arrow Label:** "Final Decisions (65 records)" →

---

### 🟦 CLASSIFICATION - Router Agent (Light Blue)
**Agent 6 & 7: Decision Router**
- **Label:** Router - Classification & Explanation
- **Content:**
  - Routes to 3 categories:
    - AUTO-APPROVED (MATCHED)
    - IN PROGRESS (PARTIAL)
    - NEEDS REVIEW (UNRESOLVED)
  - Generates plain English explanations
  - Adds confidence scores to headlines

**Category Breakdown:**

**🟢 AUTO-APPROVED (96 records)**
Examples:
- ✅ Perfect match found (order ID in narration)
- ✅ Amount and date match within normal lag
- ✅ Merchant name verified in bank statement
- ✅ AI agents both confirmed match

**🟡 IN PROGRESS (8 records)**
Examples:
- ⏳ Waiting for bank settlement (5 records)
  - Payment captured, but deposit not yet arrived
  - Within normal 1-10 day window
- ⏳ Payment failed - no action needed (3 records)
  - Card declined, no money moved
  - Nothing to reconcile

**🔴 NEEDS REVIEW (6 records)**
Examples:
- ⚠️ AI agents disagreed (2 records)
- ⚠️ Low confidence match (1 record)
- ⚠️ Unidentified bank credit (3 records)
  - Money received with no matching payment
  - Could be refund, interest, or error

---

### 📊 OUTPUT - Reporting & Analytics (Multi-color)

**Agent 8: Report Generator**
- **Label:** Agent 8 - Reporting
- **Content:**
  - Generates JSON report
  - Summary statistics
  - Sub-reason breakdown
  - Cash flow forecast
- **Output:**
  ```json
  {
    "total_records": 110,
    "matched": 96,
    "partial": 8,
    "unresolved": 6,
    "match_rate": "87.3%",
    "llm_calls": 25,
    "processing_time": "1.2s"
  }
  ```

**Agent 9: Q&A System**
- **Label:** Agent 9 - Natural Language Q&A
- **Content:**
  - ChromaDB vector database
  - sentence-transformers embeddings
  - Semantic search over all transactions
  - LLM-powered answers
- **Tech Stack:**
  - Vector DB: ChromaDB (local, no API key)
  - Embeddings: all-MiniLM-L6-v2 (CPU)
  - Query LLM: Groq llama-3.3-70b
- **Example Query:**
  ```
  Q: "Are there any gym membership payments?"
  
  A: "Yes, there are gym membership payments
      recorded: Rs.2,154.58 on 2026-03-02,
      Rs.2,222.80 on 2026-03-25..."
  
  Retrieved Records: 5 (with metadata)
  ```

**Cash Flow Forecast (NEW)**
- **Label:** Cash Flow Prediction
- **Content:**
  - Median settlement lag: 2 days
  - Pending settlements: 5
  - Expected inflow (7 days): Rs.X,XXX
  - Expected inflow (30 days): Rs.XX,XXX
- **Algorithm:**
  - Computes median lag from MATCHED records
  - Forecasts based on pending settlements
  - Uses AS_OF_DATE (never datetime.now())

---

### 🌐 FRONTEND - User Interface (Cyan)

**Dashboard**
- **Label:** React Dashboard
- **Content:**
  - Summary stats
  - Stage breakdown (Exact: 68, Fuzzy: 14, LLM: 14)
  - Match rate visualization
  - Processing time metrics

**Review Queue**
- **Label:** Transaction Review List
- **Content:**
  - Filterable by status (MATCHED/PARTIAL/UNRESOLVED)
  - Sortable by confidence, amount, date
  - One-line plain English categories
  - Confidence progress bars

**Record Detail**
- **Label:** Detailed Explanation View
- **Content:**
  - Headline with confidence
  - Checklist (what passed/failed)
  - Risk flags
  - Recommendation
  - Full transaction data

**Q&A Chat**
- **Label:** Natural Language Query
- **Content:**
  - Ask questions in plain English
  - Real-time semantic search
  - LLM-generated answers
  - Retrieved record cards

---

## Data Flow Arrows

### Arrow 1: CSV Files → Ingestion
- **Label:** "3 CSV Files (1,560 rows)"
- **Color:** Blue

### Arrow 2: Ingestion → Exact Match
- **Label:** "Normalized Records (312 canonical)"
- **Color:** Green

### Arrow 3: Exact Match → Fuzzy Match
- **Label:** "Unmatched (15 Razorpay records)"
- **Color:** Yellow

### Arrow 4: Fuzzy Match → LLM Reasoning
- **Label:** "Uncertain Matches (65 records, confidence <85%)"
- **Color:** Orange

### Arrow 5: LLM Reasoning → Second Opinion
- **Label:** "For Verification (65 decisions)"
- **Color:** Red

### Arrow 6: Second Opinion → Router
- **Label:** "Verified Decisions (50 agree, 15 disagree)"
- **Color:** Purple

### Arrow 7: Router → Frontend
- **Label:** "Categorized Results (96/8/6)"
- **Color:** Light Blue

### Arrow 8: All Agents → Report Generator
- **Label:** "Aggregated Statistics"
- **Color:** Gray (dashed)

### Arrow 9: Report Generator → Q&A System
- **Label:** "Indexed Records (ChromaDB)"
- **Color:** Cyan

### Arrow 10: Frontend ↔ Backend API
- **Label:** "GET /api/summary, POST /api/qa"
- **Color:** Green (bidirectional)

---

## Statistics Box (Bottom Right)

**Pipeline Performance**
```
┌─────────────────────────────┐
│  PERFORMANCE METRICS        │
├─────────────────────────────┤
│  Total Records:     110     │
│  Processing Time:   1.2s    │
│  ────────────────────────   │
│  Exact Match:       68      │
│  Fuzzy Auto:        14      │
│  LLM Verified:      14      │
│  Direct Routing:    14      │
│  ────────────────────────   │
│  No-LLM Resolution: 77%     │
│  LLM Calls Made:    25      │
│  Cache Hit Rate:    90%     │
│  ────────────────────────   │
│  Match Rate:        87.3%   │
│  Needs Review:      5.5%    │
└─────────────────────────────┘
```

---

## Technology Stack Box (Bottom Left)

**Tech Stack**
```
┌─────────────────────────────┐
│  BACKEND                    │
├─────────────────────────────┤
│  • Python 3.11+             │
│  • FastAPI (REST API)       │
│  • Pandas (CSV processing)  │
│  • Pydantic (validation)    │
│  • ChromaDB (vector DB)     │
│  • SQLite (audit + cache)   │
│  ────────────────────────   │
│  AI/ML                      │
│  • Groq (LLM provider)      │
│  • llama-3.3-70b-versatile  │
│  • sentence-transformers    │
│  • all-MiniLM-L6-v2         │
│  ────────────────────────   │
│  FRONTEND                   │
│  • React 18                 │
│  • Vite (build tool)        │
│  • CSS Modules              │
│  • Fetch API                │
└─────────────────────────────┘
```

---

## Decision Tree (Side Panel)

```
TRANSACTION RECONCILIATION FLOW
═══════════════════════════════

1. Order ID in bank narration?
   YES → Agent 2: MATCHED (98%)
   NO → Continue to 2

2. Amount + Date match?
   YES, Confidence ≥85% → Agent 3: MATCHED (90%)
   YES, Confidence <85% → Continue to 3
   NO → Continue to 5

3. LLM Analysis (Agent 4)
   Delayed settlement? → Check if within 1-10 days
   Merchant alias? → Check against profile
   Garbled narration? → Weigh other signals
   ↓
   Decision + Confidence → Continue to 4

4. Second Opinion (Agent 5)
   Skip if: Confidence ≥95% AND Amount <Rs.10k
   Otherwise: Independent review
   ↓
   Agree? → MATCHED
   Disagree? → UNRESOLVED

5. No Match Found
   Bank record exists? NO → PARTIAL (awaiting)
   Bank record exists? YES, unexplained → UNRESOLVED
```

---

## Example Case: End-to-End Flow

**Transaction: Gym Membership Payment**

```
┌─────────────────────────────────────────────────┐
│  INPUT DATA                                      │
├─────────────────────────────────────────────────┤
│  Razorpay:                                       │
│    order_Nx1V4TxDK                               │
│    Deepa Patel                                   │
│    Rs.2,154.58 (captured: Mar 2)                 │
│                                                  │
│  Bank Statement:                                 │
│    UTR: HDFC8372910                              │
│    Rs.2,109.16 (settled: Mar 4)                  │
│    "FITZONE WELLNESS PVT LTD NEFT"               │
│                                                  │
│  Internal Ledger:                                │
│    order_Nx1V4TxDK                               │
│    Deepa Patel                                   │
│    Rs.2,154.58                                   │
│    "Monthly gym membership renewal"              │
└─────────────────────────────────────────────────┘

                      ↓

┌─────────────────────────────────────────────────┐
│  AGENT 2: EXACT MATCH                            │
├─────────────────────────────────────────────────┤
│  ❌ Order ID "order_Nx1V4TxDK" NOT in bank       │
│     narration "FITZONE WELLNESS PVT LTD NEFT"    │
│  → Continue to Fuzzy Match                       │
└─────────────────────────────────────────────────┘

                      ↓

┌─────────────────────────────────────────────────┐
│  AGENT 3: FUZZY MATCH                            │
├─────────────────────────────────────────────────┤
│  ✅ Amount: Rs.2,109.16 (after fees) - Perfect   │
│  ✅ Date: 2-day lag (Mar 2 → Mar 4) - Normal     │
│  ❓ Text: "Deepa Patel" vs "FITZONE..."  - 0%    │
│                                                  │
│  Composite Confidence: 82%                       │
│  ⚠️  Below 85% threshold                         │
│  → Send to LLM                                   │
└─────────────────────────────────────────────────┘

                      ↓

┌─────────────────────────────────────────────────┐
│  AGENT 4: LLM REASONING                          │
├─────────────────────────────────────────────────┤
│  Prompt:                                         │
│  "Ledger notes: 'Monthly gym membership renewal' │
│   Bank narration: 'FITZONE WELLNESS PVT LTD'    │
│   Merchant profile: Brand='FitZone Gym',         │
│                     Legal='FITZONE WELLNESS...'  │
│   Amount matches, 2-day lag is normal."          │
│                                                  │
│  LLM Response:                                   │
│  "Bank narration shows merchant's registered     │
│   legal name from profile. Ledger notes describe │
│   gym membership, consistent with fitness        │
│   business. This is the merchant's own          │
│   settlement."                                   │
│                                                  │
│  Decision: match                                 │
│  Semantic Similarity: 0.95                       │
│  Confidence: 0.90                                │
│  → Send to Second Opinion                        │
└─────────────────────────────────────────────────┘

                      ↓

┌─────────────────────────────────────────────────┐
│  AGENT 5: SECOND OPINION                         │
├─────────────────────────────────────────────────┤
│  Skip Check:                                     │
│    Confidence (0.90) ≥ 0.95? NO                  │
│    Amount (2,154.58) < 10,000? YES               │
│  → Run verification (confidence not high enough) │
│                                                  │
│  Independent Analysis (same data, fresh view):   │
│  "Amount perfect, merchant legal name confirmed, │
│   2-day lag acceptable. Strong match."           │
│                                                  │
│  Decision: match                                 │
│  Confidence: 0.88                                │
│  Agrees with Agent 4? YES ✅                     │
│                                                  │
│  Combined Confidence: 0.89                       │
└─────────────────────────────────────────────────┘

                      ↓

┌─────────────────────────────────────────────────┐
│  ROUTER: FINAL CLASSIFICATION                    │
├─────────────────────────────────────────────────┤
│  Status: MATCHED                                 │
│  Sub-reason: semantic_brand_narration            │
│  Confidence: 89%                                 │
│  Category: AUTO-APPROVED                         │
│                                                  │
│  Headline:                                       │
│  "AUTO-APPROVED — Merchant name verified in      │
│   bank statement — 89% sure this is correct"     │
│                                                  │
│  Checklist:                                      │
│  ✅ Amount matches perfectly after fees          │
│  ✅ Bank shows your registered business name     │
│  ✅ Deposit arrived 2 days after payment         │
│  ✅ Independently verified by second check (88%) │
│                                                  │
│  Recommendation:                                 │
│  "No action needed — this is a confirmed match"  │
│                                                  │
│  Risk Flags: None                                │
└─────────────────────────────────────────────────┘

                      ↓

┌─────────────────────────────────────────────────┐
│  OUTPUT: DASHBOARD & REPORT                      │
├─────────────────────────────────────────────────┤
│  Display in Review Queue:                        │
│    Deepa Patel | Rs.2,154.58 | Mar 4             │
│    [●●●●●●●●●○] 89%                             │
│    "Merchant name verified in statement"         │
│                                                  │
│  Searchable in Q&A:                              │
│    Q: "gym membership payments?"                 │
│    A: "Found 3... Rs.2,154.58 on Mar 2 (Deepa   │
│        Patel - Monthly renewal)"                 │
│                                                  │
│  Included in Cash Flow Forecast:                 │
│    Median settlement lag: 2 days                 │
│    (This record contributed to that statistic)   │
└─────────────────────────────────────────────────┘
```

---

## Color Legend

- 🟦 **Light Blue:** Input sources, Classification
- 🟩 **Green:** Ingestion, Data processing
- 🟨 **Yellow:** Deterministic matching (Exact)
- 🟧 **Orange:** Statistical matching (Fuzzy)
- 🟥 **Red:** AI/LLM reasoning
- 🟪 **Purple:** Verification, Validation
- 🟫 **Brown:** Reporting, Analytics
- 🟩 **Cyan:** User interface, Q&A
- ⚫ **Gray:** System boundaries, APIs

---

## Visual Style Guide

**For Diagram Tool (Draw.io, Figma, Miro, etc.):**

1. **Use rounded rectangles** for agent boxes
2. **Use zigzag borders** for AI/LLM agents (Agents 4, 5)
3. **Use dashed lines** for optional/conditional flows
4. **Use thick solid arrows** for primary data flow
5. **Use thin arrows** for metadata/logging
6. **Add shadows** to make boxes pop
7. **Use monospace font** for code/data examples
8. **Group related agents** with light background boxes

**Box Dimensions:**
- Width: 200-250px
- Height: 150-200px (adjust for content)
- Padding: 15px
- Border radius: 10px
- Font size: 10-12pt for content, 14-16pt for titles

**Arrow Styling:**
- Width: 3-4px for primary flow
- Arrowhead: Solid triangle
- Labels: 8-10pt, placed above arrow

**Background:**
- Dark (#1a1a1a) or Light (#f5f5f5)
- Use subtle grid pattern

---

## Ready-to-Use Sections

You can copy these text blocks directly into your diagram tool:

### Agent 2 Box Text:
```
Agent 2: Exact Match
━━━━━━━━━━━━━━━━━━━━
• Searches for order ID in bank narration
• Uses regex: r'order_[A-Za-z0-9]+'
• No AI needed - deterministic

Example:
  Bank: "IMPS RZP order_Nx1V4TxDK"
  ✓ Matches: order_Nx1V4TxDK
  
Confidence: 95-98%
Result: 510 matched, 15 continue →
```

### Agent 4 Box Text:
```
Agent 4: LLM Reasoning
━━━━━━━━━━━━━━━━━━━━
🤖 Groq llama-3.3-70b-versatile

Handles:
• Delayed settlements (5-9 days)
• Merchant name mismatches
• Garbled bank narrations

Example:
  "Bank shows legal name 'FITZONE
   WELLNESS PVT LTD' - matches
   merchant profile. Ledger notes
   'gym membership' consistent."
  
  Decision: match (0.90)
  
Result: 65 decisions → verify
```

This specification provides everything needed to create a professional, informative architecture diagram similar to the GenW.AI reference!
