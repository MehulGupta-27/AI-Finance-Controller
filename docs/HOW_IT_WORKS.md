# How AI Finance Controller Works

A plain-English guide to every step of the reconciliation process — written so anyone on the finance team can understand what the system is doing and why.

---

## The problem it solves

When a customer pays you, three separate systems each record a piece of that transaction:

1. **Your order book** (internal ledger) — logs the sale at the time of checkout
2. **Razorpay** — captures the payment, deducts its fee (~2.36%), and queues the remainder for settlement
3. **Your bank account** — receives the net deposit, usually 1–5 days after the payment

In a perfect world, those three records match cleanly every time. In reality:

- Bank statements have garbled or meaningless descriptions ("UPI-9284", "IMPS 835")
- Settlements arrive late — sometimes 7–9 days — due to bank holidays or batch cutoffs
- Partial refunds mean the ledger, Razorpay, and bank all show different amounts for the same sale
- A card payment might be retried after a network error, leaving two Razorpay records for one real transaction
- Payments processed manually through the Razorpay dashboard never appear in your order book
- Your bank account receives mystery credits — interest payouts, fee reversals, misdirected transfers

Checking all of this manually across hundreds of transactions every month is slow and error-prone. This system does it automatically, flags only the exceptions, and tells you exactly why each one was flagged.

---

## The three outcomes — why "match or no match" isn't enough

Most systems give every transaction one of two labels: **matched** or **not matched**. That's not enough, because two very different situations both look like "not matched":

- A payment where Razorpay confirmed the charge but the bank deposit hasn't arrived yet (normal — just wait)
- A bank credit with no customer payment attached to it anywhere (genuinely unexplained — needs investigation)

Treating these the same wastes time: you'd chase a settlement that was never overdue, and you might miss the unexplained credit that actually needs attention.

This system uses three outcomes:

| Outcome | What it means | What you do |
|---|---|---|
| **Reconciled** | All three records match with confidence | Nothing — fully automated |
| **In Progress** | Two of three records match; the third is missing for a known, benign reason | Wait (settlement arriving) or inform ops (order not logged) |
| **Needs Review** | Genuinely unclear — the system isn't confident enough to decide | A human reviews the specific case with full context |

---

## The nine steps (agents)

The system works through nine sequential steps. Each step handles what it can, then passes the remaining unclear cases to the next step. This means cheap, certain logic runs first and AI reasoning is only used for the small fraction of records that genuinely need it.

---

### Step 0 — Set the reference date

Before anything else, the system finds the latest date in all three data sources. This becomes the "as of" date for the entire run — all time-based checks (like "has this settlement been delayed?") are measured from this date, not from today's real calendar date.

**Why this matters:** The data in the CSV files is historical. If the system used today's date instead, every record would look thousands of days overdue — clearly wrong. Using the dataset's own latest date means time calculations always make sense relative to the data.

---

### Step 1 — Load and validate

Reads all three CSV files and converts every row into a standard format. Any row that fails basic validation (missing required fields, non-numeric amounts) is written to an exception list immediately — it never enters the matching pipeline silently.

**What this guards against:** Silent data loss. A record that disappears during loading can't be reconciled — this step makes sure every input record is accounted for from the start.

---

### Step 2 — Exact match (Ledger ↔ Razorpay)

Matches your internal order records against Razorpay records using the shared order ID. This is a simple database lookup — if the order ID appears in both systems, it's a confirmed match. No AI, no fuzzy logic.

**What resolves here:**
- The majority of normal payments (order ID found in both systems)
- Failed payments — card declined, no money moved — correctly closed with no action needed
- Duplicate capture — two Razorpay attempts for one order — the failed duplicate is identified and ignored

**Early exit:** Any record matched here is done. It doesn't go through fuzzy matching or AI reasoning.

---

### Step 3 — Fuzzy match (Razorpay ↔ Bank)

For all records that passed Step 2, this step finds the corresponding bank deposit. There's no shared key between Razorpay and the bank — the match is based on:

1. **Amount** — `expected deposit = Razorpay amount − Razorpay fee` (and minus any refund). If the bank deposit matches this exactly, that's strong evidence.
2. **Date** — the deposit should arrive 1–10 days after capture.
3. **Description** — the bank narration sometimes contains a partial customer name or order reference. Low weight because narrations are often garbled or empty.

A weighted score combines all three signals. Records scoring above 0.79 are automatically matched. Records scoring 0.50–0.79 need AI reasoning (Step 4). Records below 0.50 go directly to the exception queue.

**Special case — partial refunds:** When a customer was partially refunded, the bank receives less than the original charge. The system uses the actual `refund_amount` column to adjust the expected deposit before scoring — so `1,000 charge − 200 refund − fee = 776.40 expected`, and a bank deposit of exactly 776.40 scores a perfect amount match.

**Global assignment:** All candidate pairs are solved together using an algorithm that finds the globally best one-to-one assignment (the Hungarian algorithm). This prevents a common mistake in simpler systems: greedily matching the "best-looking" pair first, which can lock in a locally good match that blocks a better assignment elsewhere — critical for cases where two different customers paid similar amounts on the same day.

---

### Step 4 — AI reasoning (the 13% that needs it)

About 13% of records don't score clearly enough at Step 3. These fall into two real-world categories:

**Delayed settlements** — the payment amount matches perfectly, but the bank deposit arrived 5–9 days after capture instead of the usual 1–3. The date score is low (unusual lag), so fuzzy scoring alone isn't confident. The AI is given the full context — amount, exact date gap, narration — and asked to reason about whether this is still a valid match.

**Merchant name mismatches** — the bank statement says `"FITZONE WELLNESS PVT LTD"` but your order book says `"Monthly gym membership renewal"`. These two strings share almost no characters, so fuzzy text scoring scores them near zero. But if you know that FitZone Wellness Pvt Ltd is the gym's legal registered name (the same way the bank knows it from their onboarding records), the connection is obvious. The AI is given this merchant profile as a known fact and asked to reason about the match.

The AI is told explicitly: a false match corrupts the financial ledger and is worse than an honest "I'm not sure." It returns a decision, a confidence score, and a 1–2 sentence explanation.

---

### Step 5 — Independent verification

For every record where Step 4 ran, a second, independent AI call reviews the same raw data — not the Step 4 reasoning, to avoid the second agent simply agreeing with the first.

If both agree: the combined confidence is the average of their two scores.
If they disagree: the record goes directly to the human review queue with both agents' reasoning shown side by side.

**Skip condition:** If Step 4 is already very confident (≥95%) and the transaction amount is small (under ₹10,000), the second check is skipped. This is the one combination where being wrong would have minimal financial impact and the probability of error is genuinely low. High-value transactions and mid-confidence matches always get independently verified.

---

### Step 6 — Final routing

A deterministic rules table that assigns every record its final status. No AI here — just explicit, readable logic.

Key rules:
- Combined confidence ≥ 85% → **Reconciled**
- Combined confidence < 85% → **Needs Review** (low confidence)
- Agents disagreed → **Needs Review** (AI conflict)
- Razorpay captured, bank deposit not yet received, within 10 days → **In Progress** (awaiting settlement)
- Razorpay + bank matched, no order in your system → **In Progress** (order not recorded)
- Bank deposit with no Razorpay or order match → **Needs Review** (unexplained credit)
- Transaction amount ≥ ₹50,000 → **Needs Review** regardless of confidence (mandatory human sign-off)

Every record gets a structured explanation — not just a status code, but the actual checklist of what passed, what failed, and what action to take.

---

### Step 7 — Audit trail

Every decision at every step is logged to a permanent, append-only database. For every record you can see exactly which step resolved it, what confidence it had, and what reasoning was used — useful if someone asks "why was this transaction flagged?" six months later.

---

### Step 8 — Reporting

After the full run:
- Counts of Reconciled / In Progress / Needs Review
- Accuracy scores (precision, recall, F1) measured against known-correct answers
- A per-case-type breakdown showing where the system performed well and where it struggled
- A check that every single input record appears in exactly one output bucket — no record was silently lost

---

### Step 9 — Q&A chat

A semantic search interface over the reconciled database. You can ask questions in plain English:

- *"Any gym membership payments this month?"* — finds them even if the bank description says "FITZONE WELLNESS PVT LTD"
- *"Show me unresolved transactions above ₹5,000"* — with amount and status filters
- *"Are there any payments waiting for bank deposit?"* — retrieves pending settlement records

The Q&A agent never invents information. If a record is "In Progress" or "Needs Review", it says so plainly. If nothing matches your question, it says that too.

---

## What "record identity invariant" means

After every full pipeline run, the system verifies that the exact set of records that went in is the exact set that came out — not just the same count, but the same specific record IDs. This catches a subtle failure mode: a record that was silently dropped somewhere in the pipeline would match its count if another was accidentally duplicated, but the set check would catch both problems. If this check fails, the pipeline stops and shows you exactly which records are missing or doubled.

---

## The 11 exception types in the test dataset

The system was tested against 11 specific categories of real-world reconciliation exceptions. See [docs/DATA_GUIDE.md](docs/DATA_GUIDE.md) for a full description of each.
