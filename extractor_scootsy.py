"""
DN PDF Editor Pro - Scootsy/Delhivery DN Extractor
Handles landscape format DNs with CGST/SGST/IGST/CESS columns.
"""

from __future__ import annotations

import re
import fitz
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Tuple

from utils import setup_logger, parse_float, clean_text

log = setup_logger(__name__)


@dataclass
class ScootsyLineItem:
    sr_no:          int
    sku_code:       str
    hsn:            str
    sku_desc:       str
    reason:         str
    remarks:        str
    exp_qty:        float
    dn_qty:         float
    lot_mrp:        float
    unit_price:     float
    taxable_value:  float
    tax_amount:     float
    cgst_rate:      float
    cgst_amount:    float
    sgst_rate:      float
    sgst_amount:    float
    igst_rate:      float
    igst_amount:    float
    cess_rate:      float
    cess_amount:    float
    total:          float
    coords:         Dict[str, Tuple] = field(default_factory=dict)
    page:           int = 0


@dataclass
class ScootsyDocument:
    doc_type:        str = "SCOOTSY"

    # Header
    company_name:    str = ""
    warehouse:       str = ""
    gstin:           str = ""
    vendor_name:     str = ""
    vendor_address:  str = ""
    vendor_gstin:    str = ""
    dn_no:           str = ""
    dn_date:         str = ""
    inbound_no:      str = ""
    inbound_date:    str = ""
    grn_no:          str = ""
    grn_date:        str = ""
    invoice_no:      str = ""
    invoice_date:    str = ""
    po_no:           str = ""
    po_date:         str = ""
    grn_qty:         str = ""
    grn_amt:         float = 0.0
    total_dn_qty:    str = ""
    dn_amt:          float = 0.0
    invoice_amt:     float = 0.0
    amount_words:    str = ""

    # Line items
    line_items:      List[ScootsyLineItem] = field(default_factory=list)

    # Totals
    total_exp_qty:   float = 0.0
    total_dn_qty_sum: float = 0.0
    total_unit_price: float = 0.0
    total_taxable:   float = 0.0
    total_tax:       float = 0.0
    total_cgst:      float = 0.0
    total_sgst:      float = 0.0
    total_igst:      float = 0.0
    total_cess:      float = 0.0
    total_amount:    float = 0.0

    spans:           List = field(default_factory=list)
    is_scanned:      bool = False


class ScootsyExtractor:

    def __init__(self, pdf_path: str | Path):
        self.pdf_path = Path(pdf_path)

    def extract(self) -> ScootsyDocument:
        doc_fitz = fitz.open(str(self.pdf_path))
        dn = ScootsyDocument()

        from extractor import SpanInfo
        spans = []
        for pnum, page in enumerate(doc_fitz):
            for b in page.get_text("dict")["blocks"]:
                if b.get("type") != 0:
                    continue
                for line in b["lines"]:
                    for sp in line["spans"]:
                        t = clean_text(sp["text"])
                        if t:
                            spans.append(SpanInfo(
                                text=t, bbox=tuple(sp["bbox"]),
                                font=sp["font"], size=sp["size"], page=pnum,
                            ))
        dn.spans = spans
        p0 = [s for s in spans if s.page == 0]

        self._parse_header(dn, p0)
        self._parse_line_items(dn, p0)
        self._parse_totals(dn, p0)
        doc_fitz.close()
        return dn

    def _val_after(self, spans, label):
        """Find value after label in same row."""
        matches = [s for s in spans if label.lower() in s.text.lower()]
        if not matches:
            return ""
        ls = matches[0]
        row_y = (ls.bbox[1] + ls.bbox[3]) / 2
        right = [s for s in spans
                 if s.bbox[0] > ls.bbox[2] - 2
                 and abs((s.bbox[1]+s.bbox[3])/2 - row_y) < 6]
        right.sort(key=lambda s: s.bbox[0])
        return clean_text(" ".join(s.text for s in right).lstrip(":- "))

    def _parse_header(self, dn: ScootsyDocument, p0) -> None:
        bold = [s for s in p0 if "Bold" in s.font]
        dn.company_name = bold[0].text if bold else ""
        dn.warehouse    = bold[1].text if len(bold) > 1 else ""
        dn.gstin        = next((s.text for s in p0 if "06AAVCS" in s.text or "GSTIN" not in s.text and re.match(r'\d{2}[A-Z]{5}', s.text)), "")

        dn.vendor_name   = self._val_after(p0, "Vendor Name")
        dn.vendor_gstin  = self._val_after(p0, "Vendor GSTIN")
        dn.dn_no         = self._val_after(p0, "DN No")
        dn.dn_date       = self._val_after(p0, "DN Date")
        dn.inbound_no    = self._val_after(p0, "Inbound No")
        dn.inbound_date  = self._val_after(p0, "Inbound Date")
        dn.grn_no        = self._val_after(p0, "GRN No")
        dn.grn_date      = self._val_after(p0, "GRN date")
        dn.invoice_no    = self._val_after(p0, "Invoice No")
        dn.invoice_date  = self._val_after(p0, "Invoice Date")
        dn.po_no         = self._val_after(p0, "PO No")
        dn.po_date       = self._val_after(p0, "PO Date")
        dn.grn_qty       = self._val_after(p0, "GRN Qty")
        dn.grn_amt       = parse_float(self._val_after(p0, "GRN Amt"))
        dn.total_dn_qty  = self._val_after(p0, "Total DN Qty")
        dn.dn_amt        = parse_float(self._val_after(p0, "DN Amt"))
        dn.invoice_amt   = parse_float(self._val_after(p0, "Invoice Amt"))

        amt_span = next((s for s in p0 if "Only" in s.text and "Bold" in s.font), None)
        if amt_span:
            dn.amount_words = amt_span.text

    def _parse_line_items(self, dn: ScootsyDocument, p0) -> None:
        # Table header is around y=297, rows start ~y=336
        # Sr.No column x≈9-22
        table_top = next((s.bbox[1] for s in p0 if s.text == "Sr." and s.bbox[0] < 20), 295)
        total_y   = next((s.bbox[1] for s in p0 if s.text == "Total:" and s.bbox[0] < 100), 368)

        sr_spans = [
            s for s in p0
            if re.match(r"^\d+$", s.text.strip())
            and s.bbox[0] < 20
            and s.bbox[1] > table_top + 20
            and s.bbox[1] < total_y
        ]
        sr_spans.sort(key=lambda s: s.bbox[1])

        for idx, sr_span in enumerate(sr_spans):
            band_top    = sr_span.bbox[1] - 5
            band_bottom = sr_spans[idx+1].bbox[1] - 5 if idx+1 < len(sr_spans) else total_y
            row = [s for s in p0 if s.bbox[1] >= band_top and s.bbox[3] <= band_bottom + 5]
            item = self._build_item(int(sr_span.text.strip()), row)
            if item:
                dn.line_items.append(item)

    def _build_item(self, sr, row) -> ScootsyLineItem | None:
        def tx(x0, x1):
            ss = sorted([s for s in row if s.bbox[0]>=x0-3 and s.bbox[2]<=x1+5],
                        key=lambda s: (s.bbox[1], s.bbox[0]))
            return clean_text(" ".join(s.text for s in ss))

        def nx(x0, x1):
            ss = [s for s in row if s.bbox[0]>=x0-3 and s.bbox[2]<=x1+5
                  and any(c.isdigit() for c in s.text)]
            return ss[0] if ss else None

        # Column x ranges from span analysis (landscape PDF, wider columns)
        sku_s   = nx(28, 90)
        desc    = tx(100, 196)
        reason  = tx(196, 250)
        remarks = tx(270, 335)
        exp_s   = nx(330, 365)
        dn_s    = nx(366, 400)
        mrp_s   = nx(400, 445)
        uprice_s= nx(445, 488)
        taxval_s= nx(488, 530)
        taxamt_s= nx(530, 565)
        cgstr_s = nx(563, 590)
        cgsta_s = nx(588, 625)
        sgstr_s = nx(623, 650)
        sgsta_s = nx(648, 685)
        igstr_s = nx(683, 715)
        igsta_s = nx(713, 755)
        cessr_s = nx(749, 775)
        cessa_s = nx(773, 810)
        total_s = nx(810, 860)

        def v(s): return parse_float(s.text) if s else 0.0
        def b(s): return s.bbox if s else None

        hsn_match = re.search(r'HSN[:\s]+(\d+)', tx(28,90), re.I)

        coords = {
            "dn_qty":       b(dn_s),
            "unit_price":   b(uprice_s),
            "taxable_value":b(taxval_s),
            "tax_amount":   b(taxamt_s),
            "igst_amount":  b(igsta_s),
            "total":        b(total_s),
        }

        return ScootsyLineItem(
            sr_no=sr,
            sku_code=clean_text(sku_s.text if sku_s else "").split("HSN")[0].strip(),
            hsn=hsn_match.group(1) if hsn_match else "",
            sku_desc=desc,
            reason=reason,
            remarks=remarks,
            exp_qty=v(exp_s),
            dn_qty=v(dn_s),
            lot_mrp=v(mrp_s),
            unit_price=v(uprice_s),
            taxable_value=v(taxval_s),
            tax_amount=v(taxamt_s),
            cgst_rate=v(cgstr_s),
            cgst_amount=v(cgsta_s),
            sgst_rate=v(sgstr_s),
            sgst_amount=v(sgsta_s),
            igst_rate=v(igstr_s),
            igst_amount=v(igsta_s),
            cess_rate=v(cessr_s),
            cess_amount=v(cessa_s),
            total=v(total_s),
            coords=coords,
            page=0,
        )

    def _parse_totals(self, dn: ScootsyDocument, p0) -> None:
        total_spans = [s for s in p0 if s.text == "Total:" and s.bbox[0] < 100]
        if not total_spans:
            return
        ty = total_spans[0].bbox[1]
        row = sorted([s for s in p0 if abs(s.bbox[1]-ty) < 8 and s.bbox[0] > 300],
                     key=lambda s: s.bbox[0])
        nums = [parse_float(s.text) for s in row if any(c.isdigit() for c in s.text)]
        if len(nums) >= 8:
            dn.total_exp_qty    = nums[0]
            dn.total_dn_qty_sum = nums[1]
            dn.total_unit_price = nums[2]
            dn.total_taxable    = nums[3]
            dn.total_tax        = nums[4]
            dn.total_cgst       = nums[5]
            dn.total_sgst       = nums[6]
            dn.total_igst       = nums[7]
            dn.total_cess       = nums[8] if len(nums) > 8 else 0.0
            dn.total_amount     = nums[9] if len(nums) > 9 else 0.0
