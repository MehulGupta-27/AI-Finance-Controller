"""
agents/qa_agent.py  —  Agent 9
Settlement Q&A Agent.

Chat interface over the finished, reconciled database. Answers must be
grounded in actual database rows — never inferred or fabricated.

Design (Section 5, Agent 9):
  Two distinct steps:
    1. INDEX: embed every reconciled record's combined text into ChromaDB
       once, after Agent 8's pipeline run completes.
    2. QUERY: embed the user's question → retrieve top-k semantically
       similar records (with optional metadata filters) → pass ONLY those
       records to a lightweight LLM call to phrase a grounded answer.

Why this is different from Agent 4's semantic_similarity:
  Agent 4 answers "do these two specific records refer to the same thing" —
  a pairwise classification, no vector DB needed.
  Agent 9 answers "find records relevant to this open-ended question" across
  hundreds of records — a genuine retrieval problem that needs a vector DB.

Stack:
  - ChromaDB: embedded in-process, no server, persists to disk at chroma_db/
  - Embeddings: sentence-transformers all-MiniLM-L6-v2, CPU, free, no API key
  - LLM: Groq GROQ_REASONING_MODEL (same fast/light tier as Agent 4) —
    narrow summarization task, not deep reasoning

Grounding rules:
  - LLM answers only from retrieved records, never invents details
  - If a record is PARTIAL or UNRESOLVED, say so plainly
  - If nothing relevant is retrieved, say so — don't guess
"""

import logging
import sys
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.config import GROQ_REASONING_MODEL, MERCHANT_PROFILE
from agents.llm_provider import call_llm, LLMError
from agents.reporting_agent import RecordResult

logger = logging.getLogger(__name__)

# ChromaDB collection name — one collection for all reconciled records
_COLLECTION_NAME = "reconciled_records"
_CHROMA_PATH     = str(_ROOT / "chroma_db")
_EMBED_MODEL     = "all-MiniLM-L6-v2"

# Lazy singletons — loaded once on first use
_embedder  = None
_chroma_client = None
_collection    = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embedder = SentenceTransformer(_EMBED_MODEL)
            logger.info("Loaded embedding model: %s", _EMBED_MODEL)
        except ImportError:
            raise RuntimeError("sentence-transformers not installed — run: pip install sentence-transformers")
    return _embedder


def _get_collection():
    global _chroma_client, _collection
    if _collection is None:
        import chromadb
        _chroma_client = chromadb.PersistentClient(path=_CHROMA_PATH)
        _collection    = _chroma_client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("ChromaDB collection: %s  (path=%s)", _COLLECTION_NAME, _CHROMA_PATH)
    return _collection


# ---------------------------------------------------------------------------
# Text builder — what gets embedded for each record
# ---------------------------------------------------------------------------

def _record_text(record: RecordResult, raw_fields: dict) -> str:
    """
    Build the combined text that gets embedded for one reconciled record.

    Fields used (per spec):
      customer_name + narration + order_ref + ledger notes (when present)

    Including `notes` is critical for semantic_brand_narration records:
    their bank narration says "FITZONE WELLNESS PVT LTD" but notes say
    "Monthly gym membership renewal" — without notes, a query about "gym
    membership payments" would miss them entirely.
    """
    parts = []
    if raw_fields.get("customer"):
        parts.append(raw_fields["customer"])
    if raw_fields.get("narration"):
        parts.append(raw_fields["narration"])
    if raw_fields.get("order_id"):
        parts.append(raw_fields["order_id"])
    if raw_fields.get("notes"):
        parts.append(raw_fields["notes"])
    # Always include sub_reason as a human-readable phrase for natural-language search
    if record.sub_reason:
        readable = {
            "awaiting_settlement":       "waiting for bank settlement",
            "no_ledger_record":          "money received without order",
            "overdue_settlement":        "overdue bank settlement",
            "agent_disagreement":        "AI agents disagreed",
            "low_confidence":            "low confidence match",
            "high_value_review_required":"high value transaction",
            "unidentified_bank_credit":  "unidentified bank credit",
            "no_action_needed":          "failed payment no action needed",
            "no_candidates_found":       "no match found",
        }
        parts.append(readable.get(record.sub_reason, record.sub_reason))
    return " | ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Indexing — run once after pipeline completes
# ---------------------------------------------------------------------------

def index_reconciled_records(
    results:    list[RecordResult],
    raw_fields: list[dict],         # parallel list of raw display fields per record
) -> int:
    """
    Embed all reconciled records and store in ChromaDB.

    Parameters
    ----------
    results    : list of RecordResult from the pipeline
    raw_fields : parallel list of dicts with keys:
                 customer, narration, order_id, notes, amount, date
                 (extracted from pipeline routing context)

    Returns
    -------
    Number of records indexed.
    """
    collection = _get_collection()
    embedder   = _get_embedder()

    # Clear existing entries — full re-index on each pipeline run
    existing = collection.count()
    if existing > 0:
        collection.delete(where={"status": {"$in": ["MATCHED", "PARTIAL", "UNRESOLVED"]}})
        logger.info("Cleared %d existing ChromaDB entries", existing)

    texts     = []
    ids       = []
    metadatas = []

    for rec, fields in zip(results, raw_fields):
        text = _record_text(rec, fields)
        if not text.strip():
            text = f"record {rec.record_id} status {rec.status}"

        texts.append(text)
        ids.append(rec.record_id)
        metadatas.append({
            "status":     rec.status,
            "sub_reason": rec.sub_reason or "",
            "amount":     float(fields.get("amount", 0)),
            "date":       str(fields.get("date", "")),
            "customer":   str(fields.get("customer", "")),
            "order_id":   str(fields.get("order_id", "")),
            "notes":      str(fields.get("notes", "")),
        })

    if not texts:
        logger.warning("No records to index")
        return 0

    embeddings = embedder.encode(texts, show_progress_bar=False).tolist()

    # ChromaDB upsert in batches of 100 (safe for any dataset size)
    batch_size = 100
    for start in range(0, len(texts), batch_size):
        end = start + batch_size
        collection.upsert(
            ids        = ids[start:end],
            embeddings = embeddings[start:end],
            documents  = texts[start:end],
            metadatas  = metadatas[start:end],
        )

    logger.info("Indexed %d records into ChromaDB", len(texts))
    return len(texts)


# ---------------------------------------------------------------------------
# Query — embed question, retrieve, LLM-phrase the answer
# ---------------------------------------------------------------------------

_QA_PROMPT_TEMPLATE = """
You are a financial reconciliation assistant. Answer the user's question based
ONLY on the reconciliation records retrieved below.

Rules:
- Only use the retrieved records. Do not invent any amounts, dates, names, or
  statuses that are not in the records.
- If a record is PARTIAL or UNRESOLVED, state that plainly — do not pretend it
  is fully reconciled.
- If the retrieved records do not contain enough information to answer the
  question, say so directly. Do not guess.
- Keep the answer concise and factual. Use plain language, not technical jargon.
- If there are no relevant records, say "No matching records found."

Merchant profile (for context):
  Brand name: {brand_name}
  Registered name: {registered_legal_name}

RETRIEVED RECORDS ({n_records} found):
{records_block}

USER QUESTION: {question}
""".strip()


from pydantic import BaseModel as _PydanticBase


class QAAnswer(_PydanticBase):
    """Wrapper so call_llm() can validate the response."""
    answer: str


def query(
    question:        str,
    n_results:       int = 5,
    status_filter:   Optional[str] = None,    # e.g. "UNRESOLVED"
    min_amount:      Optional[float] = None,
    max_amount:      Optional[float] = None,
    date_from:       Optional[str] = None,    # "YYYY-MM-DD"
    date_to:         Optional[str] = None,
) -> str:
    """
    Answer a natural-language question about the reconciled dataset.

    Parameters
    ----------
    question      : plain-language query, e.g. "any gym membership payments?"
    n_results     : number of records to retrieve (default 5)
    status_filter : restrict to MATCHED / PARTIAL / UNRESOLVED
    min_amount    : only records with amount >= this
    max_amount    : only records with amount <= this
    date_from/to  : date range filter (ISO strings)

    Returns
    -------
    Plain-text answer grounded in retrieved records.
    """
    collection = _get_collection()
    embedder   = _get_embedder()

    if collection.count() == 0:
        return "No reconciliation data has been indexed yet. Run the pipeline first."

    # Build optional metadata filter
    where_clauses = []
    if status_filter:
        where_clauses.append({"status": {"$eq": status_filter}})
    if min_amount is not None:
        where_clauses.append({"amount": {"$gte": min_amount}})
    if max_amount is not None:
        where_clauses.append({"amount": {"$lte": max_amount}})
    # Date filters as string prefix match (ISO format sorts lexicographically)
    if date_from:
        where_clauses.append({"date": {"$gte": date_from}})
    if date_to:
        where_clauses.append({"date": {"$lte": date_to}})

    where = None
    if len(where_clauses) == 1:
        where = where_clauses[0]
    elif len(where_clauses) > 1:
        where = {"$and": where_clauses}

    # Embed the question and retrieve
    q_embedding = embedder.encode([question], show_progress_bar=False)[0].tolist()

    query_kwargs = {
        "query_embeddings": [q_embedding],
        "n_results":        min(n_results, collection.count()),
        "include":          ["documents", "metadatas", "distances"],
    }
    if where:
        query_kwargs["where"] = where

    results = collection.query(**query_kwargs)

    docs      = results.get("documents",  [[]])[0]
    metas     = results.get("metadatas",  [[]])[0]
    distances = results.get("distances",  [[]])[0]

    if not docs:
        return "No matching records found for your query."

    # Format retrieved records for the LLM prompt
    record_lines = []
    for i, (doc, meta, dist) in enumerate(zip(docs, metas, distances)):
        similarity = round(1 - dist, 3)  # cosine: distance 0 = identical
        status     = meta.get("status", "?")
        sub        = meta.get("sub_reason", "")
        amount     = meta.get("amount", 0)
        date       = meta.get("date", "")
        customer   = meta.get("customer", "")
        notes      = meta.get("notes", "")

        line = (
            f"Record {i+1} (similarity {similarity:.0%}):\n"
            f"  Status   : {status}" + (f" — {sub}" if sub else "") + "\n"
            f"  Amount   : Rs.{amount:,.2f}  Date: {date}\n"
        )
        if customer:
            line += f"  Customer : {customer}\n"
        if notes:
            line += f"  Notes    : {notes}\n"
        record_lines.append(line)

    records_block = "\n".join(record_lines)

    prompt = _QA_PROMPT_TEMPLATE.format(
        brand_name            = MERCHANT_PROFILE["brand_name"],
        registered_legal_name = MERCHANT_PROFILE["registered_legal_name"],
        n_records             = len(docs),
        records_block         = records_block,
        question              = question,
    ) + '\n\nRespond with JSON: {"answer": "your answer here"}'

    try:
        result = call_llm(
            prompt    = prompt,
            schema    = QAAnswer,
            record_id = f"qa_{hash(question) % 100000:05d}",
            model     = GROQ_REASONING_MODEL,
        )
        return result.answer
    except LLMError as e:
        logger.warning("QA LLM call failed: %s", e)
        # Fallback: return a structured plain-text answer from the retrieved records
        lines = [f"Found {len(docs)} relevant record(s):"]
        for meta in metas:
            status = meta.get("status", "?")
            sub    = meta.get("sub_reason", "")
            amt    = meta.get("amount", 0)
            dt     = meta.get("date", "")
            cust   = meta.get("customer", "")
            lines.append(
                f"  • {status}{' (' + sub + ')' if sub else ''} — "
                f"Rs.{amt:,.2f} on {dt}{' — ' + cust if cust else ''}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Interactive CLI session (simple loop for demo)
# ---------------------------------------------------------------------------

def run_interactive_session():
    """
    Simple REPL for testing Agent 9 from the command line.
    Type 'exit' or Ctrl+C to quit.
    Supports optional filter flags:
      :status UNRESOLVED        — only UNRESOLVED records
      :min 5000                 — amount >= 5000
      :max 10000                — amount <= 10000
    """
    print("\n=== AI Finance Controller — Settlement Q&A ===")
    print(f"  Merchant: {MERCHANT_PROFILE['brand_name']}")
    n = _get_collection().count()
    print(f"  Records indexed: {n}")
    if n == 0:
        print("  WARNING: No records indexed. Run the pipeline first.")
    print("  Type your question. Use ':status MATCHED/PARTIAL/UNRESOLVED' to filter.")
    print("  Type 'exit' to quit.\n")

    while True:
        try:
            raw = input("Q> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not raw:
            continue
        if raw.lower() in ("exit", "quit", "q"):
            print("Goodbye.")
            break

        # Parse optional inline filters: :status UNRESOLVED :min 5000
        import re
        question = raw
        status_f = None
        min_amt  = None
        max_amt  = None

        for match in re.finditer(r':(\w+)\s+([\w.]+)', raw):
            key, val = match.group(1).lower(), match.group(2)
            if key == "status":
                status_f = val.upper()
                question = question.replace(match.group(0), "").strip()
            elif key == "min":
                try:    min_amt = float(val)
                except: pass
                question = question.replace(match.group(0), "").strip()
            elif key == "max":
                try:    max_amt = float(val)
                except: pass
                question = question.replace(match.group(0), "").strip()

        answer = query(
            question      = question,
            n_results     = 5,
            status_filter = status_f,
            min_amount    = min_amt,
            max_amount    = max_amt,
        )
        print(f"\nA> {answer}\n")


# ---------------------------------------------------------------------------
# Smoke-test (run directly)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import time
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Agent 9 — Settlement Q&A")
    parser.add_argument("--interactive", action="store_true",
                        help="Start interactive Q&A session")
    parser.add_argument("--index", action="store_true",
                        help="Re-index the reconciled records from the last pipeline run")
    parser.add_argument("--query", type=str, default=None,
                        help="Run a single query and exit")
    args = parser.parse_args()

    if args.interactive:
        run_interactive_session()

    elif args.query:
        answer = query(args.query)
        print(f"\nQ: {args.query}\nA: {answer}\n")

    else:
        # Default: smoke-test with synthetic indexed records
        print("\n=== Agent 9 smoke test ===\n")

        # Index some synthetic records to test retrieval
        from agents.reporting_agent import RecordResult

        synthetic_records = [
            RecordResult(record_id="t1", status="MATCHED",    sub_reason=None,                     confidence=1.0),
            RecordResult(record_id="t2", status="MATCHED",    sub_reason=None,                     confidence=0.95),
            RecordResult(record_id="t3", status="PARTIAL",    sub_reason="awaiting_settlement",     confidence=0.90),
            RecordResult(record_id="t4", status="PARTIAL",    sub_reason="awaiting_settlement",     confidence=0.90),
            RecordResult(record_id="t5", status="PARTIAL",    sub_reason="no_ledger_record",        confidence=0.85),
            RecordResult(record_id="t6", status="UNRESOLVED", sub_reason="unidentified_bank_credit",confidence=0.0),
            RecordResult(record_id="t7", status="MATCHED",    sub_reason=None,                     confidence=0.95),
            RecordResult(record_id="t8", status="MATCHED",    sub_reason=None,                     confidence=0.92),
        ]
        synthetic_fields = [
            {"customer": "Rahul Sharma",  "narration": "UPI transfer",             "order_id": "ORD001", "notes": "",                                     "amount": 5984.09, "date": "2026-02-01"},
            {"customer": "Priya Patel",   "narration": "TXN5530",                  "order_id": "ORD002", "notes": "",                                     "amount": 1105.80, "date": "2026-01-15"},
            {"customer": "Vikram Iyer",   "narration": "IMPS 712",                 "order_id": "ORD003", "notes": "",                                     "amount": 7324.76, "date": "2026-03-30"},
            {"customer": "Sneha Gupta",   "narration": "NEFT 441",                 "order_id": "ORD004", "notes": "",                                     "amount": 3200.00, "date": "2026-03-28"},
            {"customer": "",              "narration": "PG SETL 892",              "order_id": "ORD005", "notes": "",                                     "amount": 4200.00, "date": "2026-01-28"},
            {"customer": "",              "narration": "BANK REVERSAL FEES",       "order_id": "",       "notes": "",                                     "amount": 1452.71, "date": "2026-02-14"},
            {"customer": "Meera Reddy",   "narration": "FITZONE WELLNESS PVT LTD","order_id": "ORD006", "notes": "Monthly gym membership renewal",        "amount": 2103.73, "date": "2026-03-01"},
            {"customer": "Arjun Singh",   "narration": "FZW PRIVATE LIMITED RZRPY","order_id":"ORD007", "notes": "Personal training package - 10 sessions","amount": 2223.00, "date": "2026-03-15"},
        ]

        print(f"Indexing {len(synthetic_records)} synthetic records...")
        t0 = time.time()
        n  = index_reconciled_records(synthetic_records, synthetic_fields)
        print(f"Indexed {n} records in {time.time()-t0:.1f}s\n")

        test_questions = [
            ("How many payments are waiting for bank settlement?", None, None, None),
            ("Any unidentified credits in the bank statement?",   "UNRESOLVED", None, None),
            ("Are there any gym membership payments?",            None, None, None),
            ("Show me partial records above Rs.3000",             "PARTIAL", 3000.0, None),
        ]

        for q, status, min_a, max_a in test_questions:
            print(f"Q: {q}")
            ans = query(q, n_results=3, status_filter=status, min_amount=min_a, max_amount=max_a)
            print(f"A: {ans}\n")

        print("=== Smoke test complete ===")
        print("\nNext steps:")
        print("  python agents/qa_agent.py --interactive   # start Q&A session")
        print("  python agents/qa_agent.py --query 'your question here'")
