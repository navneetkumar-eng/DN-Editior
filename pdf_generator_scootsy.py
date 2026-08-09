"""
DN PDF Editor Pro - Scootsy DN PDF Generator
Landscape A4. Font Helvetica 8pt. Right-aligned values.

VERIFIED column mapping from actual span positions in original PDF:
Value    x0      Column borders       Actual column
24       342.0   [327.8-351.8]       Exp Qty (PO)
1        381.0   [364.6-401.3]       DN Qty   ← NOTE: DN Qty is in this range
550      422.1   [401.3-438.1]       Lot MRP
366.67   454.5   [438.1-481.6]       Unit Price
366.67   493.5   [481.6-520.6]       Taxable Value
18.33    537.7   [520.6-560.3]       Tax Amount
0        570.3   [560.3-584.3]       CGST Rate
0        612.5   [584.3-619.6]       CGST Amount
0        630.3   [619.6-645.1]       SGST Rate
0        674.0   [645.1-681.1]       SGST Amount
5        693.0   [681.1-708.8]       IGST Rate
18.33    724.2   [708.8-746.3]       IGST Amount
0        756.7   [746.3-771.1]       CESS Rate
0        798.5   [771.1-805.6]       CESS Amount
385      835.3   [805.6-849.8]       Total
"""

from __future__ import annotations

import fitz
import math
from pathlib import Path
from typing import Tuple

from config import OUTPUT_DIR
from extractor_scootsy import ScootsyDocument
from utils import setup_logger

log = setup_logger(__name__)

FONT_SIZE    = 8.0
FONT_REGULAR = "helv"
FONT_BOLD    = "hebo"

# Corrected column boundaries (left, right)
COLS = {
    "exp_qty":    (327.8, 351.8),
    "dn_qty":     (364.6, 401.3),   # DN Qty spans x=381
    "lot_mrp":    (401.3, 438.1),   # Lot MRP spans x=422
    "unit_price": (438.1, 481.6),   # Unit Price spans x=454
    "taxable":    (481.6, 520.6),   # Taxable Value spans x=493
    "tax_amount": (520.6, 560.3),   # Tax Amount spans x=537
    "cgst_rate":  (560.3, 584.3),
    "cgst_amt":   (584.3, 619.6),
    "sgst_rate":  (619.6, 645.1),
    "sgst_amt":   (645.1, 681.1),
    "igst_rate":  (681.1, 708.8),   # IGST Rate spans x=693
    "igst_amt":   (708.8, 746.3),   # IGST Amount spans x=724
    "cess_rate":  (746.3, 771.1),
    "cess_amt":   (771.1, 805.6),
    "total":      (805.6, 849.8),   # Total spans x=835
}


def _redact_col(page, col, row_y0, row_y1, padding=1.0):
    """Redact entire column width for this row."""
    x0, x1 = COLS[col]
    page.add_redact_annot(
        fitz.Rect(x0 + padding, row_y0 - 1, x1 - padding, row_y1 + 1),
        fill=(1, 1, 1)
    )


def _insert_right(page, text, col, baseline_y):
    """Insert text right-aligned within column."""
    _, right_x = COLS[col]
    tw = fitz.Font(FONT_REGULAR).text_length(text, fontsize=FONT_SIZE)
    x  = right_x - tw - 2
    page.insert_text(
        fitz.Point(x, baseline_y), text,
        fontname=FONT_REGULAR, fontsize=FONT_SIZE, color=(0, 0, 0)
    )


def _fmt(v: float) -> str:
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}"


def _find_span(spans, page_num, value_str, x_min, x_max, y_min, y_max):
    for s in spans:
        if (s.page == page_num and value_str in s.text
                and x_min <= s.bbox[0] <= x_max
                and y_min <= s.bbox[1] <= y_max):
            return s
    return None


def generate_scootsy_pdf(
    original_pdf_path: str | Path,
    dn:               ScootsyDocument,
    edited_rows:      list[dict],
    output_filename:  str | None = None,
) -> Path:

    original_pdf_path = Path(original_pdf_path)
    doc  = fitz.open(str(original_pdf_path))
    page = doc[0]
    p0   = [s for s in dn.spans if s.page == 0]

    col_redacts = []   # (col, row_y0, row_y1)
    inserts     = []   # (text, col, baseline_y)

    def queue(col, new_text, row_y0, row_y1):
        col_redacts.append((col, row_y0, row_y1))
        inserts.append((new_text, col, row_y1 - 1.5))

    # ── Line items ─────────────────────────────────────────────────────────────
    new_total_dn_qty  = 0.0
    new_total_taxable = 0.0
    new_total_tax     = 0.0
    new_total_igst    = 0.0
    new_total_amount  = 0.0

    for item, edited in zip(dn.line_items, edited_rows):
        new_dn_qty    = float(edited.get("dn_qty",     item.dn_qty))
        new_unit_price= float(edited.get("unit_price", item.unit_price))
        new_taxable   = round(new_dn_qty * new_unit_price, 2)
        igst_rate     = item.igst_rate
        new_tax       = round(math.floor(new_taxable * igst_rate / 100 * 100) / 100, 2)
        new_igst      = new_tax
        new_total     = round(new_taxable + new_tax, 2)

        new_total_dn_qty  += new_dn_qty
        new_total_taxable += new_taxable
        new_total_tax     += new_tax
        new_total_igst    += new_igst
        new_total_amount  += new_total

        # Row y bounds from dn_qty span (x=381, y0=336.3, y1=347.3)
        c = item.coords
        dn_bbox = c.get("dn_qty")
        if not dn_bbox:
            continue
        ry0, ry1 = dn_bbox[1], dn_bbox[3]

        queue("dn_qty",     _fmt(new_dn_qty),    ry0, ry1)
        queue("unit_price", _fmt(new_unit_price), ry0, ry1)
        queue("taxable",    _fmt(new_taxable),   ry0, ry1)
        queue("tax_amount", _fmt(new_tax),       ry0, ry1)
        queue("igst_amt",   _fmt(new_igst),      ry0, ry1)
        queue("total",      _fmt(new_total),     ry0, ry1)

    new_total_taxable = round(new_total_taxable, 2)
    new_total_tax     = round(new_total_tax, 2)
    new_total_igst    = round(new_total_igst, 2)
    new_total_amount  = round(new_total_amount, 2)

    # ── Totals row y≈368-380 ──────────────────────────────────────────────────
    TY0, TY1 = 366.0, 381.0
    queue("dn_qty",     _fmt(new_total_dn_qty),  TY0, TY1)
    queue("taxable",    _fmt(new_total_taxable), TY0, TY1)
    queue("tax_amount", _fmt(new_total_tax),     TY0, TY1)
    queue("igst_amt",   _fmt(new_total_igst),    TY0, TY1)
    queue("total",      _fmt(new_total_amount),  TY0, TY1)

    # ── DN Amt in header ───────────────────────────────────────────────────────
    dn_amt_s = _find_span(p0, 0, _fmt(dn.dn_amt), 525, 580, 218, 240)
    if dn_amt_s:
        bx = dn_amt_s.bbox
        page.add_redact_annot(
            fitz.Rect(bx[0]-2, bx[1]-2, bx[2]+20, bx[3]+2),
            fill=(1, 1, 1)
        )
        inserts.append((_fmt(new_total_amount), None, bx[3]-1.5, bx[0]))

    # ── Amount in words — clear ────────────────────────────────────────────────
    for s in [sp for sp in p0 if "Only" in sp.text]:
        page.add_redact_annot(
            fitz.Rect(s.bbox[0]-2, s.bbox[1]-2, s.bbox[2]+2, s.bbox[3]+2),
            fill=(1, 1, 1)
        )

    # ── Apply all column redactions ────────────────────────────────────────────
    for (col, ry0, ry1) in col_redacts:
        _redact_col(page, col, ry0, ry1)

    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

    # ── Insert all new values ──────────────────────────────────────────────────
    for item in inserts:
        if len(item) == 3:
            text, col, baseline_y = item
            if col and text:
                _insert_right(page, text, col, baseline_y)
        elif len(item) == 4:
            # Header with explicit x
            text, col, baseline_y, x = item
            if text and x:
                page.insert_text(fitz.Point(x, baseline_y), text,
                                 fontname=FONT_REGULAR, fontsize=FONT_SIZE, color=(0,0,0))

    # ── Save ──────────────────────────────────────────────────────────────────
    if not output_filename:
        output_filename = f"{original_pdf_path.stem}_edited.pdf"
    out_path = OUTPUT_DIR / output_filename
    doc.save(str(out_path), garbage=4, deflate=True)
    doc.close()
    log.info("Scootsy PDF → %s", out_path)
    return out_path
