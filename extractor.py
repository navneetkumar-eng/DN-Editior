"""
DN PDF Editor Pro - Extractor
Detects PDF type, extracts text/tables, maps editable field coordinates.
Supports multi-page PDFs with line items spanning across pages.
"""

from __future__ import annotations

import re
import fitz
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Tuple

from config import OCR_DPI
from utils import setup_logger, parse_float, clean_text

log = setup_logger(__name__)


# ── Data models ────────────────────────────────────────────────────────────────

@dataclass
class SpanInfo:
    text:  str
    bbox:  Tuple[float, float, float, float]
    font:  str
    size:  float
    page:  int


@dataclass
class LineItem:
    sr_no:      int
    upc:        str
    name:       str
    hsn:        str
    qty:        float
    rate:       float
    sub_total:  float
    gst_pct:    float
    net_amount: float
    reason:     str
    remark:     str
    coords:     Dict[str, Tuple] = field(default_factory=dict)
    page:       int = 0   # which page this row lives on


@dataclass
class DNDocument:
    title:           str = ""
    buyer_id:        str = ""
    purchase_no:     str = ""
    date:            str = ""
    buyer_gst:       str = ""
    cin_no:          str = ""
    pan_no:          str = ""
    supplier_gst:    str = ""
    original_inv_id: str = ""
    inv_to_name:     str = ""
    inv_to_address:  str = ""
    lr_gr_no:        str = ""
    carrier_name:    str = ""
    vehicle_no:      str = ""
    total_weight:    str = ""
    line_items:      List[LineItem] = field(default_factory=list)
    num_items:       int   = 0
    sub_total:       float = 0.0
    gst_total:       float = 0.0
    total_payable:   float = 0.0
    gst_type:        str   = "IGST"
    igst_breakdown:  str   = ""
    cess_text:       str   = ""
    footer_text:     str   = ""
    spans:           List[SpanInfo] = field(default_factory=list)
    is_scanned:      bool  = False
    # Page where totals section lives
    totals_page:     int   = 0


# ── Main extractor ─────────────────────────────────────────────────────────────

class DNExtractor:

    def __init__(self, pdf_path: str | Path):
        self.pdf_path = Path(pdf_path)
        self._doc: fitz.Document | None = None

    def extract(self) -> DNDocument:
        try:
            self._doc = fitz.open(str(self.pdf_path))
        except Exception as e:
            log.error("Cannot open PDF: %s", e)
            raise

        dn = DNDocument()
        dn.is_scanned = self._is_scanned()
        log.info("PDF: %d pages, type=%s", len(self._doc), "SCANNED" if dn.is_scanned else "SEARCHABLE")

        dn.spans = self._collect_spans()
        self._parse_header(dn)
        self._parse_line_items(dn)
        self._parse_totals(dn)
        self._parse_footer(dn)
        return dn

    def _is_scanned(self) -> bool:
        for page in self._doc:
            if page.get_text("text").strip():
                return False
        return True

    def _collect_spans(self) -> List[SpanInfo]:
        spans = []
        for page_num, page in enumerate(self._doc):
            for b in page.get_text("dict")["blocks"]:
                if b.get("type") != 0:
                    continue
                for line in b["lines"]:
                    for sp in line["spans"]:
                        t = clean_text(sp["text"])
                        if t:
                            spans.append(SpanInfo(
                                text=t,
                                bbox=tuple(sp["bbox"]),
                                font=sp["font"],
                                size=sp["size"],
                                page=page_num,
                            ))
        return spans

    # ── Header ────────────────────────────────────────────────────────────────

    def _parse_header(self, dn: DNDocument) -> None:
        p0 = [s for s in dn.spans if s.page == 0]

        title_parts = [s.text for s in p0 if "discrepancy" in s.text.lower() or "debit" in s.text.lower()]
        dn.title = " ".join(title_parts) or "Discrepancy Note"

        right = [s for s in p0 if s.bbox[0] > 300]
        dn.buyer_id     = self._right_val(right, "Id")
        dn.purchase_no  = self._right_val(right, "Purchase")
        dn.date         = self._right_val(right, "Date")
        dn.buyer_gst    = self._right_val(right, "Gst Tin")
        dn.cin_no       = self._right_val(right, "Cin")
        dn.pan_no       = self._right_val(right, "Pan")
        dn.original_inv_id = self._right_val(right, "Original")

        supp = [s for s in right if "supplier" in s.text.lower()]
        if supp:
            sy = (supp[0].bbox[1] + supp[0].bbox[3]) / 2
            gst_lines = [s for s in right if s.bbox[1] > sy + 5 and re.match(r'^\d{2}[A-Z]', s.text)]
            if gst_lines:
                dn.supplier_gst = sorted(gst_lines, key=lambda s: s.bbox[1])[0].text

        left = [s for s in p0 if s.bbox[0] < 300]
        dn.inv_to_name    = self._left_label_val(left, "Name")
        dn.inv_to_address = self._left_label_val(left, "Address")

    def _right_val(self, spans, key):
        matches = [s for s in spans if key.lower() in s.text.lower()]
        if not matches:
            return ""
        ls = matches[0]
        row_y = (ls.bbox[1] + ls.bbox[3]) / 2
        right_of = [s for s in spans if s.bbox[0] > ls.bbox[2] - 5 and abs((s.bbox[1]+s.bbox[3])/2 - row_y) < 6]
        right_of.sort(key=lambda s: s.bbox[0])
        return clean_text(" ".join(s.text for s in right_of).lstrip(": "))

    def _left_label_val(self, spans, key):
        matches = [s for s in spans if key.lower() in s.text.lower() and s.bbox[0] < 100]
        if not matches:
            return ""
        ls = matches[0]
        row_y = (ls.bbox[1] + ls.bbox[3]) / 2
        right_of = [s for s in spans if s.bbox[0] > 90 and abs((s.bbox[1]+s.bbox[3])/2 - row_y) < 6]
        right_of.sort(key=lambda s: s.bbox[0])
        return clean_text(" ".join(s.text for s in right_of).lstrip(": "))

    # ── Line items — multi-page aware ─────────────────────────────────────────

    def _parse_line_items(self, dn: DNDocument) -> None:
        """
        Find Sr.no anchors across ALL pages.
        For each row, only collect spans between this sr_no y-top
        and the next sr_no y-top (or totals section), capped at y<620
        to avoid pulling in totals rows.
        """
        all_spans = dn.spans

        # Find all Sr.no anchor spans across all pages
        sr_spans = [
            s for s in all_spans
            if re.match(r"^\d+$", s.text.strip())
            and s.bbox[0] < 55
            # On page 0, skip header area (y<380); on other pages allow any y
            and (s.bbox[1] > 380 if s.page == 0 else s.bbox[1] > 0)
        ]
        sr_spans.sort(key=lambda s: (s.page, s.bbox[1]))

        log.info("Found %d Sr.no anchors across %d pages", len(sr_spans), len(self._doc))

        for idx, sr_span in enumerate(sr_spans):
            page_num   = sr_span.page
            band_top   = sr_span.bbox[1] - 3

            # Bottom = next sr_span top (same or next page), or page bottom
            if idx + 1 < len(sr_spans):
                next_sr = sr_spans[idx + 1]
                if next_sr.page == page_num:
                    band_bottom = next_sr.bbox[1] - 3
                else:
                    # Next item is on next page — go to end of current page
                    band_bottom = self._doc[page_num].rect.height
            else:
                # Last item — stop before totals (Number of Items label)
                band_bottom = self._find_totals_top(all_spans, page_num)

            # Collect spans for this row on this page only
            row_spans = [
                s for s in all_spans
                if s.page == page_num
                and s.bbox[1] >= band_top
                and s.bbox[3] <= band_bottom + 3
            ]

            item = self._build_line_item(
                int(sr_span.text.strip()), row_spans, page_num
            )
            if item:
                dn.line_items.append(item)
                log.info("Extracted Sr%d: qty=%s rate=%s sub=%s",
                         item.sr_no, item.qty, item.rate, item.sub_total)

    def _find_totals_top(self, spans: List[SpanInfo], page_num: int) -> float:
        """Find y-position of 'Number of Items' label on given page."""
        candidates = [
            s for s in spans
            if s.page == page_num
            and ("Number" in s.text or "number" in s.text or "Sub Total" in s.text)
        ]
        if candidates:
            return min(s.bbox[1] for s in candidates) - 3
        return 800  # fallback: full page height

    def _build_line_item(
        self,
        sr: int,
        row_spans: List[SpanInfo],
        page_num: int,
    ) -> LineItem | None:

        def spans_in_x(x0, x1):
            return sorted(
                [s for s in row_spans if s.bbox[0] >= x0 - 5 and s.bbox[2] <= x1 + 10],
                key=lambda s: (s.bbox[1], s.bbox[0])
            )

        def text_in_x(x0, x1):
            return clean_text(" ".join(s.text for s in spans_in_x(x0, x1)))

        def numeric_bbox_in_x(x0: float, x1: float) -> tuple | None:
            """Return bbox of first numeric span in column."""
            ss = spans_in_x(x0, x1)
            numeric = [s for s in ss if any(c.isdigit() for c in s.text)]
            if not numeric:
                return None
            s = numeric[0]
            return (s.bbox[0], s.bbox[1], s.bbox[2], s.bbox[3])

        def numeric_text_in_x(x0: float, x1: float) -> str:
            ss = spans_in_x(x0, x1)
            numeric = [s for s in ss if any(c.isdigit() for c in s.text)]
            return clean_text(numeric[0].text) if numeric else ""

        # Column x-ranges — consistent across all PDFs of this template
        upc       = text_in_x(60, 140)
        name      = text_in_x(140, 225)
        qty_text  = numeric_text_in_x(220, 258)
        rate_text = numeric_text_in_x(258, 308)
        sub_text  = numeric_text_in_x(307, 357)
        gst_text  = numeric_text_in_x(357, 395)
        net_text  = numeric_text_in_x(393, 445)
        reason    = text_in_x(445, 505)
        remark    = text_in_x(505, 560)

        hsn_match = re.search(r'HSN[:\s]+(\d+)', name, re.I)
        hsn = hsn_match.group(1) if hsn_match else ""

        coords = {
            "qty":        numeric_bbox_in_x(220, 258),
            "rate":       numeric_bbox_in_x(258, 308),
            "sub_total":  numeric_bbox_in_x(307, 357),
            "gst_pct":    numeric_bbox_in_x(357, 395),
            "net_amount": numeric_bbox_in_x(393, 445),
        }

        return LineItem(
            sr_no=sr,
            upc=upc,
            name=name,
            hsn=hsn,
            qty=parse_float(qty_text),
            rate=parse_float(rate_text),
            sub_total=parse_float(sub_text),
            gst_pct=parse_float(gst_text),
            net_amount=parse_float(net_text),
            reason=reason,
            remark=remark,
            coords=coords,
            page=page_num,
        )

    # ── Totals — find which page they live on ─────────────────────────────────

    def _parse_totals(self, dn: DNDocument) -> None:
        """
        Find totals section by scanning ALL pages for 'Number of Items' label.
        Works regardless of which page the totals fall on.
        """
        all_spans = dn.spans

        # Find the page containing totals
        totals_page = 0
        ni_spans = [
            s for s in all_spans
            if "Number" in s.text and s.bbox[0] > 150
        ]
        if ni_spans:
            totals_page = ni_spans[0].page
        dn.totals_page = totals_page

        log.info("Totals on page %d", totals_page)

        p = [s for s in all_spans if s.page == totals_page]

        def amount_near_y(target_y: float, tolerance: float = 12) -> float:
            right = [
                s for s in p
                if s.bbox[0] > 490
                and abs((s.bbox[1] + s.bbox[3]) / 2 - target_y) < tolerance
            ]
            right.sort(key=lambda s: s.bbox[0])
            return parse_float(right[-1].text) if right else 0.0

        # Find y-positions of each total row dynamically
        def find_y(keywords: List[str]) -> float | None:
            for kw in keywords:
                matches = [s for s in p if kw.lower() in s.text.lower()]
                if matches:
                    return (matches[0].bbox[1] + matches[0].bbox[3]) / 2
            return None

        ni_y  = find_y(["Number", "Items:"])

        # Sub Total: handle both combined span and split spans
        # Must be BELOW the table (after last line item) — use ni_y as lower bound reference
        # or use x position: totals label "Sub" is at x<200, table header "Sub" is at x>300
        sub_candidates = [
            s for s in p
            if ("sub total" in s.text.lower() or s.text.strip() == "Sub")
            and s.bbox[0] < 250   # totals label area, not table column header (x~310)
            and s.bbox[1] > 100
        ]
        sub_y = (sub_candidates[0].bbox[1] + sub_candidates[0].bbox[3]) / 2 if sub_candidates else None

        # GST Total: combined or split, must be in label area (x<400)
        gst_candidates = [
            s for s in p
            if ("gst total" in s.text.lower() or ("GST" in s.text and "Total" in s.text))
            and s.bbox[0] < 400 and s.bbox[1] > 100
        ]
        # Fallback: find "GST" span that is NOT in the table column area
        if not gst_candidates:
            gst_candidates = [
                s for s in p
                if s.text.strip() == "GST" and s.bbox[0] < 300 and s.bbox[1] > 100
            ]
        gst_y = (gst_candidates[0].bbox[1] + gst_candidates[0].bbox[3]) / 2 if gst_candidates else None

        tot_y = find_y(["Total Payable", "Payable"])

        if ni_y:
            dn.num_items = int(amount_near_y(ni_y))
        if sub_y:
            dn.sub_total = amount_near_y(sub_y)
        if gst_y:
            dn.gst_total = amount_near_y(gst_y)
        if tot_y:
            dn.total_payable = amount_near_y(tot_y)

        # GST type — look for IGST breakdown line on totals page
        # Handles both split spans ("IGST" + "=") and combined ("IGST = ...")
        igst_spans = [s for s in p if "IGST" in s.text and s.bbox[1] > 100]
        dn.gst_type = "IGST" if igst_spans else "CGST+SGST"

        dn.igst_breakdown = clean_text(" ".join(
            s.text for s in sorted(
                [s for s in p if s.bbox[1] > 185 and s.bbox[1] < 220],
                key=lambda s: (s.bbox[1], s.bbox[0])
            )
        ))

        cess_spans = [s for s in p if "CESS" in s.text]
        dn.cess_text = clean_text(" ".join(s.text for s in cess_spans))

        log.info("Totals: sub=%s gst=%s total=%s items=%d type=%s",
                 dn.sub_total, dn.gst_total, dn.total_payable, dn.num_items, dn.gst_type)

    def _parse_footer(self, dn: DNDocument) -> None:
        last_page = len(self._doc) - 1
        p_spans = [s for s in dn.spans if s.page == last_page and s.bbox[1] > 250]
        dn.footer_text = clean_text(" ".join(s.text for s in p_spans))
