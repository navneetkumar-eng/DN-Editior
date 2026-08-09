"""
DN PDF Editor Pro - Audit Export
Export edit history to Excel and JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict
import pandas as pd

from config import OUTPUT_DIR
from database import get_audit_log
from utils import setup_logger, safe_path

log = setup_logger(__name__)


def export_audit_excel(session_id: str, pdf_name: str) -> Path:
    """Export the audit log for a session to an Excel file."""
    records = get_audit_log(session_id)
    if not records:
        log.warning("No audit records for session %s", session_id)

    df = pd.DataFrame(records, columns=[
        "id", "session_id", "pdf_name", "field_name",
        "row_index", "old_value", "new_value", "changed_at",
    ])

    out_path = safe_path(OUTPUT_DIR, f"audit_{pdf_name}_{session_id[:8]}.xlsx")
    df.to_excel(str(out_path), index=False, engine="openpyxl")
    log.info("Audit exported → %s", out_path)
    return out_path


def export_audit_json(session_id: str, pdf_name: str) -> Path:
    """Export the audit log for a session to a JSON file."""
    records = get_audit_log(session_id)

    out_path = safe_path(OUTPUT_DIR, f"audit_{pdf_name}_{session_id[:8]}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, default=str)
    log.info("Audit JSON exported → %s", out_path)
    return out_path


def export_dn_excel(dn_data: dict, pdf_name: str) -> Path:
    """Export the full extracted DN data to Excel for review."""
    rows_data = dn_data.get("line_items", [])
    df = pd.DataFrame(rows_data)

    out_path = safe_path(OUTPUT_DIR, f"dn_data_{pdf_name}.xlsx")
    with pd.ExcelWriter(str(out_path), engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Line Items", index=False)

        # Header info
        meta = {k: v for k, v in dn_data.items() if k != "line_items"}
        meta_df = pd.DataFrame(list(meta.items()), columns=["Field", "Value"])
        meta_df.to_excel(writer, sheet_name="Header", index=False)

    log.info("DN Excel exported → %s", out_path)
    return out_path


def export_dn_json(dn_data: dict, pdf_name: str) -> Path:
    """Export the full extracted DN data to JSON."""
    out_path = safe_path(OUTPUT_DIR, f"dn_data_{pdf_name}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(dn_data, f, indent=2, default=str)
    log.info("DN JSON exported → %s", out_path)
    return out_path
