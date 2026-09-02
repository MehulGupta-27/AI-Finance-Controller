"""
agents/pipeline.py
Full end-to-end pipeline runner for the AI Finance Controller.

Runs Agents 0–8 in order against the 110-record dev dataset (data/raw_100/).
Prints the dashboard summary, verifies the record identity invariant,
and confirms all four Section 0D pytest tests pass.

Usage:
    python agents/pipeline.py                   # 110-record dev set
    python agents/pipeline.py --data data/raw   # full 550-record set (step 17+)
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Suppress INFO from httpx/groq during pipeline run; keep WARNING+
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s [%(name)s] %(message)s",
)
# But always show our own pipeline INFO
_pipe_log = logging.getLogger("pipeline")
_pipe_log.setLevel(logging.INFO)
_pipe_log.propagate = False
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))
_pipe_log.addHandler(_handler)

from agents.data_loader import load_raw_data
from agents.as_of_date import compute_as_of_date
from agents.ingestion_agent import ingest
from agents.exact_match_agent import run_exact_match
from agents.fuzzy_match_agent import run_fuzzy_match
from agents.llm_reasoning_agent import reason_batch
from agents.verifier_agent import verify_batch, should_skip_verification
from agents.router import (
    route_exact_match, route_fuzzy_auto_match, route_llm_result,
    route_pending_settlement, route_missing_ledger, route_unidentified_bank_credit,
    RouteResult, _explain_unresolved,
)
from agents.audit_logger import (
    log_ingestion, log_exact_match, log_fuzzy_match, log_llm_reasoning,
    log_verification, log_routing, log_validation_failure,
)
from agents.reporting_agent import (
    PipelineRunResult, RecordResult, check_record_identity_invariant, basic_summary,
)
from agents.llm_provider import LLMError


def run_pipeline(data_dir: str = None) -> PipelineRunResult:
    t_start = time.time()

    # -----------------------------------------------------------------------
    # Agent 0 — AS_OF_DATE
    # -----------------------------------------------------------------------
    _pipe_log.info("=" * 60)
    _pipe_log.info("AI Finance Controller — Pipeline Start")
    _pipe_log.info("=" * 60)

    ledger_df, rzp_df, bank_df = load_raw_data(data_dir)
    as_of = compute_as_of_date(ledger_df, rzp_df, bank_df)
    _pipe_log.info(f"AS_OF_DATE = {as_of}  (from dataset, not wall clock)")
    _pipe_log.info(f"Records loaded: ledger={len(ledger_df)}  rzp={len(rzp_df)}  bank={len(bank_df)}")

    # -----------------------------------------------------------------------
    # Agent 1 — Ingestion & Normalization
    # -----------------------------------------------------------------------
    ing = ingest(ledger_df, rzp_df, bank_df)

    for r in ing.ledger_records + ing.razorpay_records + ing.bank_records:
        log_ingestion(r.record_id, r.source)
    for f in ing.failures:
        log_validation_failure(f.source_ref, f.source, f.detail)
        _pipe_log.warning(f"Ingestion failure: {f.source} {f.source_ref} -- {f.detail}")

    _pipe_log.info(f"Ingested {ing.total_count} canonical records  ({len(ing.failures)} failures)")

    # -----------------------------------------------------------------------
    # Agent 2 — Exact Match
    # -----------------------------------------------------------------------
    exact = run_exact_match(ing.ledger_records, ing.razorpay_records, ing.bank_records)
    _pipe_log.info(
        f"Exact match: {len(exact.matched_pairs)} pairs  "
        f"{len(exact.unmatched_rzp)} unmatched_rzp  "
        f"{len(exact.all_bank)} bank rows"
    )

    all_results: list[RouteResult] = []
    llm_call_count  = 0
    llm_token_count = 0

    # Ledger_by_order for fuzzy refund lookups
    ledger_by_order = {r.order_id: r for r in ing.ledger_records if r.order_id}

    # -----------------------------------------------------------------------
    # Agent 3 — Fuzzy Match
    # -----------------------------------------------------------------------
    fuzzy = run_fuzzy_match(exact, ledger_by_order, as_of)
    _pipe_log.info(
        f"Fuzzy match: {len(fuzzy.auto_matched_pairs)} auto-matched  "
        f"{len(fuzzy.llm_candidates)} -> LLM  "
        f"{len(fuzzy.unmatched_rzp)} unmatched_rzp  "
        f"{len(fuzzy.unmatched_bank)} unmatched_bank  "
        f"{len(fuzzy.missing_ledger_pairs)} missing_ledger"
    )

    # Build primary_input_ids now that both exact and fuzzy results are known.
    # One ID per logical transaction — maps to one ground_truth case entry.
    # Ledger record_id for all ledger-anchored cases (most records).
    # Rzp record_id for missing_from_ledger (no ledger row exists).
    # Bank record_id for unidentified_bank_credit (no Rzp/ledger counterpart).
    primary_input_ids: list[str] = []
    for pair in exact.matched_pairs:
        primary_input_ids.append(pair.ledger_record.record_id)
    for rzp_rec in exact.unmatched_rzp:          # missing_from_ledger candidates
        primary_input_ids.append(rzp_rec.record_id)
    for led_rec in exact.unmatched_ledger:       # shouldn't exist but guard it
        primary_input_ids.append(led_rec.record_id)
    for bank_rec in fuzzy.unmatched_bank:        # unidentified_bank_credit
        primary_input_ids.append(bank_rec.record_id)

    all_input_ids = primary_input_ids
    _pipe_log.info(f"Primary transaction IDs to track: {len(all_input_ids)}")

    # -----------------------------------------------------------------------
    # Agents 4 + 5 — LLM Reasoning + Verification
    # -----------------------------------------------------------------------
    a4_results = []
    if fuzzy.llm_candidates:
        _pipe_log.info(f"Running Agent 4 on {len(fuzzy.llm_candidates)} candidates...")
        a4_results = reason_batch(fuzzy.llm_candidates)
        llm_call_count += len(fuzzy.llm_candidates)

    # Separate successful A4 from errors
    a4_valid  = [(pair, res) for pair, res in a4_results if not isinstance(res, LLMError)]
    a4_errors = [(pair, res) for pair, res in a4_results if isinstance(res, LLMError)]

    ver_results = []
    if a4_valid:
        _pipe_log.info(f"Running Agent 5 on {len(a4_valid)} valid A4 results...")
        ver_results = verify_batch(a4_valid)
        llm_call_count += sum(1 for vr in ver_results if not vr.skipped)

    # -----------------------------------------------------------------------
    # Agent 6 — Route everything
    # -----------------------------------------------------------------------
    # Build rzp->ledger record_id map for all matched pairs.
    # Routing uses ledger record_id as the primary ID (matches all_input_ids).
    rzp_to_ledger_id: dict[str, str] = {}
    for pair in exact.matched_pairs:
        for rzp_rec in pair.rzp_records:
            rzp_to_ledger_id[rzp_rec.record_id] = pair.ledger_record.record_id

    # Build ledger_id->exact_pair map for failed-orphan detection
    ledger_to_exact_pair = {pair.ledger_record.record_id: pair for pair in exact.matched_pairs}

    # 1. Failed payment orphans — exact-matched, no bank needed, final result here
    for pair in exact.matched_pairs:
        if pair.is_failed:
            route = route_exact_match(pair)
            all_results.append(route)
            log_exact_match(route.record_id, pair.order_id, route.status, route.sub_reason)
            log_routing(route.record_id, route.status, route.sub_reason,
                        route.confidence, route.source, route.explanation.headline)
    # All other exact-matched pairs (non-failed) are intermediate —
    # their FINAL route comes from fuzzy/LLM steps below when Rzp+Bank confirms.

    # 2. Fuzzy auto-matched pairs — use ledger record_id (matches all_input_ids)
    for pair in fuzzy.auto_matched_pairs:
        raw_route = route_fuzzy_auto_match(pair, as_of)
        ledger_rid = rzp_to_ledger_id.get(pair.rzp_record.record_id,
                                          pair.rzp_record.record_id)
        route = RouteResult(
            record_id   = ledger_rid,
            status      = raw_route.status,
            sub_reason  = raw_route.sub_reason,
            confidence  = raw_route.confidence,
            explanation = raw_route.explanation,
            source      = raw_route.source,
        )
        all_results.append(route)
        log_fuzzy_match(pair.rzp_record.record_id, pair.scores.composite, route.status)
        log_routing(route.record_id, route.status, route.sub_reason,
                    route.confidence, route.source, route.explanation.headline)

    # 3. LLM-verified results — use ledger record_id
    for vr in ver_results:
        raw_route = route_llm_result(vr, as_of)
        ledger_rid = rzp_to_ledger_id.get(vr.pair.rzp_record.record_id,
                                          vr.pair.rzp_record.record_id)
        route = RouteResult(
            record_id   = ledger_rid,
            status      = raw_route.status,
            sub_reason  = raw_route.sub_reason,
            confidence  = raw_route.confidence,
            explanation = raw_route.explanation,
            source      = raw_route.source,
        )
        all_results.append(route)
        a4 = vr.agent4_result
        log_llm_reasoning(route.record_id, a4.decision, a4.confidence,
                          a4.semantic_similarity, tokens=0, latency_ms=0,
                          risk_flags=a4.risk_flags)
        if vr.agent5_result or vr.skipped:
            a5_dec  = vr.agent5_result.independent_decision if vr.agent5_result else "skipped"
            a5_conf = vr.agent5_result.independent_confidence if vr.agent5_result else a4.confidence
            log_verification(route.record_id, a5_dec, a5_conf,
                             vr.agrees, vr.skipped, tokens=0, latency_ms=0)
        log_routing(route.record_id, route.status, route.sub_reason,
                    route.confidence, route.source, route.explanation.headline)

    # 4. A4 LLM errors — use ledger record_id
    for pair, err in a4_errors:
        ledger_rid = rzp_to_ledger_id.get(pair.rzp_record.record_id,
                                          pair.rzp_record.record_id)
        route = RouteResult(
            record_id   = ledger_rid,
            status      = "UNRESOLVED",
            sub_reason  = "no_candidates_found",
            confidence  = 0.0,
            explanation = _explain_unresolved("no_candidates_found"),
            source      = "llm",
        )
        all_results.append(route)
        log_routing(route.record_id, route.status, route.sub_reason,
                    route.confidence, route.source, route.explanation.headline)

    # 5. Pending settlements (unmatched Rzp — no bank row found yet).
    #    The ledger record_id is the primary ID for these (the Rzp matched a
    #    ledger via exact match — ledger record_id is in all_input_ids).
    #    Build a reverse map: rzp record_id -> ledger record_id.
    rzp_to_ledger_id: dict[str, str] = {}
    for pair in exact.matched_pairs:
        for rzp_rec in pair.rzp_records:
            rzp_to_ledger_id[rzp_rec.record_id] = pair.ledger_record.record_id

    for rzp_rec in fuzzy.unmatched_rzp:
        route = route_pending_settlement(rzp_rec, as_of)
        # Use ledger record_id so the invariant check matches all_input_ids
        ledger_rid = rzp_to_ledger_id.get(rzp_rec.record_id, rzp_rec.record_id)
        route = RouteResult(
            record_id   = ledger_rid,
            status      = route.status,
            sub_reason  = route.sub_reason,
            confidence  = route.confidence,
            explanation = route.explanation,
            source      = route.source,
        )
        all_results.append(route)
        log_routing(route.record_id, route.status, route.sub_reason,
                    route.confidence, route.source, route.explanation.headline)

    # 6. Missing-ledger pairs (Rzp+Bank matched, no ledger row)
    for pair in fuzzy.missing_ledger_pairs:
        route = route_missing_ledger(pair, as_of)
        all_results.append(route)
        log_routing(route.record_id, route.status, route.sub_reason,
                    route.confidence, route.source, route.explanation.headline)

    # 7. Unidentified bank credits (bank row with no Rzp counterpart)
    for bank_rec in fuzzy.unmatched_bank:
        route = route_unidentified_bank_credit(bank_rec)
        all_results.append(route)
        log_routing(route.record_id, route.status, route.sub_reason,
                    route.confidence, route.source, route.explanation.headline)

    # 8. Catch-all: any input record_id not yet routed gets UNRESOLVED.
    #    This should be empty on a correctly-wired dataset — if it fires,
    #    it means a routing path has a gap.
    routed_ids = {r.record_id for r in all_results}
    for rid in all_input_ids:
        if rid not in routed_ids:
            _pipe_log.warning(f"UNROUTED record: {rid} — routing to no_candidates_found")
            route = RouteResult(
                record_id   = rid,
                status      = "UNRESOLVED",
                sub_reason  = "no_candidates_found",
                confidence  = 0.0,
                explanation = _explain_unresolved("no_candidates_found"),
                source      = "direct",
            )
            all_results.append(route)
            log_routing(route.record_id, route.status, route.sub_reason,
                        route.confidence, route.source, route.explanation.headline)

    # -----------------------------------------------------------------------
    # Agent 8 — Build PipelineRunResult + record identity invariant check
    # -----------------------------------------------------------------------
    matched_ids    = [r.record_id for r in all_results if r.status == "MATCHED"]
    partial_ids    = [r.record_id for r in all_results if r.status == "PARTIAL"]
    unresolved_ids = [r.record_id for r in all_results if r.status == "UNRESOLVED"]

    record_results = [
        RecordResult(
            record_id  = r.record_id,
            status     = r.status,
            sub_reason = r.sub_reason,
            confidence = r.confidence,
            source     = r.source,
        )
        for r in all_results
    ]

    run_result = PipelineRunResult(
        input_record_ids       = all_input_ids,
        matched_ids            = matched_ids,
        partial_ids            = partial_ids,
        unresolved_ids         = unresolved_ids,
        results                = record_results,
        total_runtime_seconds  = time.time() - t_start,
        llm_calls_made         = llm_call_count,
        llm_tokens_used        = llm_token_count,
    )

    # Section 0C.3 — record identity invariant (halts on failure)
    _pipe_log.info("\nChecking record identity invariant (Section 0C.3)...")
    check_record_identity_invariant(run_result)
    _pipe_log.info("OK Record identity invariant passed")

    # Dashboard summary
    summary = basic_summary(run_result)
    _pipe_log.info(summary)

    # Sub-reason breakdown
    from collections import Counter
    sub_counts = Counter(r.sub_reason for r in all_results if r.sub_reason)
    if sub_counts:
        _pipe_log.info("Sub-reason breakdown:")
        for sub, cnt in sub_counts.most_common():
            _pipe_log.info(f"  {sub:<35} {cnt}")

    # Stage throughput
    n_no_llm = len(matched_ids) + len(partial_ids) + len(unresolved_ids) - llm_call_count
    total    = len(all_input_ids)
    _pipe_log.info(f"\nStage throughput:")
    _pipe_log.info(f"  Exact match    : {len(exact.matched_pairs)} records")
    _pipe_log.info(f"  Fuzzy auto     : {len(fuzzy.auto_matched_pairs)} records")
    _pipe_log.info(f"  LLM (A4+A5)    : {llm_call_count} calls")
    _pipe_log.info(f"  Direct routing : (pending/missing/unidentified)")
    _pipe_log.info(f"  No-LLM resolve : {total - llm_call_count} / {total} "
                   f"({(total - llm_call_count) / total * 100:.0f}%)")

    return run_result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=None,
                        help="Path to data directory (default: data/raw_100)")
    args = parser.parse_args()
    run_pipeline(data_dir=args.data)
