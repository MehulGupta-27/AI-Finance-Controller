"""
agents/core/cashflow_agent.py — Cash Flow Forecast Agent (Agent 9)

Predicts future cash inflows based on pending settlements and historical
settlement patterns.

Key Features:
1. Computes median settlement lag from MATCHED records
2. Identifies PARTIAL records awaiting bank settlement
3. Predicts when each payment will arrive (expected settlement date)
4. Provides 7-day and 30-day inflow forecasts

All date calculations use the fixed as_of_date parameter (never datetime.now()).
Fully deterministic: identical inputs always produce identical outputs.

Section 8B implementation.
"""

import logging
import sys
import statistics
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
from pydantic import BaseModel

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------

class PendingSettlement(BaseModel):
    """A single pending settlement with forecast."""
    record_id: str
    customer: str
    order_id: str
    amount: float
    captured_date: date
    expected_settlement_date: date
    days_until_settlement: int


class CashFlowForecast(BaseModel):
    """Complete cash flow forecast result."""
    median_settlement_lag_days: int
    pending_settlements: list[PendingSettlement]
    expected_inflow_next_7_days: float
    expected_inflow_next_30_days: float
    note: Optional[str] = None


# ---------------------------------------------------------------------------
# Main forecast function
# ---------------------------------------------------------------------------

def forecast_cash_inflow(
    results: list,  # list of RecordResult from reporting_agent
    raw_lookup: dict[str, dict],  # record_id → {customer, amount, date, ...}
    as_of_date: date,  # Fixed date from compute_as_of_date(), never datetime.now()
) -> CashFlowForecast:
    """
    Forecast expected cash inflows based on median settlement lag computed
    from this run's own MATCHED records (Section 8B).
    
    Key requirements (per spec):
    - Median settlement lag computed from MATCHED records, not hardcoded
    - Uses AS_OF_DATE for all date calculations, never datetime.now()
    - Deterministic: identical input → identical output
    - Only forecasts PARTIAL records with sub_reason="awaiting_settlement"
    
    Parameters
    ----------
    results    : list of RecordResult from pipeline
    raw_lookup : dict mapping record_id to raw fields (amount, date, customer)
    as_of_date : fixed date from compute_as_of_date(), never wall clock
    
    Returns
    -------
    CashFlowForecast with:
        median_settlement_lag_days: int - median days from capture to settlement
        pending_settlements: list[PendingSettlement] - detailed per-record forecast
        expected_inflow_next_7_days: float - total expected in next 7 days
        expected_inflow_next_30_days: float - total expected in next 30 days
        note: optional message if no data available
    
    Example
    -------
    >>> forecast = forecast_cash_inflow(results, raw_lookup, date(2026, 4, 1))
    >>> print(f"Median lag: {forecast.median_settlement_lag_days} days")
    >>> print(f"Expected 7-day inflow: Rs.{forecast.expected_inflow_next_7_days:,.2f}")
    """
    
    # Step 1: Compute median settlement lag from MATCHED records
    logger.info("Computing median settlement lag from MATCHED records...")
    settlement_lags = []
    
    for result in results:
        if result.status != "MATCHED":
            continue
        
        raw = raw_lookup.get(result.record_id, {})
        captured_date = raw.get("captured_date")  # from Razorpay
        settled_date  = raw.get("settled_date")   # from Bank
        
        if captured_date and settled_date:
            # Both are date objects from ingestion
            lag = (settled_date - captured_date).days
            if lag >= 0:  # Only positive lags (settlement after capture)
                settlement_lags.append(lag)
    
    if not settlement_lags:
        # No MATCHED records with valid settlement data
        logger.warning("No MATCHED records available to compute settlement lag")
        return CashFlowForecast(
            median_settlement_lag_days=0,
            pending_settlements=[],
            expected_inflow_next_7_days=0.0,
            expected_inflow_next_30_days=0.0,
            note="No MATCHED records available to compute settlement lag"
        )
    
    median_lag_days = int(statistics.median(settlement_lags))
    logger.info(f"Median settlement lag: {median_lag_days} days (from {len(settlement_lags)} MATCHED records)")
    
    # Step 2: Forecast pending settlements
    logger.info("Forecasting pending settlements...")
    pending = []
    inflow_7d = 0.0
    inflow_30d = 0.0
    
    for result in results:
        if result.status == "PARTIAL" and result.sub_reason == "awaiting_settlement":
            raw = raw_lookup.get(result.record_id, {})
            captured_date = raw.get("captured_date")
            amount = float(raw.get("amount", 0))
            customer = raw.get("customer", "")
            order_id = raw.get("order_id", "")
            
            if not captured_date:
                continue
            
            # Predict settlement date using median lag
            expected_settlement = captured_date + timedelta(days=median_lag_days)
            
            # Section 8B clamping: if expected_settlement < AS_OF_DATE, clamp to as_of_date + 1
            # Never exclude overdue records - they're expected "now" (tomorrow)
            if expected_settlement < as_of_date:
                expected_settlement = as_of_date + timedelta(days=1)
            
            # Days since capture (relative to AS_OF_DATE, not wall clock)
            days_since = (as_of_date - captured_date).days
            
            # Days until expected settlement (after clamping)
            days_until = (expected_settlement - as_of_date).days
            
            pending.append(PendingSettlement(
                record_id=result.record_id,
                customer=customer,
                order_id=order_id,
                amount=round(amount, 2),
                captured_date=captured_date,
                expected_settlement_date=expected_settlement,
                days_until_settlement=days_until,
            ))
            
            # Add to inflow forecasts (after clamping, days_until is always >= 1)
            if 0 <= days_until <= 7:
                inflow_7d += amount
            if 0 <= days_until <= 30:
                inflow_30d += amount
    
    logger.info(f"Found {len(pending)} pending settlements")
    logger.info(f"Expected inflow (7 days): Rs.{inflow_7d:,.2f}")
    logger.info(f"Expected inflow (30 days): Rs.{inflow_30d:,.2f}")
    
    return CashFlowForecast(
        median_settlement_lag_days=median_lag_days,
        pending_settlements=sorted(pending, key=lambda x: x.days_until_settlement),
        expected_inflow_next_7_days=round(inflow_7d, 2),
        expected_inflow_next_30_days=round(inflow_30d, 2),
    )


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def get_forecast_summary(forecast: CashFlowForecast) -> str:
    """
    Generate a human-readable summary of the cash flow forecast.
    
    Returns
    -------
    str: Multi-line formatted summary
    """
    lines = [
        "=== Cash Flow Forecast ===",
        f"Median settlement lag: {forecast.median_settlement_lag_days} days",
        f"Pending settlements: {len(forecast.pending_settlements)}",
        f"Expected inflow (next 7 days): Rs.{forecast.expected_inflow_next_7_days:,.2f}",
        f"Expected inflow (next 30 days): Rs.{forecast.expected_inflow_next_30_days:,.2f}",
    ]
    
    if forecast.note:
        lines.append(f"Note: {forecast.note}")
    
    if forecast.pending_settlements:
        lines.append("\nPending settlements breakdown:")
        for ps in forecast.pending_settlements[:10]:  # Show first 10
            lines.append(
                f"  • {ps.customer[:20]:20s} Rs.{ps.amount:>10,.2f} "
                f"(expected in {ps.days_until_settlement} days)"
            )
        if len(forecast.pending_settlements) > 10:
            lines.append(f"  ... and {len(forecast.pending_settlements) - 10} more")
    
    return "\n".join(lines)


def get_overdue_settlements(
    forecast: CashFlowForecast,
    overdue_threshold_days: int = 7
) -> list[PendingSettlement]:
    """
    Filter pending settlements that are overdue by more than threshold days.
    
    Parameters
    ----------
    forecast : CashFlowForecast
    overdue_threshold_days : int, default 7
        Number of days beyond expected settlement to consider overdue
    
    Returns
    -------
    list[PendingSettlement]: Overdue settlements only
    """
    overdue = []
    for ps in forecast.pending_settlements:
        # days_until_settlement is already clamped to 0 minimum
        # If it's 0, payment was expected today or earlier
        if ps.days_until_settlement == 0:
            overdue.append(ps)
    
    return overdue


# ---------------------------------------------------------------------------
# Main entry point (for testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Simple test with mock data
    from agents.core.reporting_agent import RecordResult
    from agents.core.classifier_agent import Explanation, ChecklistItem
    
    # Mock data
    results = [
        RecordResult(
            record_id="r1",
            status="MATCHED",
            sub_reason=None,
            confidence=0.95,
            source="exact",
            explanation=Explanation(
                headline="Matched",
                checklist=[ChecklistItem(passed=True, label="Test")],
                risk_flags=[],
                days_elapsed=None,
                recommendation=None,
                confidence=0.95,
            )
        ),
        RecordResult(
            record_id="r2",
            status="PARTIAL",
            sub_reason="awaiting_settlement",
            confidence=0.80,
            source="fuzzy",
            explanation=Explanation(
                headline="Awaiting settlement",
                checklist=[ChecklistItem(passed=True, label="Test")],
                risk_flags=[],
                days_elapsed=2,
                recommendation="Wait for settlement",
                confidence=0.80,
            )
        ),
    ]
    
    raw_lookup = {
        "r1": {
            "captured_date": date(2026, 3, 25),
            "settled_date": date(2026, 3, 28),
            "amount": 1000,
        },
        "r2": {
            "captured_date": date(2026, 3, 30),
            "amount": 2000,
            "customer": "Test Customer",
            "order_id": "ORD123",
        },
    }
    
    as_of = date(2026, 4, 1)
    
    forecast = forecast_cash_inflow(results, raw_lookup, as_of)
    print(get_forecast_summary(forecast))
