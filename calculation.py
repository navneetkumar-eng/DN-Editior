"""
DN PDF Editor Pro - Calculation Engine
Real-time recalculation of DN financial values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from config import GST_IGST, GST_CGST_SGST
from utils import setup_logger

log = setup_logger(__name__)


@dataclass
class RowCalc:
    """Calculated values for a single line item."""
    sub_total:  float
    igst:       float
    cgst:       float
    sgst:       float
    net_amount: float


@dataclass
class TotalsCalc:
    """Calculated document-level totals."""
    sub_total:      float
    gst_total:      float
    total_payable:  float
    igst_total:     float
    cgst_total:     float
    sgst_total:     float
    num_items:      int


def _trunc2(v: float) -> float:
    """Truncate to 2 decimal places (floor rounding) — matches original PDF."""
    import math
    return math.floor(v * 100) / 100


def calc_row(
    qty:           float,
    rate:          float,
    gst_pct:       float,
    other_charges: float = 0.0,
    gst_type:      str   = GST_IGST,
) -> RowCalc:
    """
    Calculate derived values for a single line item.

    Rules:
        Sub Total  = Qty × Rate  (rounded to 2dp)
        IGST       = Sub Total × (gst_pct / 100)  [floor truncated]
        CGST       = Sub Total × (gst_pct / 2 / 100)  [floor truncated]
        SGST       = same as CGST
        Net Amount = Sub Total + GST + Other Charges
    """
    sub_total = round(qty * rate, 2)

    if gst_type == GST_IGST:
        igst = _trunc2(sub_total * gst_pct / 100)
        cgst = sgst = 0.0
    else:
        igst = 0.0
        cgst = _trunc2(sub_total * gst_pct / 2 / 100)
        sgst = cgst

    gst_amount = igst + cgst + sgst
    net_amount = round(sub_total + gst_amount + other_charges, 2)

    return RowCalc(
        sub_total=sub_total,
        igst=igst,
        cgst=cgst,
        sgst=sgst,
        net_amount=net_amount,
    )


def calc_totals(
    rows:          List[dict],
    gst_type:      str = GST_IGST,
    extra_charges: float = 0.0,
) -> TotalsCalc:
    """
    Aggregate totals across all line items.
    Matches original PDF: GST is applied once to the total sub-amount
    per GST rate group (floor-truncated), not summed from individual rows.
    """
    import math
    from collections import defaultdict

    sub_total_sum = 0.0
    num_items     = 0
    # Group sub-totals by GST rate
    gst_groups: dict[float, float] = defaultdict(float)

    for r in rows:
        qty  = float(r.get("qty", 0))
        rate = float(r.get("rate", 0))
        gst  = float(r.get("gst_pct", 0))
        sub  = round(qty * rate, 2)
        sub_total_sum += sub
        gst_groups[gst] += sub
        num_items += int(qty)

    sub_total_sum = round(sub_total_sum, 2)

    # Apply GST per rate group (floor truncated to match original PDF)
    igst_total = 0.0
    cgst_total = 0.0
    sgst_total = 0.0

    for gst_pct, sub_sum in gst_groups.items():
        sub_sum = round(sub_sum, 2)
        if gst_type == GST_IGST:
            igst_total += math.floor(sub_sum * gst_pct / 100 * 100) / 100
        else:
            half = math.floor(sub_sum * gst_pct / 2 / 100 * 100) / 100
            cgst_total += half
            sgst_total += half

    igst_total = round(igst_total, 2)
    cgst_total = round(cgst_total, 2)
    sgst_total = round(sgst_total, 2)
    gst_total  = round(igst_total + cgst_total + sgst_total, 2)
    total_payable = round(sub_total_sum + gst_total + extra_charges, 2)

    return TotalsCalc(
        sub_total=sub_total_sum,
        gst_total=gst_total,
        total_payable=total_payable,
        igst_total=igst_total,
        cgst_total=cgst_total,
        sgst_total=sgst_total,
        num_items=num_items,
    )


def build_igst_breakdown(rows: List[dict]) -> str:
    """Rebuild IGST breakdown string matching the original PDF format."""
    parts = []
    igst_total = 0.0
    for r in rows:
        sub = round(float(r.get("qty", 0)) * float(r.get("rate", 0)), 2)
        gst = float(r.get("gst_pct", 0))
        amt = round(sub * gst / 100, 2)
        igst_total += amt
        parts.append(f"{sub} * {gst}% = {amt:.2f}")
    return ", ".join(parts) + f",\nTotal IGST = {igst_total:.2f}"
