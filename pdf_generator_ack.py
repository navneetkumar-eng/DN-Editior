"""
DN PDF Editor Pro - Acknowledgement Note PDF Generator
Redacts old values, inserts new ones with matching font/size/alignment.

Font: DejaVuSans / DejaVuSans-Bold, size 9.60
Values: LEFT-aligned at original x position (not right-aligned like Blinkit DNs)
Totals: Bold
"""

from __future__ import annotations

import fitz
from pathlib import Path
from typing import Tuple, Any

from config import OUTPUT_DIR
from extractor_ack import AckDocument
from utils import setup_logger

log = setup_logger(__name__)

FONT_SIZE    = 9.60
FONT_REGULAR = "helv"
FONT_BOLD    = "hebo"


def _redact(page: fitz.Page, bbox: Tuple, padding: float = 2.0) -> None:
    x0, y0, x1, y1 = bbox
    page.add_redact_annot(
        fitz.Rect(x0-padding, y0-padding, x1+padding, y1+padding),
        fill=(1, 1, 1)
    )


def _insert(page: fitz.Page, text: str, x: float, baseline_y: float,
            bold: bool = False, font_size: float = FONT_SIZE) -> None:
    """Insert text at exact x position — left-aligned."""
    page.insert_text(
        fitz.Point(x, baseline_y),
        text,
        fontname=FONT_BOLD if bold else FONT_REGULAR,
        fontsize=font_size,
        color=(0, 0, 0),
    )


def _fmt(v: float) -> str:
    """Format number: no trailing zeros for whole numbers, 2dp otherwise."""
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}" if (v * 100) % 1 == 0 else f"{v:.4g}"


def generate_ack_pdf(
    original_pdf_path: str | Path,
    dn:               AckDocument,
    edited_receiving: list[dict],
    edited_disc:      list[dict],
    output_filename:  str | None = None,
) -> Path:
    """
    Generate updated Acknowledgement Note PDF.
    Editable fields:
      - Receiving: received_qty, unit_price → recalculates value
      - Discrepancy: discrepancy_qty, unit_price → recalculates value
    Updates header summary fields and section totals too.
    """
    original_pdf_path = Path(original_pdf_path)
    doc  = fitz.open(str(original_pdf_path))
    page = doc[0]

    ops = []  # (bbox, new_text, x, bold)

    def queue(bbox, new_text, x, bold=False):
        if bbox:
            ops.append((bbox, new_text, x, bold))

    # ── Receiving Items ────────────────────────────────────────────────────────
    new_total_rcvd  = 0.0
    new_total_value = 0.0

    for item, edited in zip(dn.receiving_items, edited_receiving):
        new_rcvd  = float(edited.get("received_qty", item.received_qty))
        new_price = float(edited.get("unit_price",   item.unit_price))
        new_value = round(new_rcvd * new_price, 2)

        new_total_rcvd  += new_rcvd
        new_total_value += new_value

        c = item.coords
        if c.get("received_qty"):
            queue(c["received_qty"], _fmt(new_rcvd),  c["received_qty"][0])
        if c.get("unit_price"):
            queue(c["unit_price"],   _fmt(new_price), c["unit_price"][0])
        if c.get("value"):
            queue(c["value"],        _fmt(new_value), c["value"][0])

    new_total_value = round(new_total_value, 2)

    # ── Receiving Totals row ───────────────────────────────────────────────────
    # Find total row spans
    p0 = [s for s in dn.spans if s.page == 0]
    disc_header_y = next((s.bbox[1] for s in p0 if "Discrepancy Details" in s.text), 521)

    recv_total_spans = [s for s in p0 if s.text == "Total" and s.bbox[1] < disc_header_y]
    if recv_total_spans:
        ty = recv_total_spans[0].bbox[1]
        row = [s for s in p0 if abs(s.bbox[1] - ty) < 8 and s.bbox[0] > 370]
        row.sort(key=lambda s: s.bbox[0])
        # row[0]=ASN total (unchanged), row[1]=received total, row[2]=value total
        if len(row) >= 3:
            queue(row[1].bbox, _fmt(new_total_rcvd),  row[1].bbox[0], bold=True)
            queue(row[2].bbox, _fmt(new_total_value),  row[2].bbox[0], bold=True)

    # ── Discrepancy Items ──────────────────────────────────────────────────────
    new_disc_qty   = 0.0
    new_disc_value = 0.0

    for item, edited in zip(dn.discrepancy_items, edited_disc):
        new_qty   = float(edited.get("discrepancy_qty", item.discrepancy_qty))
        new_price = float(edited.get("unit_price",      item.unit_price))
        new_value = round(new_qty * new_price, 2)

        new_disc_qty   += new_qty
        new_disc_value += new_value

        c = item.coords
        if c.get("discrepancy_qty"):
            queue(c["discrepancy_qty"], _fmt(new_qty),   c["discrepancy_qty"][0])
        if c.get("unit_price"):
            queue(c["unit_price"],      _fmt(new_price), c["unit_price"][0])
        if c.get("value"):
            queue(c["value"],           _fmt(new_value), c["value"][0])

    new_disc_qty   = round(new_disc_qty, 0)
    new_disc_value = round(new_disc_value, 2)

    # ── Discrepancy Totals row ─────────────────────────────────────────────────
    disc_total_spans = [s for s in p0 if s.text == "Total" and s.bbox[1] > disc_header_y]
    if disc_total_spans:
        ty = disc_total_spans[0].bbox[1]
        row = [s for s in p0 if abs(s.bbox[1] - ty) < 8 and s.bbox[0] > 400]
        row.sort(key=lambda s: s.bbox[0])
        if len(row) >= 2:
            queue(row[0].bbox, _fmt(new_disc_qty),   row[0].bbox[0], bold=True)
            queue(row[1].bbox, _fmt(new_disc_value), row[1].bbox[0], bold=True)

    # ── Header summary fields ──────────────────────────────────────────────────
    # Update GRN Value, Discrepancy Value, Discrepancy Quantity in header
    header_updates = {
        "GRN Value":            (new_total_value,  False),
        "Discrepancy Value":    (new_disc_value,   False),
        "Discrepancy Quantity": (new_disc_qty,     False),
    }
    for label, (new_val, bold) in header_updates.items():
        s = next((sp for sp in p0 if label in sp.text), None)
        if s:
            # The value is embedded in the span text after ":"
            prefix = s.text.split(":")[0] + ": "
            new_span_text = prefix + _fmt(new_val)
            queue(s.bbox, new_span_text, s.bbox[0], bold=bold)

    # ── Apply: redact all → insert all ────────────────────────────────────────
    for (bbox, _, _, _) in ops:
        _redact(page, bbox)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

    for (bbox, new_text, x, bold) in ops:
        baseline_y = bbox[3] - 1.5
        _insert(page, new_text, x, baseline_y, bold=bold)

    # ── Save ──────────────────────────────────────────────────────────────────
    if not output_filename:
        output_filename = f"{original_pdf_path.stem}_edited.pdf"

    out_path = OUTPUT_DIR / output_filename
    doc.save(str(out_path), garbage=4, deflate=True)
    doc.close()
    log.info("ACK PDF saved → %s", out_path)
    return out_path
