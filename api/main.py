"""
api/main.py — FastAPI backend for AI Finance Controller

Endpoints:
  GET  /api/summary — returns dashboard summary + all reconciled records
  POST /api/action  — logs a human review decision
"""

import logging
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.pipeline import run_pipeline
from agents.reporting_agent import PipelineRunResult

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI()

# CORS — allow frontend (default Vite dev server on 5173)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cache the last pipeline run — in production this would be database-backed
_LAST_RUN: PipelineRunResult | None = None
_LAST_FULL_RESULTS = None  # list of RouteResult with full Explanation
_LAST_RAW_LOOKUP = None     # dict[record_id, raw_fields]
_LAST_FORECAST = None       # dict with cash flow forecast


class ActionRequest(BaseModel):
    record_id: str
    action:    str  # "approve" | "reject" | "manual_link"
    note:      str | None = None


@app.get("/api/summary")
def get_summary():
    """
    Return dashboard summary + all records + cash flow forecast.
    If pipeline hasn't run yet, run it now (blocking).
    """
    global _LAST_RUN, _LAST_FULL_RESULTS, _LAST_RAW_LOOKUP, _LAST_FORECAST

    if _LAST_RUN is None:
        logger.info("No cached pipeline run — running now...")
        _LAST_RUN, _LAST_FULL_RESULTS, _LAST_RAW_LOOKUP, _LAST_FORECAST = _run_and_cache_pipeline()

    run = _LAST_RUN
    results = _LAST_FULL_RESULTS
    raw_lookup = _LAST_RAW_LOOKUP
    forecast = _LAST_FORECAST

    # Compute exact_match_count and fuzzy_auto_count
    exact_count = sum(1 for r in results if r.source == "exact")
    fuzzy_count = sum(1 for r in results if r.source == "fuzzy")

    summary = {
        "records_processed": len(run.input_record_ids),
        "matched":           len(run.matched_ids),
        "partial":           len(run.partial_ids),
        "unresolved":        len(run.unresolved_ids),
        "match_rate":        round(len(run.matched_ids) / len(run.input_record_ids) * 100, 1),
        "processing_time_s": round(run.total_runtime_seconds, 1),
        "llm_calls":         run.llm_calls_made,
        "no_llm_pct":        round((len(run.input_record_ids) - run.llm_calls_made) /
                                   len(run.input_record_ids) * 100, 1),
        "as_of_date":        run.as_of_date or "unknown",
        # NEW fields for Dashboard.jsx stage breakdown
        "exact_match_count": exact_count,
        "fuzzy_auto_count":  fuzzy_count,
    }

    # Build full records array with all fields frontend needs
    records = []
    for r in results:
        raw = raw_lookup.get(r.record_id, {})
        records.append({
            "record_id":   r.record_id,
            "status":      r.status,
            "sub_reason":  r.sub_reason,
            "confidence":  r.confidence,
            "source":      r.source,
            "explanation": {
                "headline":       r.explanation.headline,
                "checklist":      [{"passed": c.passed, "label": c.label} for c in r.explanation.checklist],
                "risk_flags":     r.explanation.risk_flags,
                "days_elapsed":   r.explanation.days_elapsed,
                "recommendation": r.explanation.recommendation,
                "confidence":     r.explanation.confidence,
            },
            # Real display fields from raw_lookup (not extracted from checklist)
            "customer":   raw.get("customer", ""),
            "amount":     float(raw.get("amount", 0)),
            "date":       str(raw.get("date", "")),
            "order_id":   raw.get("order_id", ""),
            "notes":      raw.get("notes", ""),
            "narration":  raw.get("narration", ""),
        })

    return {"summary": summary, "records": records, "forecast": forecast}


@app.get("/api/qa")
def qa_query(
    q: str,
    status: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    history: str | None = None,  # JSON string of conversation history
):
    """
    Answer a natural-language question about the reconciled dataset.
    
    Query params:
      q          : the question (required)
      status     : filter by status (MATCHED / PARTIAL / UNRESOLVED)
      min_amount : minimum amount filter
      max_amount : maximum amount filter
      history    : JSON string of conversation history [{"role": "user", "text": "..."}, ...]
    
    Returns:
      {
        "answer": "plain-text answer",
        "records": [array of ALL records the LLM was shown, not truncated]
      }
    """
    from agents.qa_agent import _get_collection, _get_embedder
    
    # Ensure records are indexed
    collection = _get_collection()
    if collection.count() == 0:
        return {
            "answer": "No reconciliation data has been indexed yet. Run the pipeline first.",
            "records": []
        }
    
    # Build filter (same logic as qa_agent.query)
    where_clauses = []
    if status:
        where_clauses.append({"status": {"$eq": status}})
    if min_amount is not None:
        where_clauses.append({"amount": {"$gte": min_amount}})
    if max_amount is not None:
        where_clauses.append({"amount": {"$lte": max_amount}})
    
    where = None
    if len(where_clauses) == 1:
        where = where_clauses[0]
    elif len(where_clauses) > 1:
        where = {"$and": where_clauses}
    
    # Retrieve records (single query, used for both LLM and response)
    embedder = _get_embedder()
    q_embedding = embedder.encode([q], show_progress_bar=False)[0].tolist()
    
    n_retrieve = 10  # How many records to retrieve and show to LLM
    query_kwargs = {
        "query_embeddings": [q_embedding],
        "n_results":        min(n_retrieve, collection.count()),
        "include":          ["documents", "metadatas", "distances"],
    }
    if where:
        query_kwargs["where"] = where
    
    results = collection.query(**query_kwargs)
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]
    
    if not docs:
        return {"answer": "No matching records found for your query.", "records": []}
    
    # Special case: if query is just a greeting or non-question, don't retrieve
    greeting_patterns = ['hi', 'hello', 'hey', 'thanks', 'thank you', 'ok', 'okay']
    if q.lower().strip() in greeting_patterns or len(q.strip()) < 3:
        return {
            "answer": "Hi! I can answer questions about your reconciled payments. Ask me anything - for example, which payments are still pending, whether there are any unresolved transactions, or to find specific transactions.",
            "records": []
        }
    
    # Call Agent 9's LLM with these EXACT records
    # (We'll use the internal prompt formatting but call the LLM ourselves)
    from agents.qa_agent import _QA_PROMPT_TEMPLATE, QAAnswer, MERCHANT_PROFILE, GROQ_QA_MODEL
    from agents.llm_provider import call_llm, LLMError
    import logging
    
    logger = logging.getLogger(__name__)
    
    # Format retrieved records for LLM prompt (same as qa_agent.query)
    record_lines = []
    for i, (doc, meta, dist) in enumerate(zip(docs, metas, distances)):
        similarity = round(1 - dist, 3)
        status_str = meta.get("status", "?")
        sub = meta.get("sub_reason", "")
        amount = meta.get("amount", 0)
        date = meta.get("date", "")
        customer = meta.get("customer", "")
        notes = meta.get("notes", "")
        
        line = (
            f"Record {i+1} (similarity {similarity:.0%}):\n"
            f"  Status   : {status_str}" + (f" — {sub}" if sub else "") + "\n"
            f"  Amount   : Rs.{amount:,.2f}  Date: {date}\n"
        )
        if customer:
            line += f"  Customer : {customer}\n"
        if notes:
            line += f"  Notes    : {notes}\n"
        record_lines.append(line)
    
    records_block = "\n".join(record_lines)
    
    # Parse conversation history if provided
    import json
    conversation_context = ""
    if history:
        try:
            hist = json.loads(history)
            if hist:
                conversation_context = "\n\nPrevious conversation:\n"
                for msg in hist[-6:]:  # Last 3 exchanges (6 messages)
                    role = "User" if msg['role'] == 'user' else "Assistant"
                    conversation_context += f"{role}: {msg['text']}\n"
                conversation_context += "\nCurrent question (use context from above if relevant):\n"
        except:
            pass  # Ignore invalid history
    
    # Build prompt with conversation history
    base_prompt = _QA_PROMPT_TEMPLATE.format(
        brand_name            = MERCHANT_PROFILE["brand_name"],
        registered_legal_name = MERCHANT_PROFILE["registered_legal_name"],
        n_records             = len(docs),
        records_block         = records_block,
        question              = q,
    )
    
    # Enforce consistent formatting
    format_instruction = (
        '\n\nIMPORTANT: Keep your answer concise (2-3 sentences). '
        'DO NOT list every record - the UI will display them separately. '
        'Just provide a brief summary count and any important patterns.'
        '\n\nRespond with JSON: {"answer": "your answer here"}'
    )
    
    prompt = conversation_context + base_prompt + format_instruction
    
    try:
        result = call_llm(
            prompt    = prompt,
            schema    = QAAnswer,
            record_id = f"qa_{hash(q) % 100000:05d}",
            model     = GROQ_QA_MODEL,
        )
        # Post-process: replace Unicode dashes with ASCII hyphens
        answer_text = result.answer
        answer_text = answer_text.replace('\u2013', '-')  # en-dash → hyphen
        answer_text = answer_text.replace('\u2014', '-')  # em-dash → hyphen
    except LLMError as e:
        logger.warning("QA LLM call failed: %s", e)
        # Fallback: structured plain-text answer
        lines = [f"Found {len(docs)} relevant record(s):"]
        for meta in metas:
            status_str = meta.get("status", "?")
            sub = meta.get("sub_reason", "")
            amt = meta.get("amount", 0)
            dt = meta.get("date", "")
            cust = meta.get("customer", "")
            lines.append(
                f"  • {status_str}{' (' + sub + ')' if sub else ''} — "
                f"Rs.{amt:,.2f} on {dt}{' — ' + cust if cust else ''}"
            )
        answer_text = "\n".join(lines)
    
    # Check if results are actually relevant
    # If the LLM says "no matching records" or similarity is very low, return empty records
    min_similarity_threshold = 0.20  # 20% similarity minimum
    max_similarity = max((1 - dist) for dist in distances) if distances else 0
    
    # If answer explicitly says "no matching" or similarity is too low, don't show records
    answer_lower = answer_text.lower()
    if "no matching records" in answer_lower or "no records found" in answer_lower or max_similarity < min_similarity_threshold:
        return {"answer": answer_text, "records": []}
    
    # Return ALL records the LLM was shown (not truncated subset)
    records = []
    for meta in metas:
        records.append({
            "status":     meta.get("status", ""),
            "sub_reason": meta.get("sub_reason", ""),
            "amount":     float(meta.get("amount", 0)),
            "date":       meta.get("date", ""),
            "customer":   meta.get("customer", ""),
            "notes":      meta.get("notes", ""),
        })
    
    return {"answer": answer_text, "records": records}


@app.post("/api/action")
def record_action(req: ActionRequest):
    """Log a human review decision to the audit trail."""
    from agents.audit_logger import log_human_action
    log_human_action(req.record_id, req.action, req.note or "")
    logger.info(f"Human action recorded: {req.record_id} -> {req.action}")
    return {"status": "ok"}


def _run_and_cache_pipeline():
    """
    Run the full pipeline and return (PipelineRunResult, list[RouteResult], raw_lookup, forecast).
    Caches the result globally.
    """
    logger.info("Running full pipeline...")
    run_result, all_results, raw_lookup, forecast = run_pipeline()
    logger.info("Pipeline complete.")
    return run_result, all_results, raw_lookup, forecast


if __name__ == "__main__":
    import uvicorn
    logger.info("Starting FastAPI server on http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
