# AI Finance Controller — Master Build Specification
### Razorpay Buildathon, Track 04
### This is a build spec for an AI coding assistant. Follow it section by section, in order. Section 0C contains non-negotiable design rules that prevent specific, well-understood failure modes — read it in full before writing any matching or scoring logic, not just skim it.

---

## 0. Mandatory Environment Setup — Do This Before Anything Else

**This step is not optional and not skippable.** Every subsequent step assumes this is done.

```bash
# 1. Create the project root and enter it
mkdir ai-finance-controller && cd ai-finance-controller

# 2. Create and activate a virtual environment — every single time you
#    open a new terminal for this project, activate it first.
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install core dependencies into the venv (never into system Python)
pip install pandas rapidfuzz pydantic fastapi uvicorn scikit-learn scipy python-dotenv tenacity groq pytest chromadb sentence-transformers

# 4. Create the .env file — do this now, not when you first need an API key
touch .env
```

Add to `.env`:
```
GROQ_API_KEY=your_key_here
LLM_PROVIDER=groq
```

**Heads up on install time:** `sentence-transformers` pulls in `torch` as a dependency, which is a large download (several hundred MB) and can take a few minutes on a slow connection. Run this install early — during environment setup, before you're actively blocked waiting on it — not right when you get to building Agent 9 late in the build order.

**Rule for the AI IDE building this project:** Before writing or running any Python file, confirm `venv` is active (check `which python` points inside the project's `venv/` folder). If a fresh terminal/session is started and `venv` is not active, activate it first — do not install packages globally, and do not run the pipeline outside the venv. This applies for the entire duration of the build, not just initial setup.

Create `.gitignore` immediately:
```
venv/
__pycache__/
*.pyc
.env
db/*.db
chroma_db/
outputs/reports/*
```

---

## 1. What This Project Is

We are building a **multi-agent reconciliation system** that takes three messy, disconnected financial data sources — a merchant's internal order ledger, a payment gateway (Razorpay) export, and a bank settlement statement — and automatically determines which records across all three sources represent the *same real-world transaction*.

The system must:
1. Match records with **measured, provable accuracy** (not a demo that "looks right")
2. Process a full batch (500+ records), not a cherry-picked handful
3. Produce an **honest exception list** for anything it cannot confidently resolve
4. Show a complete **audit trail** — every decision, explainable, traceable
5. Keep every money-adjacent AI action **bounded and gated**, with human review triggered at defined thresholds
6. Handle failure gracefully without breaking the pipeline
7. **Never collapse a three-way real-world outcome into a two-way (binary) decision** — see Section 0C, this is the single most important design rule in this entire spec

## 2. What The Track Is Actually Testing

The stated bar: *"Throughput plus measured accuracy plus an honest exception list. One cherry-picked match proves nothing."*

- **Throughput** → run on the full dataset, report exactly how many records were processed — and every record must be accounted for (see Section 0C.3)
- **Measured accuracy** → compare against known-correct ground truth, report real precision/recall/F1
- **Honest exception list** → anything uncertain is visibly flagged with a reason, never force-matched or silently dropped
- **"Cherry-picked match proves nothing"** → judges expect statistical rigor, not a single good-looking example

---

## 0C. Critical Design Rules — Non-Negotiable

These three rules exist because each one guards against a specific, well-understood failure mode in reconciliation systems. Build them in from the very first line of code in the relevant agent — do not build a simpler version "for now" and plan to harden it later. Each one is cheap to build correctly from the start and expensive to retrofit once records and thresholds are already flowing through a simpler version.

### 0C.1 — Never use a binary match/no-match decision

**The risk:** Real reconciliation has three distinct outcomes, not two — a record can be fully matched, correctly and normally incomplete (e.g. money hasn't settled yet), or genuinely ambiguous and needing a human. A system that only outputs "matched: true/false" has no way to represent "correct, but not the full picture yet" — it will either force such records into a false "match" or, more commonly, score genuinely correct partial detections as failures purely because the label had nowhere to put them.

**The rule:** every record resolves to exactly one of three statuses, defined in full in Section 6B. Build the three-state version as the only version — there should never be a point in this project where the output is a boolean.

### 0C.2 — Never anchor "how old is this" logic to the real wall-clock date

**The risk:** Any check for "has this settlement been overdue for more than N days" is tempting to write as a comparison against `datetime.now()` — the actual real-world date when the code happens to run. Because the synthetic dataset's dates are fixed in a historical window (see Section 3), comparing against the real current date will make *every* record look artificially old relative to today, regardless of when the dataset was generated or when the pipeline is actually run. This silently breaks any time-based logic in a way that's easy to miss, because the numbers still "look plausible" until you check them against the dataset's actual case counts.

**The rule:** any logic that reasons about elapsed time ("has this been pending too long," "is this settlement overdue") must be measured against a fixed **`AS_OF_DATE`**, computed once at pipeline startup as the maximum date value found across all three loaded source files — never `datetime.now()` or `date.today()`, anywhere in the codebase, for any dataset-relative time comparison. Log the computed `AS_OF_DATE` value at the start of every pipeline run, visibly, so it's always inspectable.

```python
# Correct pattern — compute once, pass explicitly, never call datetime.now()
# for dataset-relative comparisons anywhere else in the codebase.
AS_OF_DATE = max(
    ledger_df["order_date"].max(),
    razorpay_df["captured_at"].max(),
    bank_df["settlement_date"].max(),
)
```

### 0C.3 — Every record must be accounted for, every single run

**The risk:** A pipeline with multiple stages and filtering steps can silently drop records — a row that fails validation, gets filtered out during candidate generation, or falls through a gap in the routing logic can simply vanish from the final counts with no error raised. A status breakdown that sums to fewer records than the actual input size, with no explanation, is a silent data-loss bug — and it directly violates the track's "throughput" requirement. A system that can quietly lose records cannot be trusted with financial reconciliation.

**The rule:** after every full pipeline run, verify both the **count** and the **identity** of every record — not just that the numbers add up, but that the exact same set of record IDs that went in is the exact same set that came out, with no duplicates and no silent drops:
```python
input_ids = set(all_input_record_ids)
output_ids = set(matched_ids) | set(partial_ids) | set(unresolved_ids)

missing = input_ids - output_ids
duplicated = [rid for rid in (matched_ids + partial_ids + unresolved_ids)
              if (matched_ids + partial_ids + unresolved_ids).count(rid) > 1]

assert not missing, f"{len(missing)} records vanished from the pipeline: {missing}"
assert not duplicated, f"{len(duplicated)} records appear in more than one status bucket: {duplicated}"
```
This is deliberately stronger than a simple length check — `len(matched) + len(partial) + len(unresolved) == total` can still pass even if a record was silently dropped in one place and duplicated in another, since the counts could coincidentally cancel out. Checking set membership catches both failure modes directly. This must run automatically at the end of every pipeline execution, not just when someone remembers to check manually. If it fails, halt and print exactly which record IDs are missing or duplicated — never continue silently.

---

## 0D. Automated Regression Tests — Write These Alongside the Agents They Protect

Section 0C's rules are only as good as your discipline in following them by hand. Encode each one as an automated test so a violation is caught by running `pytest`, not by someone remembering to eyeball a dashboard number. Write these in `tests/` as each relevant agent is built (Section 11's build order), not as a final cleanup step.

```python
# tests/test_as_of_date.py
# Guards Section 0C.2 — AS_OF_DATE must never be the real-world date.
def test_as_of_date_is_within_dataset_range(loaded_test_data):
    as_of = compute_as_of_date(loaded_test_data)
    # the generator's BASE_DATE and day_span define the dataset's real window —
    # import these constants rather than hardcoding the range here, so this
    # test stays correct if the dataset is regenerated at a different size
    assert BASE_DATE <= as_of <= BASE_DATE + timedelta(days=DAY_SPAN + 10)

def test_overdue_check_uses_as_of_date_not_wallclock(monkeypatch):
    # freeze "real" now() far outside the dataset's window and confirm the
    # overdue calculation is unaffected — this is the exact bug this rule exists to catch
    monkeypatch.setattr(datetime, "now", lambda: datetime(2099, 1, 1))
    result = route_record(sample_pending_settlement_record, as_of_date=FIXED_AS_OF_DATE)
    assert result.sub_reason == "awaiting_settlement"  # not "overdue_settlement"

# tests/test_record_count_invariant.py
# Guards Section 0C.3 — no record may be silently dropped or duplicated.
def test_every_input_record_appears_exactly_once_in_output(pipeline_run_on_110_records):
    input_ids = set(pipeline_run_on_110_records.input_record_ids)
    output_ids = (pipeline_run_on_110_records.matched_ids
                  + pipeline_run_on_110_records.partial_ids
                  + pipeline_run_on_110_records.unresolved_ids)
    assert set(output_ids) == input_ids
    assert len(output_ids) == len(set(output_ids))  # no duplicates across buckets

# tests/test_three_state_output.py
# Guards Section 0C.1 — status must never be a boolean or any value outside the three.
def test_router_output_is_always_one_of_three_states(all_dev_set_records):
    for record in all_dev_set_records:
        status = route_record(record, as_of_date=FIXED_AS_OF_DATE).status
        assert status in {"MATCHED", "PARTIAL", "UNRESOLVED"}

# tests/test_agent5_skip_condition.py
# Guards Section 5, Agent 5's skip logic — verification must NEVER be
# skipped on confidence alone. This is an easy mistake to reintroduce
# (the naive, backwards version is the more "obvious" thing to write),
# so it gets its own dedicated test.
def test_high_confidence_high_value_still_verifies():
    # High value must always be verified, no matter how confident Agent 4 is —
    # this is the exact case the naive confidence-band approach gets wrong.
    assert should_skip_verification(confidence=0.97, amount=75_000) is False

def test_high_confidence_low_value_skips():
    assert should_skip_verification(confidence=0.97, amount=5_000) is True

def test_moderate_confidence_low_value_still_verifies():
    # Confidence alone isn't sufficient either — both conditions are required.
    assert should_skip_verification(confidence=0.80, amount=5_000) is False
```

Run the full test suite before every dev-set scoring pass, not just once at the end — a regression here should be caught within seconds of introducing it, not discovered several build steps later during a full accuracy run.

---

## 3. The Dataset

**Build and start development against a 110-record dataset first, not a larger one.** Generate this with explicit per-case-type counts (not a proportional scale-down of some larger run) so every case type is deliberately represented, including the no-match and semantic-matching case types below:

```python
N_CLEAN = 55
N_DELAYED = 10
N_HARD = 10
N_DUP = 5
N_REFUND = 5
N_PENDING = 5
N_FAILED = 5
N_MISSING_LEDGER = 3
N_ADVERSARIAL_PAIRS = 2        # → 4 records
N_UNIDENTIFIED_BANK_CREDIT = 5
N_SEMANTIC_BRAND_NARRATION = 3  # new — see below, this is what actually exercises Agent 4's semantic_similarity
# total = 110 records
```

Save this as `data/raw_100/` (name kept as-is even though the true count is 110, for consistency with the rest of this spec). This is the dataset every build step through Agent 9 targets — see Section 11. Only scale up to a larger dataset once this one is fully working and passing every check in Section 0D.

**Why start here instead of a bigger set:** a ~110-record run makes the Section 0C.3 record-identity invariant trivial to verify by eye, it makes Groq's rate limits a non-issue even during heavy iteration, and it forces every one of the 11 case types below to be exercised on every single run — no case type is rare enough to be accidentally skipped during early debugging.

**The full-scale dataset (for Section 11 step 17 onward) uses the same 11 case types at 5× the counts above, saved to `data/raw/`:**
```python
N_CLEAN = 275
N_DELAYED = 50
N_HARD = 50
N_DUP = 25
N_REFUND = 25
N_PENDING = 25
N_FAILED = 25
N_MISSING_LEDGER = 15
N_ADVERSARIAL_PAIRS = 10        # → 20 records
N_UNIDENTIFIED_BANK_CREDIT = 25
N_SEMANTIC_BRAND_NARRATION = 15  # cycle through the 3 brand scenarios (Section 3's plain-language examples) across different customers/dates — real subscription businesses do have many customers paying the same few well-known services, so repetition here is realistic, not artificial padding
# total = 550 records
```
Keep both datasets — do not delete `data/raw_100/` once you generate `data/raw/`. Every early build step continues to reference the 110-record set; only Section 11 step 17 onward uses the full 550-record set.

Three CSV files simulate real-world messiness deliberately:

### `internal_ledger.csv`
`ledger_id, order_id, customer_name, amount, currency, order_date, payment_method, status, refund_amount, notes`

**`refund_amount` is `0.0` for every case type except `partial_refund_split`, where it holds the actual refunded amount as a real number.** This used to only exist as text inside `notes` (e.g. `"refund_amount=700"`), which meant Agent 3 could never reliably use it in a calculation. Promoting it to a proper numeric column is what makes `partial_refund_split` solvable deterministically at Agent 3 instead of falling back to `UNRESOLVED` (Section 5, Agent 3's `predicted_settlement` formula). `notes` still carries the human-readable version for the explanation generator (Section 6D) — the two aren't redundant, they serve different consumers (Agent 3 needs the number, Section 6D's explanation needs the sentence).

### `razorpay_export.csv`
`rzp_payment_id, order_id, amount, currency, rzp_fee, captured_at, method, status`
- `order_id` matches the ledger's `order_id` — your one reliable shared key, connecting Ledger↔Razorpay only.

### `bank_statement.csv`
`utr_number, settlement_amount, settlement_date, narration, bank_ref_type`
- **No shared key with anything else.** `settlement_amount = amount − rzp_fee` (2% + 18% GST on that fee — deterministic and learnable). `settlement_date` lags `captured_at` by 1–9 days in normal cases. `narration` is garbled text, sometimes with partial name/order fragments, sometimes almost nothing useful.

### The 11 injected case types

Every case type below is deliberately modeled on a named, recognized category of reconciliation exception that real finance-ops teams encounter — none of these are synthetic ML test scenarios invented for this project. This matters directly for the track's own framing (*"Reconciliation, settlement and forecasting are still done by hand"*) — the dataset needs to reflect the actual mess a human reconciliation analyst deals with, not an artificial one.

| Case type | Count (110-record set) | Real-world basis | What it validates |
|---|---|---|---|
| `clean_triple_match` | 55 | The baseline case — most transactions in a well-functioning system do reconcile cleanly | Deterministic fee math, normal 1–3 day settlement lag |
| `delayed_settlement` | 10 | Known in accounting as a **value date lag / timing difference** — settlement processing delays (bank holidays, batch cutoffs) are routine, not exceptional | Date-tolerance boundary — lag stretched to 5–9 days |
| `hard_garbled_narration` | 10 | Bank statement narrations being truncated or abbreviated is a well-documented, widely-complained-about limitation of real bank statement formats, not a contrived edge case | Forces reasoning beyond text |
| `duplicate_capture` | 5 | A **double-posting error** — retried payment attempts after a network timeout or gateway hiccup creating two gateway-side records for one real transaction is a common integration failure mode | Two Razorpay attempts (one failed, one real) — must not double-match or mismatch |
| `partial_refund_split` | 5 | A **netting difference** — partial refunds (partial order fulfillment, goodwill discounts) are one of the most common reasons ledger, gateway, and bank amounts diverge for the same transaction | Ledger (net) ≠ Razorpay (gross) ≠ Bank (net of refund) |
| `pending_settlement` | 5 | An **in-transit item** — the standard accounting term for a transaction recorded on one side of a reconciliation but not yet reflected on the other, purely due to normal processing timing | Correct outcome is `PARTIAL`, not an error — no bank record yet because settlement genuinely hasn't happened |
| `failed_payment_orphan` | 5 | A **no-effect record** — a logged attempt where no money ever moved; every payments system logs failed attempts, and they should never appear in reconciliation output | Correct outcome is `MATCHED` with `sub_reason: "no_action_needed"` — payment never succeeded, nothing downstream was ever expected |
| `missing_from_ledger` | 3 | An **orphan ledger gap** — real money moved but the merchant's own system never logged it, typically from a webhook failure or manual/offline order path; a genuine and common integration gap, not a hypothetical one | Correct outcome is `PARTIAL` — real money moved, but the merchant's own system never logged it; genuine gap for ops, not a matching failure |
| `adversarial_near_miss` | 4 (2 pairs) | **Amount-collision risk** — a recognized hazard in any amount+date matching system; businesses with standard price points (subscriptions, fixed-fee products) routinely have multiple unrelated transactions with identical or near-identical amounts on the same day | Two *different* real transactions with amounts within ₹1.50–4.50, same date — tests one-to-one assignment, not naive nearest-match |
| `unidentified_bank_credit` | 5 | Known in accounting as an **unidentified receipt / unapplied cash item** — bank statements routinely contain credits (interest, fee reversals, misdirected third-party transfers) with no corresponding internal record; a standard, named category on every reconciliation team's exception report | A standalone bank row with NO ledger or Razorpay counterpart anywhere — tests that the matcher reports "no match" honestly instead of force-matching it to the nearest plausible-looking Razorpay record |
| `semantic_brand_narration` | 3 | **Merchant descriptor mismatch** — a genuine, extremely common pattern where a business's settlement narration shows its formal *registered legal name* (as filed with the payment aggregator/bank), while the business's own internal system logs the transaction using its everyday *consumer-facing brand name*. This is one of the most well-documented causes of real-world payment confusion — it's the standard explanation given for why people don't recognize charges on their own statements | The ONE case type that specifically requires Agent 4's `semantic_similarity` — fuzzy text scoring (Agent 3) will score these low, since there's little to no character overlap; solvable only by combining the ledger's context with a known fact about this specific merchant's registered identity (Section 5, Agent 4) |

**`unidentified_bank_credit` generation logic:** insert bank rows independently, not derived from any ledger/Razorpay pair the way every other case type is. Give each a random amount and a date within the dataset's normal window, and a narration that's plausible-but-unrelated bank noise (e.g. `"INT CREDIT QTR"`, `"BANK CHG REVERSAL"`, `"NEFT MISC CREDIT"`) — text that doesn't reference any customer name or order fragment, since it doesn't correspond to a Razorpay transaction at all. Do not link it to any `case_id` representing a real transaction in `ground_truth.json` — its ground truth is simply "no match exists, anywhere, ever."

**Why this needs its own status, distinct from the other "incomplete" cases (see Section 8's mapping):** `pending_settlement` and `missing_from_ledger` both have a specific, benign explanation for their incompleteness. `unidentified_bank_credit` doesn't — it's unexplained money movement with no available reason, which in a real finance operation is exactly the kind of thing that *should* get a human's attention, unlike a normal pending settlement. This is the correct behavior to route to `UNRESOLVED`, not `PARTIAL`.

**`semantic_brand_narration` generation logic — read this carefully, it only works if wired correctly, and the direction of the mismatch matters:**
- **Get the direction right.** This dataset represents ONE merchant collecting payments FROM customers. The mismatch must be about *this merchant's own identity* — its formal registered legal name vs. its everyday brand name — never a *different, unrelated* company's brand name. A bank narration for money landing in this merchant's account would never plausibly say "NETFLIX.COM" (that only makes sense on someone's *outgoing* personal spending, not this merchant's *incoming* settlements).
- **Define one fixed merchant identity for the whole dataset**, in `agents/config.py` (Section 6C):
  ```python
  MERCHANT_PROFILE = {
      "brand_name": "FitZone Gym",
      "registered_legal_name": "FitZone Wellness Private Limited",
  }
  ```
- Structure the case like `clean_triple_match` (full Ledger→Razorpay→Bank chain, real amount/date math) with one deliberate change: set the ledger row's `notes` field to a plain description of what was purchased (e.g. `"Monthly gym membership renewal"`, `"Personal training package - 10 sessions"`, `"Annual premium membership upgrade"`), and set the bank `narration` to a variant of the merchant's *registered legal name* (e.g. `"FITZONE WELLNESS PVT LTD"`, `"FZW PRIVATE LIMITED RZRPY"`, `"FITZONE WELLNESS P LTD SETL"`) instead of the usual customer-name/order-fragment narration used elsewhere.
- **Critical: this is not solvable by an LLM's general world knowledge**, unlike a famous global brand — "FitZone Wellness Private Limited" is a fictional small business the model has never seen in training data. It's only solvable if Agent 4 (and Agent 5) are given `MERCHANT_PROFILE` as fixed context in their prompts, the same way a real production system would know its own merchant's registered name from onboarding/KYC records. This is actually a more realistic design than relying on LLM brand recognition — it models genuine merchant-profile lookup, not a lucky guess.
- **Critical wiring requirement:** Agent 1's canonical schema already preserves the full original row in the `raw` field (Section 5, Agent 1), which technically includes `notes` — but that's not enough on its own. Agent 4's prompt construction must **explicitly pull the ledger `notes` field into the candidate context it sends the LLM**, not just `customer_name`. If `notes` isn't actually surfaced to the LLM call, these three records are unsolvable by design — there's nothing else in the record for the LLM to reason from. Verify this specifically when building Agent 4 (Section 11): confirm `notes` shows up in the actual prompt text sent to the LLM for these records, not just sitting unused in `raw`.
- The registered-name variants don't need to be character-identical to each other — real settlement narrations abbreviate inconsistently (full name, abbreviated, with/without "PVT LTD" or "SETL" suffixes). This variation is realistic and also means Agent 4 needs to genuinely reason about the match each time rather than pattern-matching one fixed string.

**Use this real-world grounding directly in your submission write-up** — being able to say "every exception category in our test set corresponds to a named category real reconciliation teams track" is a stronger, more specific claim than "we tested edge cases," and it's a claim judges can't easily dismiss as academic.

### Plain-language examples — what each case type actually looks like

These are here so anyone reading the code later (including your own future self, mid-demo, answering a judge's question) can picture the real situation each case represents, not just the technical rule.

1. **`clean_triple_match`** — Priya buys a ₹1,499 t-shirt on UPI. Razorpay captures ₹1,499 instantly. Two days later, the bank deposits ₹1,464 (after Razorpay's fee). All three records line up cleanly — this is what "normal" looks like.

2. **`delayed_settlement`** — Same as above, but Priya paid right before a long bank holiday weekend. Instead of the usual 2-3 day gap, the money doesn't land in the bank for 7 days. Still a real match — the system just needs to not treat "slow" as "wrong."

3. **`hard_garbled_narration`** — Rohan pays ₹3,200. The bank statement line just says `"UPI-9284"` — no name, no order number, nothing readable. Banks genuinely do this. You're forced to match on amount and date alone.

4. **`duplicate_capture`** — Someone's card gets charged ₹899, but the network hiccups right after, so checkout shows an error. The app auto-retries. Now Razorpay has two attempts logged for the same order — one that actually failed, one that actually succeeded. Must link to the real one, not the failed duplicate.

5. **`partial_refund_split`** — Ravi orders two items for ₹2,000 total. One item (₹700) is out of stock and gets refunded. The ledger shows ₹1,300 (what he kept), Razorpay shows the original ₹2,000 charge, and the bank shows the net settlement after the refund. Same transaction, three different amounts.

6. **`pending_settlement`** — A customer paid at 11pm last night. Razorpay confirmed the payment immediately, but banks take 1-2 days to actually deposit money. Check today, and there's genuinely no bank record yet — that's *correct*, not a bug.

7. **`failed_payment_orphan`** — Someone's card is declined for insufficient funds. The failed attempt is logged, but zero money moved anywhere — no Razorpay capture, no bank entry. Nothing to reconcile; correctly ignore it.

8. **`missing_from_ledger`** — A support agent manually processes a payment adjustment directly through the Razorpay dashboard, bypassing the normal checkout flow. Real money moves and shows up in Razorpay and the bank — but the internal order system never logged it, since it didn't go through the usual path. A genuine hole someone in finance needs to know about.

9. **`adversarial_near_miss`** — A gym charges everyone a flat ₹999/month. On the 1st, 40 people get charged around ₹999 the same day — Priya pays exactly ₹999.00, Aditi pays ₹998.50 (a small loyalty discount). A matcher that just grabs "closest amount, same day" could accidentally link Priya's bank settlement to Aditi's payment. This case exists purely to catch that mistake.

10. **`unidentified_bank_credit`** — The business bank account gets a ₹212.40 credit labeled `"INT CREDIT QTR"` — quarterly interest the bank pays out. Nothing to do with any customer. The system needs to honestly say "I don't recognize this" instead of forcing a match to some nearby Razorpay payment just because the amount looks plausible.

11. **`semantic_brand_narration`** — Meera pays ₹649 for her monthly gym membership at "FitZone Gym" — that's the friendly name on the sign outside, and the name customers actually know. The internal order system logs it plainly: `"Monthly gym membership renewal"`. But when the money settles into the gym's bank account, the bank statement shows `"FITZONE WELLNESS PVT LTD"` — the gym's official *registered* company name, which is different from its everyday brand name. Character-for-character, `"gym membership renewal"` and `"FITZONE WELLNESS PVT LTD"` share almost nothing. But if you know that FitZone Wellness Pvt Ltd *is* FitZone Gym's legal name (the same way any business knows its own registered identity), the connection is obvious. Same idea applies to a personal training package settling as `"FZW PRIVATE LIMITED RZRPY"`, or a premium membership upgrade settling as `"FITZONE WELLNESS P LTD SETL"` — same business, three different ways its name shows up on paper. This is the one case type in the whole dataset that specifically requires *understanding what the words mean*, not just how similar they look — and specifically, it requires knowing this merchant's own registered identity, not general trivia about famous brands.

### `ground_truth.json` — the hidden answer key
Contains, for every case, the **three-state expected status** (see Section 8's mapping table — this is the authoritative ground truth definition, not a boolean). **This file must never be read by any matching-logic agent.** Only `reporting_agent.py` (Agent 8) may import it.

---

## 4. System Architecture — Full Pipeline

```
Raw CSVs (data/raw/)
        │
        ▼
[Agent 0] AS_OF_DATE computation — logged at startup, used everywhere
        │
        ▼
[Agent 1] Ingestion & Normalization  ── deterministic, no LLM
        │
        ▼
[Agent 2] Exact Match Engine  ── deterministic, no LLM
        │  ── EARLY EXIT: exact match found → STOP → MATCHED
        │  (only unmatched records continue↓)
        ▼
[Agent 3] Fuzzy Match Engine  ── deterministic + weighted composite scoring, no LLM
        │  ── EARLY EXIT: very high confidence (≥0.90) → STOP → MATCHED
        │  (only unmatched / mid-confidence records continue↓)
        ▼
[Agent 4] LLM Reasoning Agent  ── LLM call, structured output only
        │
        ▼
[Agent 5] Verifier Agent  ── second, independent LLM call
        │
        ▼
[Agent 6] Confidence Router  ── deterministic, outputs MATCHED / PARTIAL / UNRESOLVED
        │
   ┌────┼──────────┐
   ▼    ▼           ▼
MATCHED PARTIAL   UNRESOLVED → Human Review Queue
        │
        ▼
[Agent 7] Audit Trail Logger  ── runs alongside every stage above, not after
        │
        ▼
[Agent 8] Reporting & Scoring Agent  ── multi-class scoring, record-count invariant check, ONLY consumer of ground_truth.json
        │
        ▼
[Agent 9] Settlement Q&A Agent  ── thin LLM layer on top of the finished, reconciled DB
```

**This is deliberately the same staged chain you'd sketch as "Exact → Fuzzy → Semantic → Confidence Engine → Match / Review / Exception"** — cheap, deterministic logic resolves the majority for free with early exits at each stage, and the LLM is reserved for the genuinely ambiguous minority. Within the fuzzy stage specifically, the match itself is already a **weighted combination of multiple signals** (amount closeness, date proximity, text similarity — Section 5, Agent 3), so you get both the efficiency of staged early-exit *and* the accuracy of multi-signal weighted scoring within each stage that actually runs — there is no need to choose one design over the other, this spec already combines them.

**Explicit mapping, so there's no ambiguity about where each concept lives:**

| Concept | Lives in | Notes |
|---|---|---|
| Exact Matching | Agent 2 | Character-for-character key match (`order_id`) |
| Fuzzy Matching | Agent 3 | Weighted composite: amount + date + character-level text similarity (`rapidfuzz`) |
| Semantic Matching | Agent 4 | A dedicated, separately-scored `semantic_similarity` field (0.0–1.0) from the LLM call — catches meaning-level matches fuzzy text scoring misses (e.g. this merchant's registered legal name, `"FITZONE WELLNESS PVT LTD"`, vs its own ledger note, `"Monthly gym membership renewal"` — Section 3's `semantic_brand_narration` case), via LLM reasoning plus a known merchant-profile fact, rather than a separate embeddings pipeline |
| Confidence Engine | Agent 6 (+ Section 6B) | Combines exact/fuzzy results, Agent 4's `semantic_similarity` and `confidence`, Agent 5's independent agreement, and business rules (the ₹50,000 gate) into the final `MATCHED` / `PARTIAL` / `UNRESOLVED` decision |

---

## 5. Agent-by-Agent Specification

### Agent 0 — AS_OF_DATE Computation (not a full agent, a required startup step)
Compute once, log visibly, pass explicitly to every downstream agent that needs to reason about elapsed time. See Section 0C.2. Never recomputed mid-run, never substituted with `datetime.now()`.

---

### Agent 1 — Ingestion & Normalization
**Purpose:** Load the three CSVs, validate every row against a strict schema, convert into one canonical representation.

**Approach:** Pure Python + Pandas + Pydantic validation. No LLM.

**Output contract (per record):**
```json
{
  "record_id": "uuid",
  "source": "ledger | razorpay | bank",
  "source_ref": "original ID (ledger_id / rzp_payment_id / utr_number)",
  "order_id": "string or null",
  "amount": 1234.50,
  "date": "2026-07-14",
  "text_field": "customer_name or narration",
  "notes": "ledger notes field, when present — see below",
  "status": "string",
  "raw": { "...original row, kept for audit" }
}
```

**`notes` is promoted to a top-level field, not left buried inside `raw`, specifically because Agent 4 needs it for the `semantic_brand_narration` case type (Section 3).** For most records `notes` is empty, and that's fine — but for those specific records it carries half of the signal an LLM needs to reason from (e.g. `"Monthly gym membership renewal"` vs a bank narration reading `"FITZONE WELLNESS PVT LTD"` — the other half being the `MERCHANT_PROFILE` context described in Section 5, Agent 4). If this field isn't surfaced here at the ingestion stage, it won't reliably make it into Agent 4's prompt later, and those records become unsolvable by design.

**Failure handling:** Any row failing validation is written directly to the exception list with `reason: "ingestion_validation_failed"` — never dropped silently, never passed downstream. This is the first line of defense for Section 0C.3's record-count invariant.

---

### Agent 2 — Exact Match Engine
**Purpose:** Resolve the easy majority instantly, for free — Ledger↔Razorpay via `order_id`.

**Approach:** Pandas merge. No LLM. **Early exit:** an exact match found here means STOP — this record is `MATCHED`, full stop, no further stages needed.

**Note:** There is no exact-tier key for Razorpay↔Bank (see Section 3) — do not attempt to force one. That pair always proceeds to fuzzy matching.

---

### Agent 3 — Fuzzy Match Engine (the core of your accuracy story)

**Purpose:** Resolve Razorpay↔Bank matches (and any remaining edge cases) using amount, date, and text similarity — combined via weighted scoring, with a proper one-to-one global assignment step.

**Three sub-steps, in order:**

**3a. Predicted-value candidate generation.** For every unmatched Razorpay record, use the **actual `rzp_fee` value already present in the Razorpay export row** — not a recomputed formula. The fee is already known data, not something to re-derive:
```
predicted_settlement = amount - rzp_fee
```
**Do not recompute fee via `amount * 0.02 * 1.18`.** That formula is only how the dataset generator *creates* the fee in the first place (Section 3) — once the data exists, the real `rzp_fee` column is always more accurate than re-deriving it, and real payment gateways use tiered/variable pricing that a flat formula wouldn't capture anyway.

For `partial_refund_split` cases (`status == "partially_refunded"`), also subtract `refund_amount` — a **dedicated numeric column in `internal_ledger.csv`** (Section 3), not something to parse out of the free-text `notes` field:
```
predicted_settlement = amount - rzp_fee - refund_amount
```
With the actual refund amount available as structured data, `partial_refund_split` cases resolve deterministically at Agent 3 — this is no longer a known limitation requiring `UNRESOLVED` fallback (see Section 10's updated status).

Filter bank rows to a shortlist where `settlement_date` falls within `captured_at + 0 to 10 days` and `settlement_amount` is within a small tolerance (start ±₹5) of `predicted_settlement`.

**3b. Composite scoring.** For every (Razorpay, candidate bank row) pair:
```
score = (w1 * amount_score) + (w2 * date_score) + (w3 * text_score)
```
Suggested starting weights: amount 0.45, date 0.30, text 0.25 — tune against the dev set, log the tuning process.

**3c. Global one-to-one assignment via the Hungarian algorithm — not a greedy loop.** Collect all candidate pairs across the entire unmatched set into a cost matrix and solve with `scipy.optimize.linear_sum_assignment`. This guarantees the mathematically optimal one-to-one assignment, which matters specifically for `duplicate_capture` (two Razorpay candidates competing for one bank row) and `adversarial_near_miss` (two different real transactions with close amounts) — a greedy highest-score-first approach can lock in a locally-good pair that blocks a better global assignment elsewhere, while the Hungarian algorithm can't make that mistake by construction. `scipy` is already a project dependency (Section 7); there's no reason to settle for the weaker greedy version when the correct one costs almost nothing extra to implement.

**Early exit:** score ≥ 0.90 → STOP → `MATCHED`. Records with zero candidates, or a top score below ~0.5, do not proceed to Agent 4 — they route directly per Agent 6's table (Section 6). Mid-confidence records (0.50–0.90) proceed to Agent 4.

---

### Agent 4 — LLM Reasoning Agent (this is where Semantic Matching lives)
**Purpose:** Handle the genuinely ambiguous slice left after Agents 2–3 — target under 15-20% of total records.

**This agent is the "Semantic Matching" layer in the Exact → Fuzzy → Semantic → Confidence Engine framework.** Agent 3's fuzzy stage is character-level (via `rapidfuzz`) — it catches things like `"RAZORPAY TECHNOLOGIES"` vs `"Razorpay Tech"`, where the text is *spelled* similarly. It will correctly fail to catch `"FITZONE WELLNESS PVT LTD"` vs `"Monthly gym membership renewal"` — completely different wording, same real-world transaction. That gap is exactly what this agent exists to close: not "do these look similar," but "do these refer to the same thing."

**Required context — this agent needs more than just the two records being compared.** Always include `MERCHANT_PROFILE` (Section 3/6C — this merchant's brand name and registered legal name) in Agent 4's prompt, every call, not just for semantic cases. For most records it's irrelevant and simply unused. For `semantic_brand_narration` records specifically, it's the fact that makes the match solvable at all — without it, there is nothing connecting `"gym membership renewal"` to `"FITZONE WELLNESS PVT LTD"`, since this is a fictional small business, not something the LLM would recognize from training data. This mirrors a real production system, which would know its own merchant's registered identity from onboarding/KYC records — it's a realistic capability, not a shortcut.

**This isn't a hypothetical capability — the dataset's `semantic_brand_narration` case type (Section 3) exists specifically to force this signal to be exercised for real.** Be honest about the rest of the dataset: `hard_garbled_narration` needs amount/date reasoning because its text is deliberately near-meaningless, not because it's semantically paraphrased — most of the dataset's records never actually require meaning-level understanding to resolve. `semantic_brand_narration` is the one case type where `semantic_similarity` is the deciding factor, and it's the one to point to when demonstrating this capability, rather than claiming credit for solving cases that were really solved by amount/date logic alone.

**Model tier:** fast/light tier (see Section 6A) — high volume of small, well-scoped decisions.

**Prompting approach:**
- *Role framing:* "You are a senior reconciliation analyst... deliberately conservative: a false match corrupts the financial ledger and is worse than an honest 'uncertain' response."
- *Risk-asymmetry, stated explicitly:* a false match is worse than a missed match correctly left for human review.
- *Few-shot calibration:* at minimum 3 worked examples in the prompt — a clean match, a clean non-match, and this exact semantic case (lift it verbatim, it's a good one — note it explicitly includes the merchant profile fact, since that's what makes it solvable):
  ```
  Merchant profile (known fact, always available): brand_name = "FitZone Gym",
  registered_legal_name = "FitZone Wellness Private Limited"

  Ledger: Amount = ₹2,499, Date = 12 Aug, notes = "Monthly gym membership renewal"
  Bank:   Amount = ₹2,449 (net of fee), Date = 13 Aug, narration = "FITZONE WELLNESS PVT LTD"

  Exact match: fails (no shared reference)
  Fuzzy match: near-zero text similarity, not confident enough alone
  Semantic match: "FITZONE WELLNESS PVT LTD" is this merchant's own registered legal
  name (per merchant profile) — the same business as "FitZone Gym," so a gym
  membership charge settling under that name is expected, not a coincidence
  Combined with a correct fee-adjusted amount and a 1-day settlement gap →
  high-confidence match, even though the text never overlaps
  ```
  This example exists specifically to calibrate the model toward recognizing meaning over spelling, which is the entire point of this agent's existence — and it's drawn directly from the dataset's actual `semantic_brand_narration` case type (Section 3), not a hypothetical.
- *Constrained chain-of-thought:* brief reasoning (1–2 sentences), capped explicitly.
- *Structured output, enforced:* native JSON mode / tool-calling, never "please respond in JSON" as plain-text instruction.

**Output contract — `semantic_similarity` is a required, separately-scored field, not folded into `reasoning` text:**
```json
{
  "record_id": "...",
  "candidate_ids": ["...", "..."],
  "semantic_similarity": 0.0,
  "decision": "match | no_match | uncertain",
  "confidence": 0.0,
  "reasoning": "1-2 sentence explanation",
  "risk_flags": ["weak_narration", "amount_at_tolerance_edge"]
}
```
`semantic_similarity` (0.0–1.0) specifically answers "do these descriptions refer to the same real-world entity/event," independent of `confidence` (which reflects the agent's overall certainty in the match decision, combining semantic similarity with amount/date fit). Agent 6's Confidence Engine (Section 5/6B) consumes both as separate inputs, exactly as your three-layer framework specifies — semantic similarity is a signal *feeding* the confidence decision, not the decision itself.

**Guardrails:** mask/truncate sensitive fields before prompting; hard token budget per call; timeout + single retry, then route to human queue on repeated failure — never block the batch.

---

### Agent 5 — Verifier Agent (independent second opinion)
**Purpose:** Independent LLM call reviewing the same underlying data Agent 4 saw — not Agent 4's output/reasoning, to avoid biasing toward agreement.

**Run conditionally, not on every record Agent 4 touches — but get the condition right, because the naive version of this optimization is actually backwards.** A tempting shortcut is "only verify when Agent 4's confidence is in a mid-range grey zone (e.g. 0.65–0.85), skip verification when confidence is high." **Do not implement it that way.** The entire point of an independent verifier is to catch Agent 4 being *confidently wrong* — skipping verification specifically when confidence is high removes the safety net exactly where an overconfidence failure would be most dangerous and least likely to be caught any other way.

The correct condition instead:
```python
skip_verification = (
    agent_4_result.confidence >= 0.95
    and transaction_amount < 10_000  # well below the mandatory ₹50,000 review gate
)
```
Skip Agent 5 only when the match is both *very* high confidence **and** low-value — the one combination where the cost of being wrong is genuinely small and the likelihood is genuinely low. Any transaction with real money at stake, or any confidence below 0.95, still gets independently verified. This is not the same trade-off as the confidence-band version — it preserves the core safety story (Section 10, standout feature 4) while still meaningfully cutting the number of low-stakes verification calls on a batch with many small, obviously-clean transactions.

**"Same underlying data" (for records that do get verified) explicitly includes `MERCHANT_PROFILE` (Section 5, Agent 4) — this is not optional for Agent 5.** If Agent 4 receives the merchant's registered legal name but Agent 5 doesn't, Agent 5 has no way to independently solve a `semantic_brand_narration` case — it would correctly (from its own perspective) call it "uncertain" or "no_match" purely because it's missing a fact, not because it disagrees on the merits. That produces a false `agent_disagreement`, sending a record to human review that should have cleanly resolved to `MATCHED`. Both agents must receive identical context; only their reasoning is meant to be independent.

**Model tier:** same or stronger than Agent 4 (Section 6A) — its job is catching Agent 4's mistakes.

**Output contract:**
```json
{
  "record_id": "...",
  "independent_decision": "match | no_match | uncertain",
  "independent_confidence": 0.0,
  "agrees_with_agent_4": true,
  "verifier_notes": "..."
}
```

**Routing consequence:** disagreement between Agent 4 and Agent 5 → automatically `UNRESOLVED`, full stop, regardless of individual confidence scores.

---

### Agent 6 — Confidence Router
**Purpose:** The single, explicit, deterministic policy table. This *is* your "bounded and gated" claim made literal — no LLM here. In addition to `status` and `sub_reason`, this agent's output must include the full `explanation` object defined in Section 6D — assembled via templating from signals already computed upstream (Agent 3's amount/date/text scores, Agent 4/5's confidence and reasoning), never a new LLM call.

**Output is always one of three statuses — never binary (Section 0C.1):**

| Status | Meaning |
|---|---|
| `MATCHED` | Full expected reconciliation achieved, with confidence |
| `PARTIAL` | Some expected sources linked; absence of the rest is explainable, not an error — always carries `sub_reason` |
| `UNRESOLVED` | Genuine ambiguity or low confidence — routed to human review |

**Routing table:**

| Condition | Status | sub_reason (if applicable) |
|---|---|---|
| Exact match (Agent 2), all expected sources present | `MATCHED` | — |
| Fuzzy match, score ≥ 0.90, all expected sources present | `MATCHED` | — |
| Ledger+Razorpay matched via `order_id`, no bank candidate, `(AS_OF_DATE - captured_at).days <= 10` | `PARTIAL` | `"awaiting_settlement"` |
| Razorpay+Bank matched each other with high confidence, no ledger row found | `PARTIAL` | `"no_ledger_record"` |
| Ledger+Razorpay matched, no bank candidate, `(AS_OF_DATE - captured_at).days > 10` | `UNRESOLVED` | `"overdue_settlement"` |
| Fuzzy match, 0.50 ≤ score < 0.90 | → send to Agent 4 | — |
| Agent 4 met the skip-verification condition (Section 5, Agent 5 — confidence ≥ 0.95 and amount < ₹10,000), Agent 5 not run | `MATCHED` | — |
| Agent 4 = "match", Agent 5 agrees, combined confidence ≥ 0.85, all expected sources present | `MATCHED` | — |
| Agent 4 and Agent 5 disagree | `UNRESOLVED` | `"agent_disagreement"` |
| Any confidence < 0.85 after Agent 4/5 | `UNRESOLVED` | `"low_confidence"` |
| Transaction amount ≥ ₹50,000, regardless of confidence | `UNRESOLVED` | `"high_value_review_required"` — applies even to otherwise-`MATCHED` records, no exceptions |
| Ledger only, status = `failed`, no Razorpay/Bank record | `MATCHED` | `"no_action_needed"` |
| Bank record with genuinely zero plausible Razorpay candidates (not merely below threshold — no candidate exists within any reasonable amount/date window at all) | `UNRESOLVED` | `"unidentified_bank_credit"` — distinct from the generic no-candidates case below, since an unexplained bank credit specifically warrants a human's attention, unlike a Ledger/Razorpay-side record with nothing downstream |
| No candidates found, none of the above apply | `UNRESOLVED` | `"no_candidates_found"` |

**Every `AS_OF_DATE`-relative comparison in this table uses the fixed value from Agent 0 — never the real-world current date (Section 0C.2).**

Write this table into your actual code as literal, readable conditional logic — not a black-box scoring function.

---

### Agent 7 — Audit Trail Logger
**Purpose:** Append-only log, written at every stage (not retrofitted at the end): timestamp, agent name, record(s), action, confidence, reasoning, tokens used, latency, resulting status + sub_reason.

**Schema:**
```
log_id | timestamp | record_id | agent_name | action | status | sub_reason | confidence | tokens_used | latency_ms | log_notes
```
**Naming note:** this column is deliberately called `log_notes`, not `notes` — the ledger source data already has its own `notes` field (Section 5, Agent 1) and Agent 5 has `verifier_notes`. Keeping these three distinct avoids a real risk of one silently overwriting another when audit rows are assembled from multiple agents' outputs.

---

### Agent 8 — Reporting & Scoring Agent (this is where you prove your accuracy)

**Purpose:** The only agent permitted to read `ground_truth.json`.

**Ground truth mapping (the authoritative three-state definition — use this, never a boolean):**

| Case type | Expected status | Expected sub_reason |
|---|---|---|
| clean_triple_match, delayed_settlement, hard_garbled_narration, duplicate_capture, adversarial_near_miss | `MATCHED` | — |
| partial_refund_split | `MATCHED` — resolves deterministically at Agent 3 using the real `refund_amount` column (Section 5, Agent 3), regardless of refund percentage | — |
| pending_settlement | `PARTIAL` | `"awaiting_settlement"` |
| missing_from_ledger | `PARTIAL` | `"no_ledger_record"` |
| failed_payment_orphan | `MATCHED` | `"no_action_needed"` |
| unidentified_bank_credit | `UNRESOLVED` | `"unidentified_bank_credit"` |
| semantic_brand_narration | `MATCHED` | — (resolved via Agent 4's `semantic_similarity`, not Agent 2/3) |

**If `semantic_brand_narration` cases are NOT resolving to `MATCHED`:** check two things, in order. First, that Agent 4 (and Agent 5) actually received the ledger `notes` field in their prompt context. Second — and this is the one most likely to be missed — that `MERCHANT_PROFILE` (Section 6C) is actually included in *both* agents' prompts, not just Agent 4's. Missing it on Agent 5 specifically produces a subtle failure: Agent 4 correctly matches, Agent 5 (lacking the merchant identity fact) can't independently confirm it, and the record gets wrongly routed to `agent_disagreement` instead of `MATCHED` — which looks like a disagreement bug but is actually a missing-context bug.

**Required outputs:**
1. **Held-out test methodology:** dev set (tune against this) + held-out test set (touch only once, at the end)
2. **Multi-class precision/recall/F1** across `MATCHED` / `PARTIAL` / `UNRESOLVED`, via `sklearn.metrics.classification_report` — never a binary comparison
3. **Confusion matrix**, broken down by case type
4. **Record identity invariant check (Section 0C.3)** — runs automatically at the end of every scoring pass; verifies the exact set of output record IDs matches the exact set of input record IDs (catches both silent drops and cross-bucket duplicates, not just a count mismatch); halts and reports the specific record IDs involved if it fails
5. **Throughput report** by resolution stage (exact / fuzzy / LLM / human-routed)
6. **Dashboard summary:**
```
{records_processed} records processed
Matched:      {count}
Partial:      {count}
Unresolved:   {count}
Match Rate:   {matched / total * 100}%
Processing time: {total pipeline runtime in seconds}
```
7. **Exception list:** every `UNRESOLVED` record with its full `explanation` object (Section 6D — headline, checklist, days_elapsed, recommendation), not just `sub_reason`, sorted by amount descending
8. **Cost report:** total LLM calls, tokens, estimated cost, % of records resolved without any LLM call

---

### Agent 9 — Settlement Q&A Agent (build last, only once everything above is solid)
**Purpose:** Chat interface over the finished, reconciled database. Answers must always be grounded in the actual database row (including its status and sub_reason), never inferred or guessed. If a record is `PARTIAL` or `UNRESOLVED`, say so plainly rather than fabricating certainty.

**This is where semantic search genuinely earns its place — distinct from Agent 4.** It's worth being precise about the difference, since it's easy to conflate the two:
- **Agent 4's `semantic_similarity`** answers *"do these two specific candidate records refer to the same thing"* — a comparison between two known records. This is a classification decision, and an LLM call already handles it well without needing a vector database at all.
- **Agent 9's job** is *"find the records relevant to this open-ended question"* — e.g. a user asks *"any gym membership payments this week?"* and the system needs to search across potentially hundreds of records for ones whose description semantically relates to the query, even when wording differs (`"FITZONE WELLNESS PVT LTD"` vs the user typing "gym membership"). This is a genuine retrieval problem across a whole corpus, which is exactly what vector databases are built for — Agent 4 never needed one, Agent 9 does.

**Stack, free and fully local — no API cost, no extra account to set up:**
- **Vector DB: ChromaDB.** Runs embedded in-process (no separate server to stand up or manage — important given hackathon time constraints), free and open-source, persists locally to disk, supports metadata filtering (e.g. "only `PARTIAL` records" or "only transactions above ₹10,000" alongside the semantic query) which you'll want for realistic Q&A.
- **Embedding model: `sentence-transformers` (`all-MiniLM-L6-v2`).** Runs locally on CPU, free, no API key, no per-call cost — consistent with the provider-agnostic, cost-conscious philosophy already established in Section 6A. At this dataset's scale (hundreds, not millions, of records), embedding the whole reconciled dataset takes seconds and needs no GPU.

**How it works, concretely:**
1. After Agent 8 finishes, embed every reconciled record's combined text (customer name, narration, order reference, **and ledger `notes` when present**) and store it in a ChromaDB collection alongside its metadata (status, sub_reason, amount, date). Including `notes` here matters specifically for `semantic_brand_narration` records — without it, a question like "any gym membership payments this month?" wouldn't retrieve them, since the merchant's registered name only appears in the bank narration while the connecting context lives in `notes`.
2. When a user asks a question, embed the query the same way, retrieve the top-k semantically similar records (with optional metadata filters), and pass only those retrieved records — never the whole database — to a lightweight LLM call to phrase a grounded answer.
3. The LLM's job here is narrow: summarize what was retrieved, never invent details beyond it. If nothing relevant is retrieved, say so plainly rather than guessing.

**Model tier:** fast/light tier (Section 6A) for the answer-phrasing step — same reasoning as Agent 4/Agent 9's other LLM usage, this is a grounded summarization task, not deep reasoning.

---

## 6. Model Selection Summary

| Agent | Needs LLM? | Recommended tier |
|---|---|---|
| 0. AS_OF_DATE | No | — |
| 1. Ingestion | No | — |
| 2. Exact Match | No | — |
| 3. Fuzzy Match | No | — |
| 4. LLM Reasoning | Yes | Fast/light tier |
| 5. Verifier | Yes | Same or stronger tier |
| 6. Router | No | — |
| 7. Audit Logger | No | — |
| 8. Reporting | No | — |
| 9. Q&A Agent | Yes | Fast/light tier |

---

## 6A. LLM Provider Strategy

**Hybrid, provider-agnostic:**
- **Development (writing/debugging prompts, schemas, pipeline logic):** local **Ollama** model. Free, unlimited, works offline.
- **Test runs and live demo (numbers you report to judges):** hosted **Groq** free tier. Fast, free, and comfortably within this project's actual call volume.
- **Never hardcode a provider inside an agent file.** Every agent calls one shared `call_llm()` function; provider/model is chosen by config.

### Provider comparison

| | Local (Ollama) | Groq (free tier) |
|---|---|---|
| Cost | Free, unlimited | Free, rate-limited |
| Speed | Depends on hardware | Very fast |
| Structured-output reliability | Weaker on 7-8B models | Good, especially 70B/120B |
| Internet dependency | None | Required |

### Recommended models

| Agent | Model |
|---|---|
| Agent 4 (Reasoning) | Groq `llama-3.3-70b-versatile` or `gpt-oss-20b` |
| Agent 5 (Verifier) | Groq `gpt-oss-120b` — strongest available free-tier option |
| Agent 9 (Q&A) | Same as Agent 4 |
| Development only | Ollama `llama3.1:8b-instruct` or `qwen2.5:7b-instruct` — never use for final reported accuracy numbers |

### Rate-limit math (Groq `gpt-oss-120b` free tier: 30 req/min, 1,000 req/day, 8K tokens/min, 200K tokens/day)

**Which case types actually reach Agent 4 — be precise here, don't just guess a percentage.** With the `rzp_fee`/`refund_amount` fixes (Section 5, Agent 3), `partial_refund_split` now resolves deterministically and never reaches Agent 4. `hard_garbled_narration` and `semantic_brand_narration` are designed to fail Agent 3's fuzzy threshold on purpose — those genuinely need it. `adversarial_near_miss` uses deliberately weak narration too, so expect most of these to land in Agent 4's band as well. Everything else (clean matches, duplicates, pending/missing/orphan/unidentified cases) should resolve at Agent 2/3/6 without ever calling an LLM.
```
Full dataset (550): hard_garbled_narration (50) + adversarial_near_miss (20)
                     + semantic_brand_narration (15) ≈ 85 records reach Agent 4
                     ≈ 15-16% of total, down from the earlier ~18% estimate
                     — the difference is entirely the 25 partial_refund_split
                     records that now resolve for free at Agent 3
```
With Agent 5's skip condition (confidence ≥ 0.95 AND amount < ₹10,000 — Section 5, Agent 5) cutting a further meaningful chunk of the verification calls on realistic data, and concurrent execution (25 req/min) instead of sequential calls, a full run completes comfortably within Groq's free tier. **With response caching, a repeated run touching only already-resolved records completes in seconds** — this is the change that actually matters for iterative development, not the raw call count. Always verify current limits in the Groq console before the final build; these change over time, and treat the numbers above as a planning estimate to check against your own actual run, not a guarantee.

### Required: provider-agnostic wrapper, built before Agent 4
```python
def call_llm(prompt: str, schema: BaseModel, provider: str = "groq", model: str = None) -> BaseModel:
    """
    Routes to Ollama / Groq based on `provider`. Always returns a
    schema-validated Pydantic object, never raw text. Implements:
    timeout, single retry with backoff, hard token budget per call.
    On repeated failure, raises a specific exception the calling agent
    catches and routes the record to UNRESOLVED / human review —
    never lets the batch crash.
    """
```

### Required: response caching — this is what actually fixes "hitting rate limits during development"

**The real cost problem isn't one production run — it's re-running the same records against the LLM over and over while debugging and tuning thresholds.** A single full run is a few minutes and well within Groq's free tier (Section 6A's rate-limit math). What actually burns through daily token limits is re-triggering Agent 4/5 for records that already resolved correctly on a previous run, every single time you restart the pipeline to test a small change elsewhere.

Cache every LLM call, keyed on `(record_id, prompt_hash)`, to a local SQLite table or even a flat JSON file — on a repeated run, if the same record with the same prompt content has already been resolved, return the cached result instead of calling the LLM again. Invalidate the cache entry only when the prompt content actually changes (e.g. you edited the few-shot examples or `MERCHANT_PROFILE`) — the hash catches this automatically, no manual cache-clearing needed. This single change is likely to cut real-world token usage during development far more than any per-call optimization, since most re-runs during iterative debugging touch code paths that have nothing to do with Agent 4/5 at all.

### Required: concurrent execution with a rate limiter, not sequential calls

Execute Agent 4 (and Agent 5, for records that aren't skipped) calls concurrently — Python's `asyncio` or a `ThreadPoolExecutor`. **The rate limiter must track actual token consumption per minute, not just request count.** Testing during development surfaced this precisely: Groq's `gpt-oss-20b` free tier allows 30 requests/minute, but only 8,000 *tokens*/minute — a burst of even a dozen concurrent calls (~500 tokens each) can exhaust the token budget in under a second while staying nowhere near the request-count limit. A rate limiter gated only on request count will pass that check and still get throttled. Track cumulative tokens sent in the trailing 60-second window (estimate from prompt length before sending, correct from the actual response afterward) and pause new calls only when close to the ~8K/minute ceiling — this lets calls fire as fast as the token budget genuinely allows, rather than a blunt fixed delay between every call regardless of size. This doesn't change what gets computed or any decision logic, purely how fast the same work completes.

### Optional further optimization — not required, only if there's spare time
Batching multiple records into a single Agent 4 call (e.g. reason about 5 records at once, expect 5 structured outputs back) cuts total request count further, which helps specifically against Groq's *requests*-per-minute limit rather than its token limit. This adds real complexity — correctly attributing a retry to one failed record within a batch without re-calling the whole batch is fiddly — so treat this as a nice-to-have if the fixes above already solve your actual problem, not something to build defensively. The caching, conditional-skip, and fee/refund fixes above should already resolve the latency and rate-limit issues on their own; don't add batching complexity unless you've confirmed it's still needed after those are in place.

---

## 6B. Status & Action Reference — What Actually Happens To Each Record

Section 5's Agent 6 routing table decides *what a record is called*. This table defines *what happens to it next* — every status and sub_reason must map to a concrete, visible action, not just a label sitting in a database.

| Status | sub_reason | What it means | Action the system takes | What a human does |
|---|---|---|---|---|
| `MATCHED` | — (full reconciliation) | All expected sources reconcile with confidence | Written to the reconciled database as confirmed; counted in the "Matched" number on the dashboard | Nothing — fully automated |
| `MATCHED` | `no_action_needed` | Payment attempt failed; nothing ever needed to reconcile | Logged as correctly resolved; excluded from the exception list entirely | Nothing |
| `PARTIAL` | `awaiting_settlement` | Ledger+Razorpay confirmed; bank settlement hasn't landed yet (normal timing) | Held in a "pending" state; automatically re-checked on the next pipeline run — if the bank record has since arrived, it resolves to `MATCHED` on its own | Nothing yet — only escalates to `UNRESOLVED` (`overdue_settlement`) if it crosses the 10-day threshold on a later run |
| `PARTIAL` | `no_ledger_record` | Razorpay+Bank confirm real money moved, but no internal ledger entry exists | Added to a separate "gap list" for ops follow-up — deliberately *not* the same urgent queue as `UNRESOLVED`, since no immediate money-safety decision is required | Ops investigates why the internal system didn't log it (usually a webhook/integration issue) |
| `UNRESOLVED` | `overdue_settlement` | Ledger+Razorpay confirmed, but bank settlement is more than 10 days late | Added to the human review queue | A human checks with the bank/gateway on the delayed settlement |
| `UNRESOLVED` | `agent_disagreement` | Agent 4 and Agent 5 (the two independent LLM reasoning passes) reached different conclusions | Added to the human review queue with both agents' reasoning shown side by side | A human makes the final call |
| `UNRESOLVED` | `low_confidence` | LLM reasoning was uncertain, combined confidence below 0.85 | Added to the human review queue with the reasoning/explanation attached | A human approves, rejects, or manually links the correct record |
| `UNRESOLVED` | `high_value_review_required` | Transaction ≥ ₹50,000, regardless of confidence | Added to the human review queue automatically — a hard rule with no exceptions, even an otherwise-confident match still needs sign-off | A human specifically confirms high-value transactions |
| `UNRESOLVED` | `unidentified_bank_credit` | A bank credit with no plausible counterpart anywhere | Added to the human review queue | A human identifies where the money actually came from |
| `UNRESOLVED` | `no_candidates_found` | Nothing to go on at all after every matching stage has run | Added to the human review queue as a catch-all | A human investigates from scratch |

**What happens after a human acts on an `UNRESOLVED` record (build this into the Human Review Queue UI, Section 11 step 15):**
- **Approve** → the record is manually confirmed and moves to `MATCHED` (or `PARTIAL`, if that's what the human determines), with an audit log entry noting it was human-approved, not machine-confirmed
- **Reject** → the record stays flagged but is marked "confirmed no match" — removed from the active queue, but never deleted, so the decision itself is part of the permanent audit trail
- **Manual link** → the human specifies the correct match directly; this becomes the final answer for that record and is logged as a human override, distinct from an automated decision

---

## 6C. Centralized Configuration — One File, No Scattered Magic Numbers

Every tunable threshold in this system — fuzzy-match weights, confidence cutoffs, the high-value review amount, the overdue-days window, LLM token/retry budgets — must live in a single `agents/config.py`, not hardcoded inline across multiple agent files. This matters for three concrete reasons: it makes tuning during dev-set iteration a one-file change instead of a hunt across the codebase, it makes the whole decision policy inspectable at a glance for judges, and it prevents the exact class of bug where a threshold is updated in one place but an old copy still lives somewhere else.

```python
# agents/config.py — the single source of truth for every tunable value
FUZZY_MATCH_WEIGHTS = {"amount": 0.45, "date": 0.30, "text": 0.25}
FUZZY_AUTO_MATCH_THRESHOLD = 0.90
FUZZY_MIN_CANDIDATE_THRESHOLD = 0.50
SETTLEMENT_DATE_TOLERANCE_DAYS = 10
OVERDUE_SETTLEMENT_DAYS = 10
AMOUNT_TOLERANCE_RUPEES = 5.0
HIGH_VALUE_REVIEW_THRESHOLD_RUPEES = 50_000
LLM_CONFIDENCE_AUTO_CONFIRM = 0.85
LLM_MAX_TOKENS_PER_CALL = 500
LLM_TIMEOUT_SECONDS = 15
LLM_MAX_RETRIES = 1
PARTIAL_REFUND_TOLERANCE_PCT = 0.50

# This merchant's own identity — required context for Agent 4 and Agent 5
# (Section 5) to resolve semantic_brand_narration cases (Section 3). Include
# this in every Agent 4/5 prompt, not conditionally — for most records it's
# simply unused, but it's a known fact a real production system would have
# from merchant onboarding/KYC, not something either agent should guess.
MERCHANT_PROFILE = {
    "brand_name": "FitZone Gym",
    "registered_legal_name": "FitZone Wellness Private Limited",
}
```

Every agent imports from this file rather than restating a value. When Section 8's dev-set tuning changes a threshold, it changes here, once.

---

## 6D. Explanation Generation — A Structured Reason for Every Single Record

This is not optional polish — it's the difference between a system that outputs a label and a system that behaves like an actual finance controller. Every record, regardless of status, must carry a structured `explanation` object, not just a `status` and `sub_reason` code. This is what powers the exception queue detail view, the click-to-expand transaction panel, and it's the single strongest thing to show a judge who asks "how do I know this isn't a black box."

**Critical design decision: this is generated by deterministic templating, not another LLM call.** Every signal needed already exists by the time Agent 6 runs — amount difference, date gap, text/semantic scores, confidence, Agent 4/5's reasoning if applicable. Agent 6 assembles these into the explanation using string templates, at zero additional cost and zero additional latency. Do not add an LLM call for this — it would be pure waste, re-explaining something the pipeline already knows.

### The `explanation` object — attached to every record, part of Agent 6's output

```json
{
  "headline": "MATCHED — 94% confidence",
  "checklist": [
    {"passed": true,  "label": "Amount matches (₹10,000.00 vs ₹10,000.00)"},
    {"passed": true,  "label": "Transaction date within 1 day"},
    {"passed": true,  "label": "Semantic similarity: description clearly refers to the same payment"},
    {"passed": false, "label": "Reference ID only partially matches"}
  ],
  "risk_flags": ["Merchant name differs slightly"],
  "days_elapsed": null,
  "recommendation": null,
  "confidence": 0.94
}
```

`days_elapsed` is `(AS_OF_DATE − relevant record date).days` (Section 0C.2's `AS_OF_DATE`, never the real clock) — populated for any `PARTIAL`/`UNRESOLVED` status where elapsed time is part of the reason, `null` otherwise. `recommendation` is a one-line, specific next action — `null` for `MATCHED`, always populated for `PARTIAL`/`UNRESOLVED`.

### Template reference — exact wording per status/sub_reason

| Status | sub_reason | Headline | Checklist source | Recommendation |
|---|---|---|---|---|
| `MATCHED` | — (exact, Agent 2) | `"MATCHED — 100% confidence"` | ✓ Amount matches exactly, ✓ Order ID matches exactly | `null` |
| `MATCHED` | — (fuzzy, Agent 3) | `"MATCHED — {confidence}% confidence"` | ✓/✗ Amount (show ₹ diff if any), ✓/✗ Date (show day gap), ✓/✗ Text similarity | `null` |
| `MATCHED` | — (LLM-resolved, Agent 4/5) | `"MATCHED — {confidence}% confidence"` | ✓/✗ Amount, ✓/✗ Date, ✓/✗ Semantic similarity ({semantic_similarity}%) — plus any `risk_flags` from Agent 4 | `null` |
| `MATCHED` | `no_action_needed` | `"MATCHED — No action needed"` | ✓ Payment attempt failed, no downstream record was ever expected | `null` |
| `PARTIAL` | `awaiting_settlement` | `"PARTIAL — Awaiting settlement ({days_elapsed} days)"` | ✓ Ledger confirms order, ✓ Gateway confirms capture, ⏳ Bank settlement pending ({days_elapsed}/{OVERDUE_SETTLEMENT_DAYS} days) | `"No action needed yet — will auto-resolve on a later run"` |
| `PARTIAL` | `no_ledger_record` | `"PARTIAL — No ledger record"` | ✓ Gateway confirms capture, ✓ Bank confirms settlement (₹{amount}), ✗ No matching ledger entry found | `"Flag for ops: check integration/webhook logs"` |
| `UNRESOLVED` | `overdue_settlement` | `"UNRESOLVED — Settlement overdue ({days_elapsed} days)"` | ✓ Ledger confirms order, ✓ Gateway confirms capture, ✗ Bank settlement overdue by {days_elapsed} days (threshold: {OVERDUE_SETTLEMENT_DAYS}) | `"Contact bank/gateway regarding the delayed settlement"` |
| `UNRESOLVED` | `agent_disagreement` | `"UNRESOLVED — AI reasoning conflict"` | Show both: "Agent 4: {decision} ({confidence}%)" and "Agent 5: {independent_decision} ({independent_confidence}%)" | `"Human review required — the two independent reasoning passes disagreed"` |
| `UNRESOLVED` | `low_confidence` | `"UNRESOLVED — {confidence}% confidence"` | ✓/✗ per signal (amount/date/semantic), plus Agent 4's `reasoning` text verbatim | `"Review and confirm or reject the suggested match"` |
| `UNRESOLVED` | `high_value_review_required` | `"UNRESOLVED — High value (₹{amount})"` | ✓ Match confidence {confidence}%, ⚠ Exceeds ₹{HIGH_VALUE_REVIEW_THRESHOLD_RUPEES} review threshold | `"Mandatory sign-off required regardless of match confidence"` |
| `UNRESOLVED` | `unidentified_bank_credit` | `"UNRESOLVED — Unidentified credit"` | ✗ No matching ledger entry, ✗ No matching gateway record, ✓ Amount ₹{amount} on {date} | `"Identify the source of this credit"` |
| `UNRESOLVED` | `no_candidates_found` | `"UNRESOLVED — No match found"` | ✗ No plausible candidates in any source | `"Investigate manually — no automated signal to go on"` |

**The exact-₹-amount-difference pattern (e.g. `"Amount differs by ₹4,200 (Bank: ₹14,500, Expected: ₹18,700)"`) applies specifically when a near-miss candidate existed but failed tolerance** — this is a checklist item, not a separate template: whenever `amount_score` failed but a candidate was still the closest option considered, show the real numbers, not just a checkmark. This is what makes the exception queue useful instead of just accurate — a human reading it immediately knows what to go check, without opening the raw data.

### Where this shows up
- **Human review queue (Section 11 step 15):** the primary UI for this — every queued record shows its full `explanation` object
- **Click-to-expand transaction detail:** clicking any record, `MATCHED` included, shows this same object — consistency matters here, don't build a different explanation format for matched vs. unresolved records
- **Agent 8's exception list (Section 5, Agent 8):** each entry includes the full `explanation`, not just `sub_reason` — this is what turns a bare list of IDs into something a finance team could actually act on
- **Agent 7's audit trail:** logs the `explanation` alongside every decision, so the full reasoning is permanently attached to the audit record, not reconstructed after the fact

---

## 7. Tech Stack

| Layer | Choice |
|---|---|
| Language | Python 3.11+, always inside `venv/` (Section 0) |
| Data handling | Pandas |
| Fuzzy text matching | RapidFuzz |
| Schema validation | Pydantic |
| Optimal assignment (stretch) | scipy (`linear_sum_assignment`) |
| Database | SQLite |
| Vector DB (Agent 9 only) | ChromaDB — free, embedded, no separate server |
| Embeddings (Agent 9 only) | `sentence-transformers` (`all-MiniLM-L6-v2`) — free, local, no API cost |
| Backend API | FastAPI |
| Frontend | React |
| Metrics | scikit-learn (`classification_report`, `confusion_matrix`) |
| LLM providers | Groq (test runs & demo) + Ollama (development) via shared `call_llm()` |
| Retry/backoff | `tenacity` |
| Testing | `pytest` — regression tests for Section 0D, run before every dev-set scoring pass |
| Logging | Python `logging` module (not `print`), feeding Agent 7's audit trail directly |
| Configuration | `agents/config.py` — every tunable threshold in one place (Section 6C) |
| Secrets | `.env` (never committed — see Section 0's `.gitignore`) |

---

## 8. Validation & Tuning Methodology

1. Generate the dataset once (reproducible, seed 42)
2. Split `ground_truth.json` cases into dev (~70%, visible, for tuning) and held-out test (~30%, touch once)
3. Build the pipeline, running Agent 8 continuously against the **dev set** as each piece comes online — including the Section 0C.3 record-count invariant, from the very first partial pipeline run, not added later
4. Run against the **held-out test set once**, near the end, and report that number as final
5. If held-out accuracy is meaningfully worse than dev accuracy, report that gap honestly — it's a sign of threshold overfitting, and saying so demonstrates rigor rather than undermining your submission

---

## 9. Explicit Safety / Guardrail Checklist

- [ ] `venv` active for every command run against this project (Section 0)
- [ ] `.env` used for all API keys, never hardcoded, never committed
- [ ] Every record resolves to exactly one of `MATCHED` / `PARTIAL` / `UNRESOLVED` — no binary shortcuts anywhere (Section 0C.1), enforced by `tests/test_three_state_output.py`
- [ ] `AS_OF_DATE` computed once at startup from the dataset itself, logged visibly, used for every elapsed-time comparison — `datetime.now()` never used for dataset-relative logic (Section 0C.2), enforced by `tests/test_as_of_date.py`
- [ ] Record identity invariant (set equality, not just count) runs automatically after every full pipeline execution (Section 0C.3), enforced by `tests/test_record_count_invariant.py`
- [ ] Full `pytest` suite passes before every dev-set scoring run, not just checked once at the end
- [ ] Every tunable threshold lives in `agents/config.py`, nowhere else (Section 6C)
- [ ] PII masking before any LLM call
- [ ] Hard token budget per LLM call
- [ ] Timeout + single retry on LLM calls, then fail gracefully to `UNRESOLVED` / human queue
- [ ] High-value transactions (≥ ₹50,000) always human-reviewed regardless of confidence
- [ ] Agent 4 / Agent 5 disagreement always routes to `UNRESOLVED`
- [ ] `ground_truth.json` imported only in `reporting_agent.py`
- [ ] No forced matches — anything below threshold becomes `UNRESOLVED`, never a guess
- [ ] All LLM calls go through the shared `call_llm()` wrapper — no agent calls a provider SDK directly
- [ ] Development used Ollama; final reported numbers came from the Groq models specified in Section 6A
- [ ] LLM responses cached by `(record_id, prompt_hash)` — a repeated pipeline run doesn't re-call the LLM for records that already resolved with unchanged prompt content (Section 6A)
- [ ] Agent 4/5 calls run concurrently with a rate limiter, not sequentially (Section 6A)
- [ ] Agent 5's skip condition requires BOTH high confidence (≥0.95) AND low value (<₹10,000) — never skips based on confidence alone (Section 5, Agent 5)
- [ ] Agent 3's `predicted_settlement` uses the actual `rzp_fee` column, never a recomputed formula (Section 5, Agent 3)
- [ ] `partial_refund_split` resolves via the real `refund_amount` column, not text parsed from `notes` (Section 3, Section 5 Agent 3)

---

## 10. Features That Will Make You Stand Out

**A real decision worth knowing the story behind, not just a rule:** during development, one `delayed_settlement` record combined to 0.84 confidence — just under the 0.85 auto-confirm threshold — and correctly routed to `UNRESOLVED`. The tempting fix was lowering the threshold to 0.82 to capture it. **Don't do this without full-dataset evidence.** One record is a data point, not a pattern — adjusting a global threshold to rescue a single known dev-set case is overfitting to an answer you've already seen, which is exactly what the held-out test set exists to prevent. Keep the threshold as-is, document the trade-off explicitly in Agent 8's reporting, and only revisit it if the full 550-record run shows a genuine cluster of similar cases — real evidence, not a single instance. This exact reasoning is worth stating directly to judges: it demonstrates the same discipline as the three-state model itself — resisting the urge to force a better-looking number at the cost of honest uncertainty.

1. **Real multi-class precision/recall/F1 on a held-out set, with a confusion matrix, per case type** — most teams won't do proper held-out evaluation at all, let alone a three-state one
2. **The three-state MATCHED/PARTIAL/UNRESOLVED model itself** — explicitly reject binary match/no-match in your write-up: *"We don't force every reconciliation outcome into match/no-match. 'Not yet settled' and 'genuinely ambiguous' require completely different actions, so our system distinguishes them."* This is a mature, production-grade insight almost no other team will articulate — and it's backed by a real story of finding and fixing the exact failure mode in your own dev-set numbers
3. **Structured, checklist-style explanations for every single record (Section 6D)** — not "MATCHED — 94%", but the actual ✓/✗ reasoning behind that number, the exact ₹ amount difference when relevant, days elapsed for anything time-based, and a specific one-line recommendation. Built entirely from signals the pipeline already computed, at zero extra LLM cost — this is the single most demo-friendly feature in the whole system, since it's the difference between "trust our number" and "here's exactly why," and it directly answers the track's own emphasis on explainability
4. **Agent 4 + Agent 5 independent verifier double-pass**, with visible disagreement handling
5. **Provably optimal one-to-one assignment via the Hungarian algorithm** (not a greedy approximation) — correctly resolves duplicate-capture and adversarial-near-miss cases with a mathematical guarantee, not a heuristic that happens to work on your test data
6. **Explicit, inspectable routing table** (Section 5, Agent 6) shown live in the demo
7. **Cost/efficiency reporting** — % of records resolved with zero LLM calls
8. **Refund-aware predicted-amount calculation** for `partial_refund_split` — resolved deterministically using a real `refund_amount` field, at any refund percentage, with zero LLM cost — most teams would either miss this case entirely or burn an LLM call reasoning about it
9. **The record identity invariant check itself** — a system that provably never silently loses or duplicates a record is a genuine trust signal for a finance product, and you can demonstrate it live by intentionally seeding a dropped/duplicated record and showing the check catch it
10. **An automated regression test suite (Section 0D)** guarding your core correctness invariants — most hackathon teams have zero tests; having four targeted tests that specifically encode "never binary," "never wall-clock time," "never lose a record," and "never skip verification just because confidence is high" shows engineering discipline judges can verify by literally running `pytest` themselves
11. **Provider-agnostic LLM layer** — runs fully offline on local models with a config flip, a real resilience point for a finance system
12. **A single, inspectable `agents/config.py`** holding every threshold in the system — you can open one file during Q&A and show a judge exactly every number your pipeline's decisions depend on, rather than making them hunt across files
13. **Conditional verification with a deliberately conservative skip rule** — Agent 5 only skips when a match is both very high confidence *and* low value, never on confidence alone. This is a genuinely defensible efficiency decision, and the reasoning ("skipping verification exactly when confidence is high defeats the purpose of having a verifier") is worth being able to explain if a judge probes it
14. **LLM response caching keyed on content, not just record ID** — a repeated pipeline run during development doesn't re-burn tokens on unchanged records, which is a real, unglamorous engineering detail most teams won't have thought about, but it's exactly the kind of thing that separates a demo that survives iteration from one that doesn't

**Scope decision worth stating explicitly, not hiding:** the track lists "Forward cash forecaster" and "Tax-line matcher" as additional example directions. This project deliberately covers only two — multi-source reconciliation and the Settlement Q&A agent — going deep with real held-out accuracy testing rather than shallow across four directions. If asked: *"We chose depth over breadth, given the track's own emphasis on measured accuracy over generation speed."*

---

## 11. Suggested Build Order

1. Environment setup (Section 0) — `venv`, `.env`, `.gitignore`
2. `agents/config.py` (Section 6C) — every tunable threshold, before any agent that uses one
3. Generate the 110-record dev subset (Section 3) alongside the full dataset — build and test everything below against the 110-record set first
4. `data_loader.py` — confirm it reads all three CSVs correctly (point it at `data/raw_100/` for now)
5. `AS_OF_DATE` computation (Agent 0) — print it, sanity-check it's within the dataset's real date range, not today's date; write `tests/test_as_of_date.py` (Section 0D) immediately after, not later
6. Agent 1 (Ingestion) + schema validation
7. Agent 2 (Exact Match) — first end-to-end pipeline run, even if crude
8. Agent 8 (Reporting) — wired up early, including the record identity invariant check from the very first run, even on partial output; write `tests/test_record_count_invariant.py` at this point, on 110 records this check is also verifiable by eye
9. Agent 3 (Fuzzy Match) — candidate generation → scoring → global assignment, in that order, tested against the 110-record set after each sub-step
10. `call_llm()` provider-agnostic wrapper — tested against both Ollama and Groq before Agent 4 is written; 110 records keeps LLM call volume trivial during this phase
11. Agent 4 (LLM Reasoning)
12. Agent 5 (Verifier)
13. Agent 6 (Router) — the three-state table, built as the only version, not upgraded from a binary one later; include the `explanation` object (Section 6D) from the start, not as a follow-up — it uses signals Agent 6 already has, so there's no reason to defer it; write `tests/test_three_state_output.py` immediately after
14. Agent 7 (Audit Trail) — should already be partially in place; complete it, including logging the `explanation` object alongside every decision
15. Human review queue UI + basic dashboard — every record's `explanation` (Section 6D) must render on click, `MATCHED` records included, using the same format as `UNRESOLVED` ones
16. Agent 9 (Q&A Agent)
17. **Switch from the 110-record set to the full dataset.** Re-run the entire pipeline end-to-end and confirm the full `pytest` suite (Section 0D) still passes, and the record identity invariant still holds at full scale — this is the point where a bug that only shows up under volume would surface, so don't skip re-verifying every agent's output here
18. Dev-set tuning against the full dataset (Section 8) — threshold changes go through `agents/config.py` only
19. Final held-out test run using the Groq-hosted models — report the real number, do not re-tune after seeing it

---

## 12. Folder Structure

```
ai-finance-controller/
├── venv/                      (never committed)
├── .env                        (never committed)
├── .gitignore
├── data/
│   ├── raw/                    (the full 550-record dataset — read-only, Section 3)
│   ├── raw_100/                (110-record dev subset, folder literally named raw_100 for historical consistency — read-only, used for all early build steps)
│   ├── ground_truth/           (ground_truth.json — reporting_agent.py only)
│   └── generator/               (generate_dataset.py)
├── agents/
│   ├── data_loader.py
│   ├── config.py                (every tunable threshold — Section 6C)
│   ├── as_of_date.py            (Agent 0 — Section 0C.2)
│   ├── llm_provider.py          (shared call_llm() wrapper — Section 6A)
│   ├── ingestion_agent.py
│   ├── exact_match_agent.py
│   ├── fuzzy_match_agent.py
│   ├── llm_reasoning_agent.py
│   ├── verifier_agent.py
│   ├── router.py                (three-state model, Section 5/6)
│   ├── audit_logger.py
│   ├── reporting_agent.py       (includes record identity invariant check)
│   └── qa_agent.py
├── tests/                        (Section 0D — write alongside the agents they protect)
│   ├── test_as_of_date.py
│   ├── test_record_count_invariant.py
│   ├── test_three_state_output.py
│   └── test_agent5_skip_condition.py
├── db/                          (SQLite, created at runtime)
├── chroma_db/                    (ChromaDB local persistence — Agent 9, created at runtime)
├── api/                         (FastAPI app)
├── frontend/                    (React app)
└── outputs/reports/             (generated metrics/exception reports)
```
