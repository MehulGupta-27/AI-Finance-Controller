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


class ActionRequest(BaseModel):
    record_id: str
    action:    str  # "approve" | "reject" | "manual_link"
    note:      str | None = None


@app.get("/api/summary")
def get_summary():
    """
    Return dashboard summary + all records.
    If pipeline hasn't run yet, run it now (blocking).
    """
    global _LAST_RUN, _LAST_FULL_RESULTS, _LAST_RAW_LOOKUP

    if _LAST_RUN is None:
        logger.info("No cached pipeline run — running now...")
        _LAST_RUN, _LAST_FULL_RESULTS, _LAST_RAW_LOOKUP = _run_and_cache_pipeline()

    run = _LAST_RUN
    results = _LAST_FULL_RESULTS
    raw_lookup = _LAST_RAW_LOOKUP

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
        "as_of_date":        str(run.input_record_ids[0][:10]) if run.input_record_ids else "unknown",
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

    return {"summary": summary, "records": records}


@app.post("/api/action")
def record_action(req: ActionRequest):
    """Log a human review decision to the audit trail."""
    from agents.audit_logger import log_human_action
    log_human_action(req.record_id, req.action, req.note or "")
    logger.info(f"Human action recorded: {req.record_id} -> {req.action}")
    return {"status": "ok"}


def _run_and_cache_pipeline():
    """
    Run the full pipeline and return (PipelineRunResult, list[RouteResult], raw_lookup).
    Caches the result globally.
    """
    logger.info("Running full pipeline...")
    run_result, all_results, raw_lookup = run_pipeline()
    logger.info("Pipeline complete.")
    return run_result, all_results, raw_lookup


if __name__ == "__main__":
    import uvicorn
    logger.info("Starting FastAPI server on http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
