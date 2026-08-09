"""
DN PDF Editor Pro - Acknowledgement Note Extractor
Handles TBOF-style GRN Acknowledgement Notes.
Different structure from Blinkit Discrepancy Notes.
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
class AckReceivingItem:
    sr_no:       int
    ean:         str
    sku_id:      str
    description: str
    asn_qty:     float
    received_qty: float
    unit_price:  float
    value:       float
    coords:      Dict[str, Tuple] = field(default_factory=dict)
    page:        int = 0


@dataclass
class AckDiscrepancyItem:
    sr_no:            int
    ean:              str
    sku_id:           str
    description:      str
    discrepancy_type: str
    discrepancy_qty:  float
    unit_price:       float
    value:            float
    coords:           Dict[str, Tuple] = field(default_factory=dict)
    page:             int = 0


@dataclass
class AckDocument:
    # Document type identifier
    doc_type:             str = "ACK"

    # Header fields
    vendor_name:          str = ""
    vendor_address:       str = ""
    po:                   str = ""
    invoice:              str = ""
    asn:                  str = ""
    po_created_date:      str = ""
    asn_created_date:     str = ""
    asn_close_date:       str = ""
    sku_received:         int = 0
    grn_quantity:         int = 0
    grn_value:            float = 0.0
    discrepancy_sku:      int = 0
    discrepancy_quantity: int = 0
    discrepancy_value:    float = 0.0

    # Tables
    receiving_items:      List[AckReceivingItem] = field(default_factory=list)
    discrepancy_items:    List[AckDiscrepancyItem] = field(default_factory=list)

    # Totals (from PDF)
    receiving_total_asn:  float = 0.0
    receiving_total_rcvd: float = 0.0
    receiving_total_value: float = 0.0
    disc_total_qty:       float = 0.0
    disc_total_value:     float = 0.0

    # Raw spans
    spans:                List = field(default_factory=list)
    is_scanned:           bool = False


class AckExtractor:

    # Column x-ranges (from span analysis)
    # Sr: 10-30 | EAN: 34-120 | SKU_ID: 124-220
    # Desc: 225-375 | ASN_QTY: 380-430 | RCVD_QTY: 437-490
    # UNIT_PRICE: 496-540 | VALUE: 540-590
    # Discrepancy section:
    # Disc_Type: 357-432 | Disc_Qty: 433-508 | Unit: 510-552 | Value: 553-590

    def __init__(self, pdf_path: str | Path):
        self.pdf_path = Path(pdf_path)

    def extract(self) -> AckDocument:
        doc_obj = fitz.open(str(self.pdf_path))
        dn = AckDocument()

        # Collect all spans
        spans = []
        for pnum, page in enumerate(doc_obj):
            for b in page.get_text("dict")["blocks"]:
                if b.get("type") != 0:
                    continue
                for line in b["lines"]:
                    for sp in line["spans"]:
                        t = clean_text(sp["text"])
                        if t:
                            from extractor import SpanInfo
                            spans.append(SpanInfo(
                                text=t,
                                bbox=tuple(sp["bbox"]),
                                font=sp["font"],
                                size=sp["size"],
                                page=pnum,
                            ))
        dn.spans = spans
        p0 = [s for s in spans if s.page == 0]

        self._parse_header(dn, p0)
        self._parse_receiving(dn, p0)
        self._parse_discrepancy(dn, p0)
        doc_obj.close()
        return dn

    # ── Header ─────────────────────────────────────────────────────────────────

    def _parse_header(self, dn: AckDocument, spans) -> None:
        def val(label):
            for s in spans:
                if label.lower() in s.text.lower():
                    # Value is after the colon in the same span
                    if ":" in s.text:
                        return s.text.split(":", 1)[1].strip()
            return ""

        dn.vendor_name     = val("Vendor Name")
        dn.vendor_address  = val("Vendor Address")
        dn.po              = val("PO:")
        dn.invoice         = val("Invoice:")
        dn.asn             = val("ASN:")
        dn.po_created_date = val("PO Created")
        dn.asn_created_date= val("ASN Created")
        dn.asn_close_date  = val("ASN Close")
        dn.sku_received    = int(parse_float(val("SKU received")))
        dn.grn_quantity    = int(parse_float(val("GRN Quantity")))
        dn.grn_value       = parse_float(val("GRN Value"))
        dn.discrepancy_sku = int(parse_float(val("Discrepancy SKU")))
        dn.discrepancy_quantity = int(parse_float(val("Discrepancy Quantity")))
        dn.discrepancy_value    = parse_float(val("Discrepancy Value"))

    # ── Receiving Details table ────────────────────────────────────────────────

    def _parse_receiving(self, dn: AckDocument, spans) -> None:
        """
        Receiving section: y range 210–490 (before Discrepancy Details at y≈521)
        """
        # Find section boundaries
        recv_header_y = next((s.bbox[1] for s in spans if "Receiving Details" in s.text), 166)
        disc_header_y = next((s.bbox[1] for s in spans if "Discrepancy Details" in s.text), 521)

        # Sr.no anchors in receiving section
        sr_spans = [
            s for s in spans
            if re.match(r"^\d+$", s.text.strip())
            and s.bbox[0] < 25
            and s.bbox[1] > recv_header_y + 30
            and s.bbox[1] < disc_header_y - 10
        ]
        sr_spans.sort(key=lambda s: s.bbox[1])

        for idx, sr_span in enumerate(sr_spans):
            band_top    = sr_span.bbox[1] - 15
            band_bottom = sr_spans[idx+1].bbox[1] - 5 if idx+1 < len(sr_spans) else disc_header_y - 10

            row = [s for s in spans if s.bbox[1] >= band_top and s.bbox[3] <= band_bottom + 5]

            item = self._build_receiving_item(int(sr_span.text.strip()), row)
            if item:
                dn.receiving_items.append(item)

        # Receiving totals row
        total_spans = [s for s in spans if s.text == "Total" and s.bbox[1] > recv_header_y and s.bbox[1] < disc_header_y]
        if total_spans:
            ty = total_spans[0].bbox[1]
            tot_row = [s for s in spans if abs(s.bbox[1] - ty) < 8]
            nums = [s for s in tot_row if s.bbox[0] > 370 and any(c.isdigit() for c in s.text)]
            nums.sort(key=lambda s: s.bbox[0])
            if len(nums) >= 3:
                dn.receiving_total_asn   = parse_float(nums[0].text)
                dn.receiving_total_rcvd  = parse_float(nums[1].text)
                dn.receiving_total_value = parse_float(nums[2].text)

    def _build_receiving_item(self, sr, row) -> AckReceivingItem | None:
        def text_x(x0, x1):
            ss = sorted([s for s in row if s.bbox[0] >= x0-5 and s.bbox[2] <= x1+10],
                        key=lambda s: (s.bbox[1], s.bbox[0]))
            return clean_text(" ".join(s.text for s in ss))

        def num_x(x0, x1):
            ss = [s for s in row if s.bbox[0] >= x0-5 and s.bbox[2] <= x1+10
                  and any(c.isdigit() for c in s.text)]
            return ss[0] if ss else None

        ean   = text_x(34, 120)
        skuid = text_x(124, 222)
        desc  = text_x(225, 378)
        asn_s = num_x(378, 432)
        rcv_s = num_x(435, 493)
        upr_s = num_x(494, 542)
        val_s = num_x(538, 590)

        coords = {
            "received_qty": (rcv_s.bbox if rcv_s else None),
            "unit_price":   (upr_s.bbox if upr_s else None),
            "value":        (val_s.bbox if val_s else None),
        }

        return AckReceivingItem(
            sr_no=sr,
            ean=ean,
            sku_id=skuid,
            description=desc,
            asn_qty=parse_float(asn_s.text if asn_s else "0"),
            received_qty=parse_float(rcv_s.text if rcv_s else "0"),
            unit_price=parse_float(upr_s.text if upr_s else "0"),
            value=parse_float(val_s.text if val_s else "0"),
            coords=coords,
            page=0,
        )

    # ── Discrepancy Details table ──────────────────────────────────────────────

    def _parse_discrepancy(self, dn: AckDocument, spans) -> None:
        disc_header_y = next((s.bbox[1] for s in spans if "Discrepancy Details" in s.text), 521)
        end_y = 700

        sr_spans = [
            s for s in spans
            if re.match(r"^\d+$", s.text.strip())
            and s.bbox[0] < 25
            and s.bbox[1] > disc_header_y + 30
        ]
        sr_spans.sort(key=lambda s: s.bbox[1])

        for idx, sr_span in enumerate(sr_spans):
            band_top    = sr_span.bbox[1] - 15
            band_bottom = sr_spans[idx+1].bbox[1] - 5 if idx+1 < len(sr_spans) else end_y
            row = [s for s in spans if s.bbox[1] >= band_top and s.bbox[3] <= band_bottom + 5]
            item = self._build_disc_item(int(sr_span.text.strip()), row)
            if item:
                dn.discrepancy_items.append(item)

        # Discrepancy totals
        total_spans = [s for s in spans if s.text == "Total" and s.bbox[1] > disc_header_y]
        if total_spans:
            ty = total_spans[0].bbox[1]
            tot_row = [s for s in spans if abs(s.bbox[1] - ty) < 8]
            nums = sorted([s for s in tot_row if s.bbox[0] > 400 and any(c.isdigit() for c in s.text)],
                          key=lambda s: s.bbox[0])
            if len(nums) >= 2:
                dn.disc_total_qty   = parse_float(nums[0].text)
                dn.disc_total_value = parse_float(nums[1].text)

    def _build_disc_item(self, sr, row) -> AckDiscrepancyItem | None:
        def text_x(x0, x1):
            ss = sorted([s for s in row if s.bbox[0] >= x0-5 and s.bbox[2] <= x1+10],
                        key=lambda s: (s.bbox[1], s.bbox[0]))
            return clean_text(" ".join(s.text for s in ss))

        def num_x(x0, x1):
            ss = [s for s in row if s.bbox[0] >= x0-5 and s.bbox[2] <= x1+10
                  and any(c.isdigit() for c in s.text)]
            return ss[0] if ss else None

        ean   = text_x(34, 120)
        skuid = text_x(124, 222)
        desc  = text_x(226, 356)
        dtype = text_x(357, 432)
        qty_s = num_x(433, 508)
        upr_s = num_x(508, 553)
        val_s = num_x(553, 590)

        coords = {
            "discrepancy_qty": (qty_s.bbox if qty_s else None),
            "unit_price":      (upr_s.bbox if upr_s else None),
            "value":           (val_s.bbox if val_s else None),
        }

        return AckDiscrepancyItem(
            sr_no=sr,
            ean=ean,
            sku_id=skuid,
            description=desc,
            discrepancy_type=dtype,
            discrepancy_qty=parse_float(qty_s.text if qty_s else "0"),
            unit_price=parse_float(upr_s.text if upr_s else "0"),
            value=parse_float(val_s.text if val_s else "0"),
            coords=coords,
            page=0,
        )
