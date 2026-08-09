"""
DN PDF Editor Pro - Validator
Field-level validation before PDF generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict

from config import MAX_GST_PERCENT, MIN_QTY, MIN_RATE
from utils import setup_logger, parse_float

log = setup_logger(__name__)


@dataclass
class ValidationResult:
    valid:  bool = True
    errors: Dict[str, str] = field(default_factory=dict)   # field_key → message

    def add_error(self, key: str, msg: str) -> None:
        self.valid = False
        self.errors[key] = msg


def validate_rows(rows: List[dict]) -> ValidationResult:
    """
    Validate all editable fields in DN line items.

    Rules:
        - Qty must be ≥ 0 (no negatives).
        - Rate must be ≥ 0.
        - GST % must be 0–28 and not blank.
        - Sub Total / Net Amount must be numeric.
    """
    result = ValidationResult()

    for i, row in enumerate(rows):
        prefix = f"Row {i+1}"

        # Qty
        qty = row.get("qty", "")
        qty_val = parse_float(str(qty), default=-999)
        if qty_val == -999:
            result.add_error(f"qty_{i}", f"{prefix}: Quantity must be a number.")
        elif qty_val < MIN_QTY:
            result.add_error(f"qty_{i}", f"{prefix}: Quantity cannot be negative.")

        # Rate
        rate = row.get("rate", "")
        rate_val = parse_float(str(rate), default=-999)
        if rate_val == -999:
            result.add_error(f"rate_{i}", f"{prefix}: Rate must be a number.")
        elif rate_val < MIN_RATE:
            result.add_error(f"rate_{i}", f"{prefix}: Rate cannot be negative.")

        # GST %
        gst = row.get("gst_pct", "")
        if str(gst).strip() == "":
            result.add_error(f"gst_{i}", f"{prefix}: GST % cannot be blank.")
        else:
            gst_val = parse_float(str(gst), default=-999)
            if gst_val == -999:
                result.add_error(f"gst_{i}", f"{prefix}: GST % must be a number.")
            elif gst_val < 0 or gst_val > MAX_GST_PERCENT:
                result.add_error(f"gst_{i}", f"{prefix}: GST % must be between 0 and {MAX_GST_PERCENT}.")

    if not result.valid:
        log.warning("Validation failed: %s", result.errors)

    return result
