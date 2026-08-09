"""
DN PDF Editor Pro - PDF Generator
Multi-page aware. Matches original PDF font, size, bold, and alignment exactly.
"""

from __future__ import annotations

import fitz
from pathlib import Path
from typing import Tuple, Any

from config import OUTPUT_DIR
from extractor import DNDocument, SpanInfo
from utils import setup_logger

log = setup_logger(__name__)

FONT_SIZE    = 9.22
FONT_REGULAR = "helv"
FONT_BOLD    = "hebo"

# Column RIGHT edges (verified from PDF border positions)
COL_RIGHT = {
    "qty":        253.0,
    "rate":       304.0,
    "sub_total":  354.0,
    "gst_pct":    391.0,
    "net_amount": 441.0,
    "totals":     554.5,
}

# Page width for center-alignment calculations
PAGE_WIDTH = 595.0


# ── Low-level drawing helpers ──────────────────────────────────────────────────

def _redact(page: fitz.Page, bbox: Tuple, padding: float = 2.0) -> None:
    """Permanently erase text in bbox."""
    x0, y0, x1, y1 = bbox
    page.add_redact_annot(
        fitz.Rect(x0-padding, y0-padding, x1+padding, y1+padding),
        fill=(1, 1, 1)
    )


def _insert_right_aligned(
    page: fitz.Page,
    text: str,
    right_x: float,
    baseline_y: float,
    bold: bool = False,
    font_size: float = FONT_SIZE,
) -> None:
    """Insert text so its right edge aligns to right_x."""
    fontname = FONT_BOLD if bold else FONT_REGULAR
    tw = fitz.Font(fontname).text_length(text, fontsize=font_size)
    page.insert_text(
        fitz.Point(right_x - tw, baseline_y),
        text, fontname=fontname, fontsize=font_size, color=(0, 0, 0)
    )


def _insert_centered(
    page: fitz.Page,
    text: str,
    baseline_y: float,
    bold: bool = True,
    font_size: float = FONT_SIZE,
    page_width: float = PAGE_WIDTH,
) -> None:
    """Insert text centered on the page — matches IGST/CESS lines."""
    fontname = FONT_BOLD if bold else FONT_REGULAR
    tw = fitz.Font(fontname).text_length(text, fontsize=font_size)
    x  = (page_width - tw) / 2
    page.insert_text(
        fitz.Point(x, baseline_y),
        text, fontname=fontname, fontsize=font_size, color=(0, 0, 0)
    )


# ── Span finders ───────────────────────────────────────────────────────────────

def _find_span(spans: list[SpanInfo], page_num: int, text_hint: str,
               min_x: float = 0) -> SpanInfo | None:
    """Find first span on page containing text_hint."""
    for s in spans:
        if s.page == page_num and text_hint in s.text and s.bbox[0] >= min_x:
            return s
    return None


def _find_right_span(spans: list[SpanInfo], page_num: int,
                     hints: list[str]) -> SpanInfo | None:
    """Find a right-column amount span by matching original value hints."""
    p = [s for s in spans if s.page == page_num and s.bbox[0] > 490]
    for hint in hints:
        for s in p:
            if hint in s.text:
                return s
    return None


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_pdf(
    original_pdf_path: str | Path,
    dn:                DNDocument,
    edited_rows:       list[dict],
    totals:            Any,
    output_filename:   str | None = None,
) -> Path:
    """
    Generate updated PDF:
    - Right-align numeric values in table columns (regular font)
    - Right-align bold totals in right column
    - Center-align bold IGST/CESS breakdown lines
    - Handle items spanning multiple pages
    """
    original_pdf_path = Path(original_pdf_path)
    doc = fitz.open(str(original_pdf_path))

    # page_num → [(bbox, new_text, insert_fn_args)]
    # We store operations as (bbox_to_redact, callable) and execute per page
    page_ops: dict[int, list[tuple]] = {}

    def queue(page_num: int, bbox: Tuple, fn, *args):
        if page_num not in page_ops:
            page_ops[page_num] = []
        page_ops[page_num].append((bbox, fn, args))

    # ── 1. Line item cells ────────────────────────────────────────────────────
    for item, edited in zip(dn.line_items, edited_rows):
        pnum   = getattr(item, 'page', 0)
        coords = item.coords

        cells = {
            "qty":        (_fmt_qty(float(edited.get("qty",        item.qty))),        COL_RIGHT["qty"]),
            "rate":       (_fmt_num(float(edited.get("rate",       item.rate))),       COL_RIGHT["rate"]),
            "sub_total":  (_fmt_num(float(edited.get("sub_total",  item.sub_total))),  COL_RIGHT["sub_total"]),
            "gst_pct":    (_fmt_num(float(edited.get("gst_pct",    item.gst_pct))),    COL_RIGHT["gst_pct"]),
            "net_amount": (_fmt_num(float(edited.get("net_amount", item.net_amount))), COL_RIGHT["net_amount"]),
        }

        for field_key, (new_text, right_x) in cells.items():
            bbox = coords.get(field_key)
            if bbox:
                queue(pnum, bbox, _insert_right_aligned,
                      doc[pnum], new_text, right_x, bbox[3]-1.5, False)

    # ── 2. Totals (bold, right-aligned) ──────────────────────────────────────
    tp = getattr(dn, 'totals_page', 0)

    # Match original amount values to find their bboxes, then replace
    total_targets = [
        ([str(dn.num_items)],     str(totals.num_items),          True),
        ([str(dn.sub_total),
          _fmt_num(dn.sub_total)], _fmt_num(totals.sub_total),    True),
        ([str(dn.gst_total),
          _fmt_num(dn.gst_total)], _fmt_num(totals.gst_total),    True),
        ([str(dn.total_payable),
          _fmt_num(dn.total_payable)], _fmt_num(totals.total_payable), True),
    ]

    for (hints, new_text, bold) in total_targets:
        s = _find_right_span(dn.spans, tp, hints)
        if s:
            queue(tp, s.bbox, _insert_right_aligned,
                  doc[tp], new_text, COL_RIGHT["totals"], s.bbox[3]-1.5, bold)

    # ── 3. IGST/CESS breakdown (bold, center-aligned) ─────────────────────────
    _queue_igst_cess(doc, dn, edited_rows, totals, page_ops, tp, queue)

    # ── 4. Apply per page ─────────────────────────────────────────────────────
    for pnum in sorted(page_ops.keys()):
        pg  = doc[pnum]
        ops = page_ops[pnum]

        # Pass 1: redact all
        for (bbox, fn, args) in ops:
            if bbox:
                _redact(pg, bbox)
        pg.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

        # Pass 2: insert all
        for (bbox, fn, args) in ops:
            fn(*args)

    # ── 5. Save ───────────────────────────────────────────────────────────────
    if not output_filename:
        output_filename = f"{original_pdf_path.stem}_edited.pdf"

    out_path = OUTPUT_DIR / output_filename
    doc.save(str(out_path), garbage=4, deflate=True)
    doc.close()
    log.info("Saved → %s", out_path)
    return out_path


# ── IGST/CESS section handler ──────────────────────────────────────────────────

def _queue_igst_cess(
    doc:         fitz.Document,
    dn:          DNDocument,
    edited_rows: list[dict],
    totals:      Any,
    page_ops:    dict,
    tp:          int,
    queue,
) -> None:
    """
    Rebuild IGST/CESS breakdown lines matching original format exactly:
    - Bold, center-aligned
    - Format: IGST = <sub_total> * <gst>% = <amt>,
    - Then: Total IGST = <total>
    - Then CESS lines (unchanged)
    """
    spans = dn.spans
    pg    = doc[tp]

    # Find all bold centered lines on totals page (IGST/CESS section)
    igst_spans  = [s for s in spans if s.page == tp and "IGST" in s.text and "=" in s.text and "Total" not in s.text]
    tigst_spans = [s for s in spans if s.page == tp and "Total IGST" in s.text]
    cess_spans  = [s for s in spans if s.page == tp and "CESS" in s.text and "Total" not in s.text and "ADDT" not in s.text]
    tcess_spans = [s for s in spans if s.page == tp and "Total CESS" in s.text]
    addt_spans  = [s for s in spans if s.page == tp and "ADDT_CESS" in s.text]

    # Build new IGST breakdown — group by GST rate
    from collections import defaultdict
    gst_groups = defaultdict(float)
    for r in edited_rows:
        sub = round(float(r.get('qty', 0)) * float(r.get('rate', 0)), 2)
        gst = float(r.get('gst_pct', 0))
        gst_groups[gst] += sub

    # Format: IGST = <sub1> * <gst>% = <amt1>, <sub2> * <gst>% = <amt2>,
    igst_parts = []
    igst_total = 0.0
    for gst_pct, sub_sum in sorted(gst_groups.items()):
        sub_sum  = round(sub_sum, 2)
        amt      = round(sub_sum * gst_pct / 100, 2)
        igst_total += amt
        igst_parts.append(f"{sub_sum} * {gst_pct}% = {amt:.2f}")

    igst_total = round(igst_total, 2)
    new_igst_line  = "IGST = " + ", ".join(igst_parts) + ","
    new_tigst_line = f"Total IGST = {igst_total:.2f}"

    # Redact and rewrite IGST line
    if igst_spans:
        s = igst_spans[0]
        queue(tp, s.bbox, _insert_centered, pg, new_igst_line, s.bbox[3]-1.5, True)

    # Redact and rewrite Total IGST line
    if tigst_spans:
        s = tigst_spans[0]
        queue(tp, s.bbox, _insert_centered, pg, new_tigst_line, s.bbox[3]-1.5, True)

    # CESS lines — recalculate based on new sub total
    new_sub = round(sum(
        float(r.get('qty',0)) * float(r.get('rate',0)) for r in edited_rows
    ), 2)
    new_cess_line  = f"CESS = {new_sub} * 0.0% = 0.00,"
    new_tcess_line = "Total CESS = 0.00"

    if cess_spans:
        s = cess_spans[0]
        queue(tp, s.bbox, _insert_centered, pg, new_cess_line, s.bbox[3]-1.5, True)

    if tcess_spans:
        s = tcess_spans[0]
        queue(tp, s.bbox, _insert_centered, pg, new_tcess_line, s.bbox[3]-1.5, True)

    # ADDT_CESS — always 0.00, no change needed but redact/rewrite to be safe
    if addt_spans:
        s = addt_spans[0]
        queue(tp, s.bbox, _insert_centered, pg, "Total ADDT_CESS = 0.00", s.bbox[3]-1.5, True)


# ── Format helpers ─────────────────────────────────────────────────────────────

def _fmt_num(v: float) -> str:
    return f"{v:.2f}"

def _fmt_qty(v: float) -> str:
    return str(int(v)) if v == int(v) else f"{v:.2f}"
