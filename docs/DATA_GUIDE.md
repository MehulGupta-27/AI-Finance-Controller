# Data Guide — CSV Files, Fields & Linkage

## The Three Source Files

The pipeline reconciles three CSV files that each describe the same payment from a different perspective.

```
internal_ledger.csv          razorpay_export.csv          bank_statement.csv
────────────────────         ────────────────────         ──────────────────
What the business            What the payment             What the bank
recorded as sold             gateway captured             actually received

LED23434                     pay_d0ed9afde54547           UTR914655221101
ORD770487 ◄──── order_id ───► ORD770487                   (no order_id)
Rs.5,984.09                  Rs.5,984.09                  Rs.5,842.87
                             fee = Rs.141.22              ↑ amount - fee
2026-02-01                   2026-02-01                   2026-02-02
                                                          ↑ settled next day
```

### How they link together

```
internal_ledger  ──── order_id ────►  razorpay_export
                                             │
                                     amount - rzp_fee
                                       = settlement
                                             │
                                     matches within
                                      ±Rs.5 window
                                             │
                                             ▼
                                      bank_statement
                                    (linked by amount
                                     + date proximity,
                                     NOT by any shared ID)
```

**Key point:** The ledger and Razorpay share `order_id` — that is the hard link. The bank has **no shared ID** with either. The only way to match a bank record to a Razorpay record is by computing the expected settlement amount (`rzp_amount - rzp_fee`) and finding a bank deposit within ±Rs.5 that arrived within 10 days.

---

## File 1 — `internal_ledger.csv`

The business's own record of every order. Created at the time of sale. This is the source of truth for what was sold and to whom.

**Location:** `data/raw/internal_ledger.csv` (550 records) · `data/raw_100/internal_ledger.csv` (110 records)

### Fields

| Field | Type | Example | Description |
|---|---|---|---|
| `ledger_id` | string | `LED23434` | Unique ID for this ledger entry. Primary key for the whole pipeline — all final results are keyed to this ID. |
| `order_id` | string | `ORD770487` | Links this ledger row to Razorpay. The join key used by Agent 2 (Exact Match). |
| `customer_name` | string | `Rahul Sharma` | Name of the paying customer. Used by Agent 3 for text similarity scoring against bank narrations. |
| `amount` | float | `5984.09` | Amount the customer paid in Rs. Should match the Razorpay amount exactly. |
| `currency` | string | `INR` | Always INR. Present for completeness, not used in matching logic. |
| `order_date` | date | `2026-02-01` | Date the order was placed. Used as the baseline for settlement lag calculations. |
| `payment_method` | string | `card` | How the customer paid: `card`, `upi`, `netbanking`, `wallet`. |
| `status` | string | `paid` | Current state of the order. Drives routing logic — see status values below. |
| `refund_amount` | float | `796.97` | Amount refunded to the customer. Non-zero only for `partially_refunded` records. Subtracted from expected settlement by Agent 3. |
| `notes` | string | `Partial refund of ₹796.97 applied` | Free-text description of the transaction. Indexed into ChromaDB for Q&A search. Usually blank for standard transactions. |

### Status values

| Value | Meaning | Pipeline behaviour |
|---|---|---|
| `paid` | Normal successful payment | Standard reconciliation flow |
| `captured` | Payment confirmed by gateway | Same as paid — fully reconcilable |
| `failed` | Payment attempt failed | Paired with a `failed` Razorpay row → MATCHED (no_action_needed), no bank deposit expected |
| `partially_refunded` | Customer got a partial refund back | `refund_amount` is non-zero; Agent 3 computes `expected = amount - fee - refund_amount` |

### Sample rows

```
ledger_id, order_id,    customer_name,   amount,   status,              refund_amount, notes
LED23434,  ORD770487,  Rahul Sharma,    5984.09,  paid,                0.0,
LED12441,  ORD966589,  Pooja Mishra,    2357.50,  partially_refunded,  796.97,        Partial refund of ₹796.97 applied
```

---

## File 2 — `razorpay_export.csv`

Razorpay's record of every payment attempt. Exported from the Razorpay dashboard. Contains the gateway fee which is deducted before settlement reaches the bank.

**Location:** `data/raw/razorpay_export.csv` (550 records) · `data/raw_100/razorpay_export.csv` (110 records)

### Fields

| Field | Type | Example | Description |
|---|---|---|---|
| `rzp_payment_id` | string | `pay_d0ed9afde54547` | Razorpay's unique payment ID. Used as the record identifier for this source. |
| `order_id` | string | `ORD770487` | Links this payment to the internal ledger. The join key used by Agent 2. Same value appears in `internal_ledger.order_id`. |
| `amount` | float | `5984.09` | Amount the customer paid in Rs. Should exactly match `internal_ledger.amount` for the same order. |
| `currency` | string | `INR` | Always INR. Not used in matching logic. |
| `rzp_fee` | float | `141.22` | Razorpay's processing fee deducted before settlement. **Critical field** — `expected_bank_amount = amount - rzp_fee`. Agent 3 uses the actual column value, never recomputes it. |
| `captured_at` | date | `2026-02-01` | Date the payment was captured by Razorpay. Used as the start of the settlement lag window. |
| `method` | string | `card` | Payment method: `card`, `upi`, `netbanking`, `wallet`. Should match ledger's `payment_method`. |
| `status` | string | `captured` | Gateway status — see values below. |

### Status values

| Value | Meaning | Pipeline behaviour |
|---|---|---|
| `captured` | Payment successfully collected | Normal — expect a bank settlement within 1–10 days |
| `failed` | Payment was declined or dropped | No settlement ever sent; `rzp_fee = 0.0`; paired with failed ledger row → MATCHED (no_action_needed) |

### Sample rows

```
rzp_payment_id,       order_id,   amount,   rzp_fee,  captured_at,  method,      status
pay_d0ed9afde54547,  ORD770487,  5984.09,  141.22,   2026-02-01,   card,        captured
pay_0bc389aac71b48,  ORD988459,   299.61,    0.00,   2026-01-14,   wallet,      failed
```

### Computing expected bank amount

```
Standard:          expected = amount - rzp_fee
                   5984.09 - 141.22 = Rs.5,842.87

Partial refund:    expected = amount - rzp_fee - refund_amount
                   2357.50 - 55.62 - 796.97 = Rs.1,504.91
```

---

## File 3 — `bank_statement.csv`

The bank's record of every credit received. This is the ground truth for what money actually arrived. Bank narrations are often garbled, abbreviated, or generic — they do not reliably contain the customer name.

**Location:** `data/raw/bank_statement.csv` (550 records) · `data/raw_100/bank_statement.csv` (110 records)

### Fields

| Field | Type | Example | Description |
|---|---|---|---|
| `utr_number` | string | `UTR914655221101` | Unique Transaction Reference — the bank's ID for this credit. Used as the record identifier for this source. |
| `settlement_amount` | float | `5842.87` | Amount credited to the bank account in Rs. Should match `razorpay.amount - razorpay.rzp_fee` for a normal payment. |
| `settlement_date` | date | `2026-02-02` | Date the money arrived in the bank. Typically 1–3 days after `razorpay.captured_at`, but can be up to 10 days. |
| `narration` | string | `PG SETL 189` | Bank's description of the transaction. Often a generic code like `PG SETL`, `IMPS`, `UPI/ref`. Sometimes contains the merchant's registered legal name (e.g. `FITZONE WELLNESS PVT LTD`). This is the hardest field to work with. |
| `bank_ref_type` | string | `RTGS` | Transfer type: `RTGS`, `IMPS`, `UPI`, `NEFT`. Informational only — not used in matching logic. |

### Narration patterns

The `narration` field is the most variable and problematic field in the dataset. Examples seen in the data:

| Narration | What it means | Matched by |
|---|---|---|
| `PG SETL 189` | Generic payment gateway settlement code | Amount + date only |
| `IMPS 818` | IMPS transfer with a reference number | Amount + date only |
| `UPI/331148` | UPI transfer with order fragment | Amount + date + partial text |
| `FITZONE WELLNESS PVT LTD` | Merchant's registered legal name | Merchant alias lookup in Agent 4 |
| `SETL/FZ WELLNESS/032826` | Abbreviated merchant alias | Merchant alias lookup in Agent 4 |

Generic codes (`PG SETL`, `IMPS`, `UPI`) carry no customer-specific signal — Agent 4 explicitly treats them as neutral (neither confirming nor denying). The narration only becomes a positive signal when it contains a known merchant alias.

### Sample rows

```
utr_number,       settlement_amount,  settlement_date,  narration,    bank_ref_type
UTR914655221101,  5842.87,            2026-02-02,       PG SETL 189,  RTGS
UTR268697172873,  2313.96,            2026-01-02,       UPI/331148,   UPI
```

---

## Cross-File Linkage — Worked Example

Here is one complete transaction traced through all three files:

**Customer Rohan Patel pays Rs.1,905.38 for a UPI order on 6 March 2026.**

### Step 1 — internal_ledger.csv
```
ledger_id     = LED83563
order_id      = ORD133326
customer_name = Rohan Patel
amount        = 1905.38
order_date    = 2026-03-06
payment_method= upi
status        = paid
refund_amount = 0.0
notes         = (blank)
```

### Step 2 — razorpay_export.csv
```
rzp_payment_id = pay_6464c7b9d4e544
order_id       = ORD133326          ← same order_id → Agent 2 joins these
amount         = 1905.38
rzp_fee        = 44.97
captured_at    = 2026-03-06
method         = upi
status         = captured
```

### Step 3 — Compute expected settlement
```
expected = 1905.38 - 44.97 = Rs.1,860.41
```

### Step 4 — bank_statement.csv
```
utr_number        = UTR886833016361
settlement_amount = 1860.41          ← matches expected exactly
settlement_date   = 2026-03-09       ← 3 days after capture (normal)
narration         = IMPS 818         ← generic code, no customer signal
bank_ref_type     = RTGS
```

### Step 5 — Scoring (Agent 3)
```
amount_score = 1.00   (Rs.1,860.41 exact match)
date_score   = 0.70   (3 days lag → 1.0 - 3/10 = 0.70)
text_score   = 0.05   ('IMPS 818' vs 'Rohan Patel' — near zero)
composite    = 0.70×1.00 + 0.20×0.70 + 0.10×0.05 = 0.845

→ 0.50 <= 0.845 < 0.90 → sent to Agent 4 (LLM)
```

### Step 6 — Agent 4 decision
```
reasoning  : "Amount matches expected settlement exactly; 3-day lag is normal;
              IMPS code is generic and carries no signal either way."
decision   : match
confidence : 0.88
```

### Step 7 — Agent 5 verification
```
independent_decision   : match
independent_confidence : 0.91
agrees                 : True
combined_confidence    : 0.895
```

### Final result
```
record_id  : LED83563
status     : MATCHED
sub_reason : llm_confirmed
confidence : 89.5%
headline   : "MATCHED — 90% confidence"
```

---

## Data Directories

| Directory | Records | Purpose |
|---|---|---|
| `data/raw_100/` | 110 | Development and testing dataset. Runs in ~2 min on free-tier Groq. |
| `data/raw/` | 550 | Full production dataset. Runs in ~15–20 min on free-tier Groq (8K TPM limit). |
| `data/ground_truth/` | — | Expected outputs for accuracy validation (`ground_truth_110.json`, `ground_truth_550.json`). |
| `data/generator/` | — | Script that generates synthetic datasets (`generate_dataset.py`). |

---

## Field Usage by Agent

| Field | Agent 1 | Agent 2 | Agent 3 | Agent 4 | Agent 8 | Agent 9 |
|---|---|---|---|---|---|---|
| `ledger_id` | Parse | Join key | — | record_id | Index | — |
| `order_id` | Parse | **Join key** | — | Context | Index | — |
| `customer_name` | Parse | — | text_score | Context | **Indexed** | Customer label |
| `ledger.amount` | Parse | Validate | amount_score | Context | — | Amount |
| `ledger.status` | Parse | Route failed | — | Context | Index | Filter pending |
| `refund_amount` | Parse | — | **Deduct from expected** | Context | — | — |
| `notes` | Parse | — | — | Context | **Indexed** | — |
| `rzp_payment_id` | Parse | — | — | record_id | — | — |
| `rzp_fee` | Parse | — | **Deduct from expected** | Context | — | — |
| `captured_at` | Parse | — | date_score | Context | — | **Pending start date** |
| `utr_number` | Parse | — | — | record_id | — | — |
| `settlement_amount` | Parse | — | amount_score | Context | — | — |
| `settlement_date` | Parse | — | date_score | Context | — | — |
| `narration` | Parse | — | text_score (weak) | **Alias match** | **Indexed** | — |
