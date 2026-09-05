"""
agents/audit_logger.py  —  Agent 7
Append-only audit trail logger.

Writes one row per agent decision to an SQLite table. Runs alongside every
stage (not retrofitted at the end). Schema per Section 5, Agent 7:

  log_id | timestamp | record_id | agent_name | action | status | sub_reason
       | confidence | tokens_used | latency_ms | log_notes

Note: column is `log_notes` (not `notes`) to avoid collision with the ledger
source data's `notes` field and Agent 5's `verifier_notes`.
"""

import json
import logging
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logger = logging.getLogger(__name__)

_DB_PATH = _ROOT / "db" / "audit_log.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_conn_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                log_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    TEXT    NOT NULL,
                record_id    TEXT    NOT NULL,
                agent_name   TEXT    NOT NULL,
                action       TEXT    NOT NULL,
                status       TEXT,
                sub_reason   TEXT,
                confidence   REAL,
                tokens_used  INTEGER,
                latency_ms   INTEGER,
                log_notes    TEXT
            )
        """)
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_record_id ON audit_log(record_id)")
        _conn.commit()
    return _conn


def log_event(
    record_id:   str,
    agent_name:  str,
    action:      str,
    status:      Optional[str]   = None,
    sub_reason:  Optional[str]   = None,
    confidence:  Optional[float] = None,
    tokens_used: Optional[int]   = None,
    latency_ms:  Optional[int]   = None,
    log_notes:   Optional[str]   = None,
) -> None:
    """
    Append one row to the audit log. Thread-safe. Never raises — log failures
    are swallowed with a warning so a logging error never breaks the pipeline.
    """
    ts = datetime.now(timezone.utc).isoformat()
    with _conn_lock:
        try:
            _get_conn().execute(
                """INSERT INTO audit_log
                   (timestamp, record_id, agent_name, action, status, sub_reason,
                    confidence, tokens_used, latency_ms, log_notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (ts, record_id, agent_name, action, status, sub_reason,
                 confidence, tokens_used, latency_ms, log_notes),
            )
            _get_conn().commit()
        except Exception as exc:
            logger.warning("Audit log write failed for %s: %s", record_id, exc)


# ---------------------------------------------------------------------------
# Convenience wrappers — one per agent stage
# ---------------------------------------------------------------------------

def log_ingestion(record_id: str, source: str, status: str = "ok", notes: str = "") -> None:
    log_event(record_id, "agent_1_ingestion", f"ingested:{source}", status=status, log_notes=notes)


def log_exact_match(record_id: str, order_id: str, result_status: str,
                    sub_reason: Optional[str] = None) -> None:
    log_event(record_id, "agent_2_exact_match", f"matched:order_id={order_id}",
              status=result_status, sub_reason=sub_reason, confidence=1.0)


def log_fuzzy_match(record_id: str, composite_score: float,
                    result_status: str, latency_ms: int = 0) -> None:
    log_event(record_id, "agent_3_fuzzy_match", "scored",
              status=result_status, confidence=composite_score, latency_ms=latency_ms)


def log_llm_reasoning(record_id: str, decision: str, confidence: float,
                       semantic_sim: float, tokens: int, latency_ms: int,
                       risk_flags: list) -> None:
    notes = f"sem_sim={semantic_sim:.2f} flags={risk_flags}"
    log_event(record_id, "agent_4_llm_reasoning", f"decision:{decision}",
              confidence=confidence, tokens_used=tokens, latency_ms=latency_ms,
              log_notes=notes)


def log_verification(record_id: str, independent_decision: str,
                     independent_confidence: float, agrees: bool,
                     skipped: bool, tokens: int, latency_ms: int) -> None:
    action = "skipped" if skipped else f"verified:{independent_decision}"
    notes  = f"agrees={agrees}"
    log_event(record_id, "agent_5_verifier", action,
              confidence=independent_confidence, tokens_used=tokens,
              latency_ms=latency_ms, log_notes=notes)


def log_routing(record_id: str, status: str, sub_reason: Optional[str],
                confidence: float, source: str, headline: str) -> None:
    log_event(record_id, "agent_6_router", f"routed:{source}",
              status=status, sub_reason=sub_reason, confidence=confidence,
              log_notes=headline)


def log_validation_failure(record_id: str, source: str, detail: str) -> None:
    log_event(record_id, "agent_1_ingestion", "validation_failed",
              status="UNRESOLVED", sub_reason="ingestion_validation_failed",
              log_notes=detail)


# ---------------------------------------------------------------------------
# Query helpers for reporting
# ---------------------------------------------------------------------------

def get_log_for_record(record_id: str) -> list[dict]:
    """Return all audit entries for a record_id, ordered by timestamp."""
    with _conn_lock:
        try:
            cur = _get_conn().execute(
                "SELECT * FROM audit_log WHERE record_id=? ORDER BY log_id",
                (record_id,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception as exc:
            logger.warning("Audit log read failed: %s", exc)
            return []


def get_summary_stats() -> dict:
    """Row counts by agent and status — for the pipeline dashboard."""
    with _conn_lock:
        try:
            cur = _get_conn().execute(
                "SELECT agent_name, status, COUNT(*) as cnt FROM audit_log GROUP BY agent_name, status"
            )
            rows = cur.fetchall()
            total_cur = _get_conn().execute("SELECT COUNT(*) FROM audit_log")
            total = total_cur.fetchone()[0]
            return {"total_entries": total, "by_agent_status": rows}
        except Exception:
            return {"total_entries": 0, "by_agent_status": []}


# ---------------------------------------------------------------------------
# Smoke-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(f"\n=== audit_logger smoke test ===")
    print(f"  DB path: {_DB_PATH}")

    # Write a few test entries
    log_ingestion("rec_001", "ledger")
    log_ingestion("rec_001", "razorpay")
    log_exact_match("rec_001", "ORD_TEST", "MATCHED")
    log_fuzzy_match("rec_002", 0.872, "MATCHED", latency_ms=12)
    log_llm_reasoning("rec_003", "match", 0.90, 0.85, tokens=320, latency_ms=850, risk_flags=[])
    log_verification("rec_003", "match", 0.99, agrees=True, skipped=False, tokens=280, latency_ms=920)
    log_routing("rec_003", "MATCHED", None, 0.945, "llm", "MATCHED — 95% confidence")
    log_validation_failure("rec_bad", "bank", "settlement_amount is null")

    entries = get_log_for_record("rec_001")
    print(f"\n  Entries for rec_001: {len(entries)}")
    for e in entries:
        print(f"    [{e['agent_name']}] {e['action']} status={e['status']}")

    stats = get_summary_stats()
    print(f"\n  Total audit rows: {stats['total_entries']}")
    for row in stats['by_agent_status']:
        print(f"    {row[0]}: status={row[1]}  count={row[2]}")

    assert stats['total_entries'] >= 8
    print("\n  ✓ All entries written and readable")
    print("=== OK ===\n")
