# Data Guide — Fields, Files, and Record Categories

A reference for the three CSV files the system reads, plus the 11 types of reconciliation exceptions the test dataset contains.

---

## The three source files

### internal_ledger.csv — Your order book

One row per customer order, as recorded in your own system.

| Column | Type | What it means |
|---|---|---|
| `ledger_id` | Text | Unique ID for this ledger row (e.g. `LED23434`) |
| `order_id` | Text | The order number — **the key that links your records to Razorpay** (e.g. `ORD770487`) |
| `customer_name` | Text | The customer's name as entered at checkout |
| `amount` | Number | The amount the customer paid (or the net amount after a refund) |
| `currency` | Text | Always `INR` in this dataset |
| `order_date` | Date | When the order was placed |
| `payment_method` | Text | How they paid: `upi`, `card`, `netbanking`, `wallet` |
| `status` | Text | `paid`, `failed`, or `partially_refunded` |
| `refund_amount` | Number | The rupee amount refunded, if any. **Zero for all records except partial refunds.** This is a real number — not text buried in the notes field — so the system can use it directly in calculations. |
| `notes` | Text | A human-readable description of the purchase (e.g. "Monthly gym membership renewal"). Blank for most records. Critical for the merchant-name mismatch case — see below. |

---

### razorpay_export.csv — Razorpay's records

One row per payment attempt captured by Razorpay. Note: a single order can have two rows if a payment was retried after a network error.

| Column | Type | What it means |
|---|---|---|
| `rzp_payment_id` | Text | Razorpay's own unique ID for this payment attempt (e.g. `pay_3104c0024e4d49`) |
| `order_id` | Text | Links back to the same order in the ledger |
| `amount` | Number | The original gross charge (before Razorpay's fee) |
| `currency` | Text | Always `INR` |
| `rzp_fee` | Number | The actual fee Razorpay charged on this payment. **Used directly — never recalculated.** Real payment gateways use variable pricing, so re-deriving the fee from a flat formula would sometimes be wrong. |
| `captured_at` | Date | When Razorpay confirmed the payment was captured |
| `method` | Text | `upi`, `card`, `netbanking`, `wallet` |
| `status` | Text | `captured` (money moved) or `failed` (no money moved) |

**Expected bank deposit = `amount − rzp_fee`**
For partial refunds: **Expected bank deposit = `amount − rzp_fee − refund_amount`**

---

### bank_statement.csv — Your bank's records

One row per credit to your bank account. Bank records have no shared key with Razorpay or the ledger — they are matched by amount and date.

| Column | Type | What it means |
|---|---|---|
| `utr_number` | Text | The bank's unique transaction reference (e.g. `UTR914655221101`) |
| `settlement_amount` | Number | The net amount deposited in your account |
| `settlement_date` | Date | When the money actually landed in your bank |
| `narration` | Text | The bank's description. Often garbled: `"UPI-9284"`, `"IMPS 835"`, `"PG SETL 189"`. Sometimes blank. Sometimes contains a partial customer name fragment. The system doesn't rely on this being readable. |
| `bank_ref_type` | Text | The transfer type: `NEFT`, `IMPS`, `UPI`, `RTGS` |

---

### ground_truth.json — The answer key

Used only by the reporting agent to check how accurate the system was. Never read by any matching logic — that would be cheating.

Each entry contains the case type (e.g. `delayed_settlement`), the expected outcome (`MATCHED` / `PARTIAL` / `UNRESOLVED`), and the IDs of the records involved.

---

## The 11 record categories

The test dataset contains 110 records covering exactly these 11 real-world exception types. Every category is based on a named, documented pattern that real finance teams track — not synthetic test scenarios.

---

### 1. Clean triple match (55 records)

**What it is:** A normal, successful transaction. Customer paid, Razorpay captured it, bank deposit arrived 1–3 days later. All three records line up.

**Real-world name:** The baseline case. Most transactions in a well-run system look like this.

**Expected outcome:** Reconciled

**Example:** Priya buys a ₹1,499 item on UPI. Razorpay captures ₹1,499, deducts ₹35.34 fee. Bank receives ₹1,463.66 two days later.

---

### 2. Delayed settlement (10 records)

**What it is:** Everything is correct, but the bank deposit arrived 5–9 days after the payment instead of the usual 1–3 days. This happens around bank holidays, long weekends, or batch processing cutoffs — it's routine, not an error.

**Real-world name:** Value date lag / timing difference.

**Expected outcome:** Reconciled

**Why it's tricky:** The date gap makes fuzzy scoring less confident, so AI reasoning is needed to confirm "yes, the amount matches exactly and the lag is within the normal range."

**Example:** Vikram pays on Thursday before a 4-day holiday weekend. The settlement doesn't land until the following Wednesday — 7 days later.

---

### 3. Garbled narration (10 records)

**What it is:** The bank deposit amount and date match correctly, but the bank description is completely unreadable — just a code like `"TXN8216"` or `"PMT4697"` with no customer name or order reference.

**Real-world name:** Banks genuinely do this. Statement narrations are truncated or replaced with internal codes, especially for UPI and IMPS transfers.

**Expected outcome:** Reconciled (matched on amount + date alone)

**Why it's in the dataset:** To prove the system doesn't give up when text matching fails. If amount and date match perfectly, the record should still resolve — text is a helpful signal, not a required one.

---

### 4. Duplicate capture (5 records)

**What it is:** A card payment went through, but the customer's screen showed an error (network glitch). The checkout automatically retried. Now Razorpay has two records for the same order: one that actually succeeded (status: `captured`) and one that failed (status: `failed`).

**Real-world name:** Double-posting error. Very common with card payments on slow connections.

**Expected outcome:** Reconciled (matched to the captured attempt; the failed attempt is ignored)

**Why it matters:** A naive matcher might link the bank deposit to the wrong Razorpay record, or try to match both. The system correctly identifies which attempt succeeded.

---

### 5. Partial refund (5 records)

**What it is:** The customer was partially refunded — maybe one item out of a multi-item order was out of stock. The ledger shows the net amount (what the customer kept), Razorpay shows the original gross charge, and the bank receives the original amount minus fee minus the refund.

**Real-world name:** Netting difference. One of the most common reasons amounts don't match across the three systems.

**Expected outcome:** Reconciled

**How it's handled:** The `refund_amount` column in the ledger holds the actual rupee refund as a number. The system uses it directly: `expected deposit = Razorpay charge − Razorpay fee − refund amount`. This gives an exact match against the bank deposit without any guessing.

---

### 6. Pending settlement (5 records)

**What it is:** The customer paid and Razorpay confirmed the capture. But it's only been 1–3 days — the bank transfer genuinely hasn't happened yet. There's no bank record because the money is still in transit.

**Real-world name:** In-transit item. Standard in every bank reconciliation.

**Expected outcome:** In Progress (awaiting settlement) — **not** an error

**Why it matters:** A system that only knows "match or no match" would flag this as a failed match. This system correctly recognises it as a normal, incomplete-but-on-track situation. It will auto-resolve once the bank deposit arrives.

---

### 7. Failed payment (5 records)

**What it is:** A customer's card was declined — insufficient funds, wrong CVV, bank blocked it. Razorpay logs the attempt (status: `failed`). No money moved. No bank deposit was ever going to arrive.

**Real-world name:** No-effect record. Every payment system logs failed attempts.

**Expected outcome:** Reconciled (sub-status: no action needed)

**Why it matters:** Nothing to reconcile here — the record should be cleanly closed, not left in a perpetual "unmatched" state.

---

### 8. Missing from ledger (3 records)

**What it is:** Real money moved — Razorpay captured the payment and the bank deposit arrived — but there is no corresponding order in your internal system. This typically happens when a support agent processes a payment directly through the Razorpay dashboard, bypassing the normal checkout flow.

**Real-world name:** Orphan ledger gap. A common integration failure when manual overrides skip the webhook/order-creation step.

**Expected outcome:** In Progress (no ledger record) — someone on your ops team should investigate why the order wasn't logged

**Why it matters:** The money is real and accounted for, but your order book has a hole. This needs to be tracked down and recorded, not just ignored.

---

### 9. Adversarial near-miss (4 records — 2 pairs)

**What it is:** Two different real customers paid almost identical amounts on the same day. For example, a gym that charges ₹999/month might have Priya paying ₹999.00 and Aditi paying ₹998.50 (a small loyalty discount) on the 1st of the month. A matcher that just picks "closest amount on the same day" could accidentally link Priya's bank deposit to Aditi's payment.

**Real-world name:** Amount-collision risk. Particularly common for businesses with fixed-price products or subscriptions.

**Expected outcome:** Both reconcile correctly — each payment matches its own bank deposit

**Why it matters:** Tests that the global assignment algorithm (Hungarian algorithm) correctly solves the overall best assignment, not just a greedy left-to-right best-guess.

---

### 10. Unidentified bank credit (5 records)

**What it is:** Your bank account received money with a description like `"INT CREDIT QTR"` or `"BANK REVERSAL FEES"` — something that doesn't correspond to any customer payment or Razorpay transaction. Could be quarterly interest, a fee reversal, or a misdirected transfer from another account.

**Real-world name:** Unidentified receipt / unapplied cash item. A standard line item on every reconciliation team's exception report.

**Expected outcome:** Needs Review — someone needs to find out where this money came from

**Why it's different from "pending settlement":** Pending settlements have a known explanation (money on the way). Unidentified credits have no explanation — they require active investigation.

---

### 11. Merchant name mismatch (3 records)

**What it is:** The bank statement shows the gym's **legal registered company name** (`"FITZONE WELLNESS PVT LTD"`) instead of the everyday brand name (`"FitZone Gym"`). Internally, the order note says `"Monthly gym membership renewal"` — completely different text.

**Real-world name:** Merchant descriptor mismatch. One of the most commonly cited causes of confusion on bank statements. Businesses often register under a different legal entity name than their consumer-facing brand.

**Expected outcome:** Reconciled

**Why it's the hardest case:** The fuzzy text scorer sees near-zero similarity between "gym membership renewal" and "FITZONE WELLNESS PVT LTD" — they share almost no characters. This case is only solvable if the system knows that FitZone Wellness Pvt Ltd is FitZone Gym's legal name — a fact stored in the merchant configuration, not guessable from the transaction data alone.

**How it's solved:** The AI agent is given the merchant's registered name as a known fact (the same way a real system would know it from merchant onboarding records). It reasons: "The bank shows our own registered legal name, the amount matches exactly after fee, and the lag is 1 day — this is clearly our own settlement."

---

## Confidence scores — what they mean

Every record that goes through AI reasoning carries a confidence score (0–100%).

| Range | Meaning |
|---|---|
| 95–100% | Both agents agreed and the match is clear — no corroboration needed |
| 85–95% | Both agents agreed — one or more signals were weak (e.g. garbled narration) but amount and date were solid |
| 80–85% | One agent is less certain — still matched but with reduced conviction |
| Below 85% | Routed to Needs Review — not confident enough to auto-resolve |

The threshold is set at 85% deliberately conservatively. A false match corrupts your financial records; an honest "I'm not sure" that routes to human review is always the safer outcome.
