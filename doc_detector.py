"""
DN PDF Editor Pro - Document Type Detector
Identifies which DN format was uploaded and routes to correct extractor.

Supported types:
    DN       - Blinkit Discrepancy Note
    ACK      - TBOF Acknowledgement Note
    SCOOTSY  - Scootsy/Delhivery Discrepancy Note
    UNKNOWN  - Unrecognised format
"""

import fitz
from pathlib import Path


def detect_doc_type(pdf_path: str | Path) -> str:
    doc  = fitz.open(str(pdf_path))
    text = doc[0].get_text("text").lower()
    doc.close()

    if "acknowledgement note" in text:
        return "ACK"
    if "scootsy" in text or "delhivery warehouse" in text:
        return "SCOOTSY"
    if "discrepancy note" in text or "debit note" in text:
        return "DN"
    return "UNKNOWN"
