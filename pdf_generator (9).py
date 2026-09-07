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
    # Find CESS row by y-position grouping
    _p = [s for s in spans if s.page == tp and s.bbox[1] > 590 and s.bbox[1] < 685]
    _cess_anchor = [s for s in _p if s.text.strip() == "CESS"]
    if _cess_anchor:
        _ry = (_cess_anchor[0].bbox[1]+_cess_anchor[0].bbox[3])/2
        _cess_row = [s for s in _p if abs((s.bbox[1]+s.bbox[3])/2-_ry)<8]
        class _FS:
            def __init__(self,b): self.bbox=b
        _cx0=min(s.bbox[0] for s in _cess_row)-3
        _cy0=min(s.bbox[1] for s in _cess_row)-2
        _cx1=max(s.bbox[2] for s in _cess_row)+10
        _cy1=max(s.bbox[3] for s in _cess_row)+2
        cess_spans = [_FS((_cx0,_cy0,_cx1,_cy1))]
    else:
        cess_spans = []
    # Find Total CESS row by y-position
    _tcess_anchor = [s for s in _p if s.text.strip() == "Total"]
    tcess_spans = []
    for _ts in _tcess_anchor:
        _ry = (_ts.bbox[1]+_ts.bbox[3])/2
        _row = [s for s in _p if abs((s.bbox[1]+s.bbox[3])/2-_ry)<8]
        if any(s.text.strip()=="CESS" for s in _row) and not any("ADDT" in s.text for s in _row):
            class _FST:
                def __init__(self,b): self.bbox=b
            tcess_spans = [_FST((min(s.bbox[0] for s in _row)-3,
                                 min(s.bbox[1] for s in _row)-2,
                                 max(s.bbox[2] for s in _row)+10,
                                 max(s.bbox[3] for s in _row)+2))]
            break
    addt_spans  = [s for s in spans if s.page == tp and "ADDT_CESS" in s.text]

    import math
    from collections import defaultdict
    gst_groups = defaultdict(float)
    for r in edited_rows:
        sub = round(float(r.get('qty', 0)) * float(r.get('rate', 0)), 2)
        gst = float(r.get('gst_pct', 0))
        gst_groups[gst] += sub

    if dn.gst_type == "IGST":
        igst_parts = []
        igst_total = 0.0
        for gst_pct, sub_sum in sorted(gst_groups.items()):
            sub_sum = round(sub_sum, 2)
            amt     = round(math.floor(sub_sum * gst_pct / 100 * 100) / 100, 2)
            igst_total += amt
            igst_parts.append(f"{sub_sum} * {gst_pct}% = {amt:.2f}")
        igst_total = round(igst_total, 2)
        new_igst_line  = "IGST = " + ", ".join(igst_parts) + ","
        new_tigst_line = f"Total IGST = {igst_total:.2f}"

        if igst_spans:
            s = igst_spans[0]
            queue(tp, s.bbox, _insert_centered, pg, new_igst_line, s.bbox[3]-1.5, True)
        if tigst_spans:
            s = tigst_spans[0]
            queue(tp, s.bbox, _insert_centered, pg, new_tigst_line, s.bbox[3]-1.5, True)

    else:
        # CGST + SGST breakdown
        # CGST/SGST spans may be split - find by label only (not requiring = in same span)
        # Find rows by y-position grouping (spans are split across multiple words)
        _bp = [s for s in spans if s.page == tp and s.bbox[1] > 590 and s.bbox[1] < 685]

        def _find_row(label):
            anchors = [s for s in _bp if s.text.strip() == label]
            if not anchors:
                return None
            ry = (anchors[0].bbox[1] + anchors[0].bbox[3]) / 2
            row = [s for s in _bp if abs((s.bbox[1]+s.bbox[3])/2 - ry) < 8]
            class FS:
                def __init__(self, b): self.bbox = b
            return FS((min(s.bbox[0] for s in row)-3, min(s.bbox[1] for s in row)-2,
                       max(s.bbox[2] for s in row)+10, max(s.bbox[3] for s in row)+2))

        def _find_total_row(label):
            for ts in [s for s in _bp if s.text.strip() == "Total"]:
                ry = (ts.bbox[1] + ts.bbox[3]) / 2
                row = [s for s in _bp if abs((s.bbox[1]+s.bbox[3])/2 - ry) < 8]
                if any(s.text.strip() == label for s in row):
                    class FS:
                        def __init__(self, b): self.bbox = b
                    return FS((min(s.bbox[0] for s in row)-3, min(s.bbox[1] for s in row)-2,
                               max(s.bbox[2] for s in row)+10, max(s.bbox[3] for s in row)+2))
            return None

        cgst_s  = _find_row("CGST")
        tcgst_s = _find_total_row("CGST")
        sgst_s  = _find_row("SGST")
        tsgst_s = _find_total_row("SGST")
        cgst_spans  = [cgst_s]  if cgst_s  else []
        tcgst_spans = [tcgst_s] if tcgst_s else []
        sgst_spans  = [sgst_s]  if sgst_s  else []
        tsgst_spans = [tsgst_s] if tsgst_s else []


        cgst_parts = []
        cgst_total = 0.0
        for gst_pct, sub_sum in sorted(gst_groups.items()):
            sub_sum  = round(sub_sum, 2)
            half_pct = gst_pct / 2
            amt      = round(math.floor(sub_sum * half_pct / 100 * 100) / 100, 2)
            cgst_total += amt
            cgst_parts.append(f"{sub_sum} * {half_pct}% = {amt:.2f}")
        cgst_total = round(cgst_total, 2)

        new_cgst_line  = "CGST = " + ", ".join(cgst_parts) + ","
        new_tcgst_line = f"Total CGST = {cgst_total:.2f}"
        new_sgst_line  = "SGST = " + ", ".join(cgst_parts) + ","
        new_tsgst_line = f"Total SGST = {cgst_total:.2f}"

        def redact_full_line(span_list, new_text):
            if not span_list:
                return
            s = span_list[0]
            row_y = (s.bbox[1] + s.bbox[3]) / 2
            row_spans = [sp for sp in spans if sp.page == tp
                        and abs((sp.bbox[1]+sp.bbox[3])/2 - row_y) < 8]
            if row_spans:
                x0 = min(sp.bbox[0] for sp in row_spans) - 3
                y0 = min(sp.bbox[1] for sp in row_spans) - 2
                x1 = max(sp.bbox[2] for sp in row_spans) + 10
                y1 = max(sp.bbox[3] for sp in row_spans) + 2
                full_bbox = (x0, y0, x1, y1)
            else:
                full_bbox = (s.bbox[0]-3, s.bbox[1]-2, s.bbox[2]+100, s.bbox[3]+2)
            queue(tp, full_bbox, _insert_centered, pg, new_text, s.bbox[3]-1.5, True)

        redact_full_line(cgst_spans,  new_cgst_line)
        redact_full_line(tcgst_spans, new_tcgst_line)
        redact_full_line(sgst_spans,  new_sgst_line)
        redact_full_line(tsgst_spans, new_tsgst_line)

    # CESS lines — recalculate based on new sub total
    new_sub = round(sum(
        float(r.get('qty',0)) * float(r.get('rate',0)) for r in edited_rows
    ), 2)
    new_cess_line  = f"CESS = {new_sub} * 0.0% = 0.00,"
    new_tcess_line = "Total CESS = 0.00"

    def _full_row_bbox(span_list):
        """Get bbox covering full row width of all spans at same y level."""
        if not span_list:
            return None
        s = span_list[0]
        row_y = (s.bbox[1] + s.bbox[3]) / 2
        row = [sp for sp in spans if sp.page == tp
               and abs((sp.bbox[1]+sp.bbox[3])/2 - row_y) < 6]
        x0 = min(sp.bbox[0] for sp in row) - 5 if row else s.bbox[0]-5
        y0 = min(sp.bbox[1] for sp in row) - 2 if row else s.bbox[1]-2
        x1 = max(sp.bbox[2] for sp in row) + 15 if row else s.bbox[2]+50
        y1 = max(sp.bbox[3] for sp in row) + 2 if row else s.bbox[3]+2
        return (x0, y0, x1, y1)

    def _row_full_bbox(label_spans):
        if not label_spans:
            return None
        s = label_spans[0]
        row_y = (s.bbox[1] + s.bbox[3]) / 2
        # Get ALL spans on same row (within 8pts vertically)
        row = [sp for sp in spans if sp.page == tp
               and abs((sp.bbox[1]+sp.bbox[3])/2 - row_y) < 8]
        if not row:
            return s.bbox
        return (
            min(sp.bbox[0] for sp in row) - 3,
            min(sp.bbox[1] for sp in row) - 2,
            max(sp.bbox[2] for sp in row) + 10,
            max(sp.bbox[3] for sp in row) + 2
        )

    if cess_spans:
        s = cess_spans[0]
        bbox = _row_full_bbox(cess_spans)
        queue(tp, bbox, _insert_centered, pg, new_cess_line, s.bbox[3]-1.5, True)

    if tcess_spans:
        s = tcess_spans[0]
        bbox = _row_full_bbox(tcess_spans)
        queue(tp, bbox, _insert_centered, pg, new_tcess_line, s.bbox[3]-1.5, True)

    # ADDT_CESS — always 0.00, no change needed but redact/rewrite to be safe
    if addt_spans:
        s = addt_spans[0]
        queue(tp, s.bbox, _insert_centered, pg, "Total ADDT_CESS = 0.00", s.bbox[3]-1.5, True)


# ── Format helpers ─────────────────────────────────────────────────────────────

def _fmt_num(v: float) -> str:
    return f"{v:.2f}"

def _fmt_qty(v: float) -> str:
    return str(int(v)) if v == int(v) else f"{v:.2f}"
