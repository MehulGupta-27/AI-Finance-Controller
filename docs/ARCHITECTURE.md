# Architecture & Data Flow

## Overview

The pipeline runs 9 agents in sequence. Each agent takes a well-defined input, does one job, and passes its output to the next. No agent loops back or calls another agent directly — the flow is strictly linear.

```
Raw CSV Files
     │
     ▼
[Agent 1]  Ingestion — parse, validate, normalize all three CSVs into canonical records
     │
     ▼
[Agent 2]  Exact Match — match Ledger ↔ Razorpay by order_id (zero ambiguity)
     │
     ▼
[Agent 3]  Fuzzy Match — match Razorpay ↔ Bank by amount + date + text scoring
     │                   Hungarian algorithm guarantees optimal one-to-one assignment
     ├─ score >= 0.90 ──────────────────────────────────────────► MATCHED (auto)
     ├─ 0.50 <= score < 0.90 ──────────────────────────────────► Agent 4
     └─ score < 0.50 ──────────────────────────────────────────► Agent 6 (direct)
          │
          ▼
[Agent 4]  LLM Reasoning — GPT-20b reasons over ambiguous pairs
     │                      uses merchant profile + all 25 fields
     │                      decision: match / no_match / uncertain
     │
     ▼
[Agent 5]  Verifier — GPT-120b independently re-examines same data
     │                 agrees → combined confidence used
     │                 disagrees → UNRESOLVED (safe failure)
     │                 skip if: confidence >= 0.95 AND amount < Rs.10,000
     │
     ▼
[Agent 6]  Classifier — applies business rules, assigns final status
     │                   MATCHED / PARTIAL / UNRESOLVED
     │                   generates plain-English explanation for every record
     │
     ▼
[Agent 7]  Reporting — builds PipelineRunResult summary + record identity check
     │
     ▼
[Agent 8]  Q&A (ChromaDB) — indexes all records for semantic search queries
     │
     ▼
[Agent 9]  Cash Flow Forecast — predicts pending settlement inflows (next 7/30 days)
```

---

## Agent 1 — Ingestion & Normalization

**File:** `agents/core/ingestion_agent.py`

**What it does:** Reads all three CSV files, validates every row, and converts each record into a unified `CanonicalRecord` format. Strips whitespace, normalizes date formats, coerces amounts to float, flags any rows that fail validation.

**Why it matters:** Downstream agents don't deal with raw CSV quirks. They all work on the same clean data structure with consistent field names.

**CanonicalRecord fields (common to all sources):**
- `record_id` — unique ID (ledger_id / rzp_payment_id / utr_number)
- `source` — `"ledger"` / `"razorpay"` / `"bank"`
- `source_ref` — original ID from the source file
- `order_id` — links ledger ↔ Razorpay (blank for bank records)
- `amount` — normalized float (Rs.)
- `date` — normalized Python `date` object
- `text_field` — customer name (ledger), payment method (Razorpay), narration (bank)
- `status` — paid / captured / failed / partially_refunded
- `notes` — free-text from ledger (e.g. "gym membership - annual plan")
- `raw` — original row dict for fields like `rzp_fee`, `refund_amount`

**Example:**
```
Input (ledger CSV row):
  LED23434, ORD770487, Rahul Sharma, 5984.09, INR, 2026-02-01, card, paid, 0.0, ""

Output (CanonicalRecord):
  record_id  = "LED23434"
  source     = "ledger"
  order_id   = "ORD770487"
  amount     = 5984.09
  date       = 2026-02-01
  text_field = "Rahul Sharma"
  status     = "paid"
```

---

## Agent 2 — Exact Match

**File:** `agents/core/exact_match_agent.py`

**What it does:** Matches Ledger records to Razorpay records using `order_id` as the key. This is a deterministic lookup — if the order_id matches, it's a confirmed pair. No scoring, no ambiguity.

**Also handles:**
- `duplicate_capture` — same order_id appears twice in Razorpay (one captured, one failed). Keeps the captured one, marks the failed one as handled.
- `failed_payment_orphan` — both ledger and Razorpay show `failed`. No bank deposit ever expected. Routes immediately to MATCHED (no action needed).

**Output:**
- `matched_pairs` — confirmed Ledger+Razorpay pairs (still need bank match from Agent 3)
- `unmatched_rzp` — Razorpay rows with no ledger counterpart (`missing_from_ledger`)
- `all_bank` — all bank records, untouched, passed through for Agent 3

**Example:**
```
Ledger  : LED23434  order_id=ORD770487  amount=Rs.5,984.09
Razorpay: pay_d0ed  order_id=ORD770487  amount=Rs.5,984.09

→ Exact match on ORD770487 → matched_pair confirmed
  (bank match still needed — goes to Agent 3)
```

**Failed payment example:**
```
Ledger  : LED99001  order_id=ORD999  status=failed
Razorpay: pay_xxxx  order_id=ORD999  status=failed

→ Both failed → no bank deposit ever sent → MATCHED (no_action_needed)
  Agent 3 never runs on this pair.
```

---

## Agent 3 — Fuzzy Match

**File:** `agents/core/fuzzy_match_agent.py`

**What it does:** Takes all Razorpay records that have a confirmed ledger pair (from Agent 2) and finds their corresponding bank settlement. Uses three scoring components combined into a composite score.

**Scoring:**

| Component | Weight | How it works |
|---|---|---|
| Amount score | 0.70 | 1.0 if `|expected - bank| <= Rs.5`, decays to 0 at Rs.20 diff |
| Date score | 0.20 | 1.0 same day, decays linearly to 0 at 10 days lag |
| Text score | 0.10 | rapidfuzz character similarity (customer name vs narration) |

`expected = rzp_amount - rzp_fee - refund_amount`

**Hungarian algorithm:** scipy's `linear_sum_assignment` solves the global optimal one-to-one assignment. This prevents greedy errors where a strong candidate "steals" a bank record from a slightly better match elsewhere.

**Routing thresholds:**
```
score >= 0.90                → AUTO-MATCHED (no LLM needed)
score 0.50–0.89              → send to Agent 4 (LLM)
score < 0.50                 → no candidate → Agent 6 (PENDING or UNRESOLVED)
merchant name in narration   → force to Agent 4 regardless of score
```

**Example — auto match:**
```
Razorpay: pay_d0ed  Rs.5,984.09  fee=Rs.141.22  date=2026-02-01
Expected settlement: Rs.5,842.87

Bank: UTR914655  Rs.5,842.87  date=2026-02-02  narration='PG SETL 189'

amount_score = 1.00  (exact match)
date_score   = 0.90  (1 day lag)
text_score   = 0.10  ('PG SETL 189' vs 'Rahul Sharma' — no overlap)
composite    = 0.70×1.00 + 0.20×0.90 + 0.10×0.10 = 0.89 → LLM candidate

(If composite were 0.91 → auto-matched immediately, no LLM)
```

**Example — partial refund:**
```
Razorpay: Rs.2,154.58  fee=Rs.50.88  ledger_status=partially_refunded
Refund amount (from ledger): Rs.500.00
Expected settlement: 2154.58 - 50.88 - 500.00 = Rs.1,603.70

Bank: Rs.1,603.70 → amount_score=1.00 → auto-matched
```

---

## Agent 4 — LLM Reasoning

**File:** `agents/core/llm_reasoning_agent.py`  
**Model:** `openai/gpt-oss-20b` via Groq

**What it does:** Handles the ambiguous 10–20% of records that fuzzy match couldn't confidently resolve. Sends the full context of a candidate pair to an LLM and gets a structured decision back.

**Data it sees per pair (~25 fields):**
- Merchant profile: brand name, registered legal name, narration aliases
- Razorpay: payment ID, amount, fee, date, order ID, status
- Bank: UTR, amount, date, narration
- Ledger: customer name, notes, amount, status, refund amount
- Computed: expected settlement, amount diff, lag days
- Fuzzy scores: amount, date, text, composite

**Output schema (Agent4Result):**
- `decision` — `match` / `no_match` / `uncertain`
- `confidence` — 0.0–1.0
- `semantic_similarity` — 0.0–1.0 (do these describe the same real-world event?)
- `reasoning` — one-sentence explanation
- `risk_flags` — list of concerns (e.g. `["high_value", "delayed_settlement"]`)

**Key rules baked into the prompt:**
- False match is worse than honest uncertain — be conservative
- Lag 1–10 days is valid; 5–9 days is "delayed but valid"
- Generic narration codes (IMPS, UPI, PG SETL) carry no signal either way
- If narration contains a merchant alias → strong positive signal

**What Agent 4 CAN resolve:**

| Scenario | Example |
|---|---|
| Merchant alias in narration | Bank: `'SETL/FZ WELLNESS/032826'` → matches narration_alias `FZ WELLNESS` |
| Garbled narration | Bank: `'IMPS/9284/RAJSH'` → consistent with customer `Rajesh Kumar` |
| Delayed settlement | Razorpay: Mar 20 → Bank: Mar 28 (8 days) → "delayed but valid" |
| Partial refund + garbled narration | Amount correct after subtracting refund, narration abbreviated |
| High-value confirmation | Rs.85,000 transaction forced through LLM even if fuzzy score = 0.92 |
| Generic UPI code | `UPI/9284726/ref` carries no text signal — LLM decides on amount+date alone |

**What Agent 4 CANNOT resolve:**

| Scenario | Why |
|---|---|
| Split payments (1 Rzp → 2 bank deposits) | Only sees one-to-one pairs; multi-leg never constructed |
| Batch settlements (N Rzp → 1 bank) | Same — pairing is always one-to-one |
| Amount diff > Rs.20 | Pre-filtered by Agent 3; no candidate pair ever built |
| Bank arrives after 10 days | Pre-filtered by Agent 3 date window |
| Missing bank record | Nothing to pair; goes directly to PENDING_SETTLEMENT |
| Unidentified bank credit | No Razorpay anchor; Agent 4 never involved |

---

## Agent 5 — Verifier

**File:** `agents/core/verifier_agent.py`  
**Model:** `openai/gpt-oss-120b` via Groq (stronger model than Agent 4)

**What it does:** Independently re-examines the same candidate pair that Agent 4 just decided on. It does NOT see Agent 4's reasoning — only the raw data. This prevents rubber-stamping.

**Skip condition (both must be true to skip):**
- `confidence >= 0.95` AND
- `amount < Rs.10,000`

High-value transactions are **always** verified regardless of confidence. Mid-confidence records are **always** verified regardless of amount.

**Agreement logic:**
```
Agent 4 says: match (conf=0.88)
Agent 5 says: match (conf=0.91)
→ AGREE → combined_confidence = geometric mean ≈ 0.896 → MATCHED

Agent 4 says: match (conf=0.75)
Agent 5 says: uncertain (conf=0.55)
→ DISAGREE → combined_confidence = 0.0 → UNRESOLVED
```

**LLM failure behavior:** If Agent 5's API call fails, it is treated as a disagreement → UNRESOLVED. The pipeline never crashes; it routes to a safe state.

---

## Agent 6 — Classifier

**File:** `agents/core/classifier_agent.py`

**What it does:** Takes every record — from exact match, fuzzy auto-match, LLM-verified, pending, missing, or unidentified — and assigns a final status with a human-readable explanation. This is pure deterministic business logic, no LLM.

**Final statuses:**

| Status | Sub-reason | Trigger |
|---|---|---|
| MATCHED | exact_match | Agent 2 order_id match |
| MATCHED | no_action_needed | Both ledger + Rzp show failed |
| MATCHED | fuzzy_auto_match | Agent 3 score >= 0.90 |
| MATCHED | llm_confirmed | Agents 4+5 agree |
| PARTIAL | pending_settlement | Rzp captured, no bank record yet |
| PARTIAL | missing_from_ledger | Rzp+Bank matched, no ledger row |
| UNRESOLVED | unidentified_bank_credit | Bank deposit with no Rzp counterpart |
| UNRESOLVED | agent_disagreement | Agents 4 and 5 gave different decisions |
| UNRESOLVED | no_candidates_found | No bank record within amount/date window |

**Explanation generation:** For every record, Agent 6 generates a structured `Explanation` object:
- `headline` — e.g. "MATCHED — 89% confidence"
- `checklist` — list of passed/failed checks with plain-English labels
- `risk_flags` — any concerns flagged by Agent 4
- `recommendation` — action text for PARTIAL/UNRESOLVED records

**Example (fuzzy match explanation):**
```
Headline  : MATCHED — 89% confidence
Checklist :
  ✓ Amount matches: Rs.5,842.87 vs Rs.5,842.87
  ✓ Bank deposit arrived 1 day after payment — normal timing
  ✗ Bank description 'PG SETL 189' doesn't match (matched on amount and date instead)
```

---

## Agent 7 — Reporting

**File:** `agents/core/reporting_agent.py`

**What it does:** Assembles the final `PipelineRunResult` — a summary object with matched/partial/unresolved IDs, total runtime, LLM call count, and token usage. Also enforces the **record identity invariant**: every input record ID must appear in exactly one output status. If any record is missing or duplicated, the pipeline halts immediately.

**Output summary example:**
```
Total records  : 110
MATCHED        : 87  (79%)
PARTIAL        : 14  (13%)
UNRESOLVED     :  9  ( 8%)

LLM calls made : 13
LLM tokens used: ~20,000
Runtime        : 2.1 min
```

---

## Agent 8 — Q&A (ChromaDB)

**File:** `agents/core/qa_agent.py`

**What it does:** After the pipeline finishes, indexes all reconciled records into ChromaDB using sentence-transformer embeddings. This enables natural-language queries against the full transaction history.

**What gets indexed per record:**
- Customer name
- Ledger notes (e.g. "gym membership - annual plan")
- Bank narration (e.g. "FITZONE WELLNESS PVT LTD")
- Status, amount, date, order ID

**Why bank narration matters for search:** A query like "show me gym membership payments" works because the bank narration "FITZONE WELLNESS PVT LTD" is indexed alongside the ledger note "gym membership". Without the narration, semantic search would miss merchant-name-based matches.

**Example queries it can answer:**
```
"Which payments are still pending?"
"Show all transactions above Rs.50,000"
"Find Razorpay payments that didn't match the bank"
"What was reconciled for Rohan Patel?"
"Are there any unidentified bank credits?"
```

---

## Agent 9 — Cash Flow Forecast

**File:** `agents/core/cashflow_agent.py`

**What it does:** Looks at all PARTIAL/pending_settlement records (payments captured by Razorpay but not yet settled to bank) and forecasts when the money will arrive, based on the median settlement lag observed from already-MATCHED records in the same run.

**Output (CashFlowForecast):**
- `median_settlement_lag_days` — computed from actual matched pairs in this run
- `pending_settlements` — list of pending records with expected settlement date
- `expected_inflow_next_7_days` — total Rs. expected to arrive within the next 7 days
- `expected_inflow_next_30_days` — total Rs. expected within 30 days

**Example:**
```
Run date   : 2026-03-31
Median lag from matched records: 3 days

Pending record:
  Customer   : Rohan Patel
  Amount     : Rs.1,905.38
  Captured   : 2026-03-29
  Expected   : 2026-04-01  (captured + 3 days)
  Days until : 1 day

Expected inflow next 7 days  : Rs.42,800.00
Expected inflow next 30 days : Rs.98,600.00
```

---

## Data Flow Summary

```
              internal_ledger.csv
              razorpay_export.csv      ← 3 CSV files in
              bank_statement.csv
                      │
              [Agent 1] Ingest → CanonicalRecord × N
                      │
              [Agent 2] Exact Match (order_id key)
                      │
                ┌─────┴──────┐
          matched_pairs    unmatched_rzp
                │               │
              [Agent 3] Fuzzy Match (amount + date + text)
                      │
          ┌───────────┼───────────┐
        auto        llm        no_candidate
       matched    candidates
          │           │           │
          │       [Agent 4]       │
          │       LLM Reason      │
          │           │           │
          │       [Agent 5]       │
          │       Verify          │
          │           │           │
          └───────────┴───────────┘
                      │
              [Agent 6] Classify
              MATCHED / PARTIAL / UNRESOLVED
              + plain-English explanation
                      │
              [Agent 7] Report
              PipelineRunResult + invariant check
                      │
              [Agent 8] Index → ChromaDB
                      │
              [Agent 9] Cash Flow Forecast
                      │
              API (FastAPI) → React Frontend
```

---

## Configuration

All tunable parameters live in `agents/utils/config.py`:

| Parameter | Value | Used by |
|---|---|---|
| `FUZZY_AUTO_MATCH_THRESHOLD` | 0.90 | Agent 3 — auto-match cutoff |
| `FUZZY_MIN_CANDIDATE_THRESHOLD` | 0.50 | Agent 3 — LLM routing cutoff |
| `AMOUNT_TOLERANCE_RUPEES` | Rs.5 | Agent 3 — amount window |
| `SETTLEMENT_DATE_TOLERANCE_DAYS` | 10 | Agent 3 — date window |
| `FUZZY_MATCH_WEIGHTS` | amount=0.70, date=0.20, text=0.10 | Agent 3 — score weights |
| `HIGH_VALUE_REVIEW_THRESHOLD_RUPEES` | Rs.50,000 | Agent 3/5 — force LLM + always verify |
| `SKIP_VERIFICATION_CONFIDENCE` | 0.95 | Agent 5 — skip threshold |
| `SKIP_VERIFICATION_MAX_AMOUNT` | Rs.10,000 | Agent 5 — skip amount cap |
| `LLM_CONFIDENCE_AUTO_CONFIRM` | 0.85 | Agent 6 — MATCHED threshold from LLM |
| `OVERDUE_SETTLEMENT_DAYS` | 10 | Agent 6 — when PARTIAL becomes overdue |
| `GROQ_REASONING_MODEL` | openai/gpt-oss-20b | Agent 4 |
| `GROQ_VERIFIER_MODEL` | openai/gpt-oss-120b | Agent 5 |
