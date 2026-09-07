"""
DN PDF Editor Pro - Main Application
Clean, simple Streamlit UI for editing Debit Note PDFs.
"""

from __future__ import annotations

import io
import uuid
import base64
import json
from pathlib import Path
from copy import deepcopy

import streamlit as st
import pandas as pd

from config import APP_TITLE, APP_ICON, OUTPUT_DIR, TEMP_DIR
from extractor import DNExtractor, DNDocument
from calculation import calc_row, calc_totals
from validator import validate_rows
from pdf_generator import generate_pdf
from database import create_session, log_edit, get_audit_log
from audit import export_audit_excel, export_dn_json
from utils import setup_logger, format_currency, parse_float

log = setup_logger("app")

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="DN Editor · Powered by Shivam",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CSS ────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Clean background */
.stApp { background: #f5f6fa; }

/* Header banner */
.app-header {
    background: linear-gradient(135deg, #1a3c5e 0%, #2e75b6 100%);
    color: white;
    padding: 1.2rem 1.8rem;
    border-radius: 10px;
    margin-bottom: 1.5rem;
}
.app-header h1 { margin: 0; font-size: 1.8rem; font-weight: 700; }
.app-header p  { margin: 0.3rem 0 0; opacity: 0.85; font-size: 0.9rem; }

/* Section cards */
.section-box {
    background: white;
    border: 1px solid #e0e4ea;
    border-radius: 8px;
    padding: 1.2rem 1.5rem;
    margin-bottom: 1.2rem;
}
.section-title {
    font-size: 1rem;
    font-weight: 700;
    color: #1a3c5e;
    margin-bottom: 0.8rem;
    padding-bottom: 0.4rem;
    border-bottom: 2px solid #e8f0fe;
}

/* Total cards */
.total-card {
    background: white;
    border: 1px solid #e0e4ea;
    border-radius: 8px;
    padding: 1rem;
    text-align: center;
}
.total-card .label { font-size: 0.78rem; color: #666; margin-bottom: 0.3rem; }
.total-card .value { font-size: 1.4rem; font-weight: 700; color: #1a3c5e; }

/* Action buttons */
.stButton > button {
    border-radius: 6px;
    font-weight: 600;
    height: 2.5rem;
    width: 100%;
}

/* Data editor — keep original grid styling */
.stDataEditor {
    border: 1px solid #d0d7e3 !important;
    border-radius: 6px;
}

/* Info / success / error */
.stAlert { border-radius: 6px; }

/* Hide streamlit branding */
#MainMenu, footer { visibility: hidden; }

/* Upload area */
.stFileUploader > div {
    border: 2px dashed #2e75b6 !important;
    border-radius: 8px !important;
    background: #f0f6ff !important;
}

/* Tab styling */
.stTabs [data-baseweb="tab"] {
    font-weight: 600;
    font-size: 0.9rem;
}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Session state
# ══════════════════════════════════════════════════════════════════════════════

def _init_state():
    defaults = {
        "session_id":        None,
        "dn":                None,
        "edited_rows":       [],
        "original_rows":     [],
        "pdf_bytes":         None,
        "pdf_name":          "",
        "generated_pdf":     None,
        "validation_errors": {},
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()



# ══════════════════════════════════════════════════════════════════════════════
# LOGIN
# ══════════════════════════════════════════════════════════════════════════════

def show_login():
    if st.session_state.get("authenticated"):
        return True
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("""
        <div style="text-align:center;padding:2rem 0 1rem 0;">
            <h2>📄 DN Editor</h2>
            <p style="color:#666;">Powered by Shivam</p>
        </div>""", unsafe_allow_html=True)
        st.markdown("#### 🔐 Login Required")
        username = st.text_input("Username", placeholder="Enter username")
        password = st.text_input("Password", type="password", placeholder="Enter password")
        if st.button("Login", type="primary", use_container_width=True):
            from auth import check_login
            if check_login(username, password):
                st.session_state["authenticated"] = True
                st.session_state["logged_in_user"] = username
                st.rerun()
            else:
                st.error("❌ Invalid username or password.")
        st.markdown('<div style="text-align:center;margin-top:1rem;color:#999;font-size:0.8rem;">Contact admin to get access</div>', unsafe_allow_html=True)
    return False

if not show_login():
    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _pdf_preview(pdf_bytes: bytes, height: int = 500):
    # Use Streamlit native viewer (works on Streamlit Cloud)
    st.download_button(
        label="👁️ View PDF (click to open)",
        data=pdf_bytes,
        file_name="preview.pdf",
        mime="application/pdf",
        use_container_width=True,
    )


def _rows_to_dicts(dn: DNDocument) -> list[dict]:
    return [{
        "Sr":            item.sr_no,
        "UPC":           item.upc,
        "Product Name":  item.name.split("HSN")[0].strip(),
        "HSN":           item.hsn,
        "Qty":           item.qty,
        "Rate":          item.rate,
        "Sub Total":     item.sub_total,
        "GST %":         item.gst_pct,
        "Net Amount":    item.net_amount,
        "Reason":        item.reason,
        "Remark":        item.remark,
        "Other Charges": 0.0,
    } for item in dn.line_items]


def _recalc(rows: list[dict], gst_type: str) -> list[dict]:
    updated = []
    for r in rows:
        rc = calc_row(
            qty=float(r.get("Qty", 0)),
            rate=float(r.get("Rate", 0)),
            gst_pct=float(r.get("GST %", 0)),
            other_charges=float(r.get("Other Charges", 0)),
            gst_type=gst_type,
        )
        updated.append({**r, "Sub Total": rc.sub_total, "Net Amount": rc.net_amount})
    return updated


def _to_calc_input(rows: list[dict]) -> list[dict]:
    return [{
        "qty":           r.get("Qty", 0),
        "rate":          r.get("Rate", 0),
        "gst_pct":       r.get("GST %", 0),
        "other_charges": r.get("Other Charges", 0),
        "sub_total":     r.get("Sub Total", 0),
        "net_amount":    r.get("Net Amount", 0),
    } for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("""
<div class="app-header">
    <h1>📄 DN Editor</h1>
    <p>Powered by Shivam &nbsp;·&nbsp; Upload a Debit Note PDF · Edit values · Download updated PDF</p>
</div>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Upload
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("### 📤 Upload Debit Note PDF")

uploaded = st.file_uploader(
    "Drop your PDF here or click to browse",
    type=["pdf"],
    label_visibility="collapsed",
)

if uploaded and uploaded.name != st.session_state.get("pdf_name"):
    with st.spinner("Reading PDF and extracting data…"):
        try:
            tmp = TEMP_DIR / uploaded.name
            tmp.write_bytes(uploaded.getvalue())

            from doc_detector import detect_doc_type
            doc_type = detect_doc_type(tmp)
            st.session_state["doc_type"] = doc_type
            st.session_state["pdf_path"] = tmp

            if doc_type == "ACK":
                from extractor_ack import AckExtractor
                dn = AckExtractor(tmp).extract()
            elif doc_type == "SCOOTSY":
                from extractor_scootsy import ScootsyExtractor
                dn = ScootsyExtractor(tmp).extract()
                sid = str(uuid.uuid4())
                create_session(sid, uploaded.name)
                st.session_state.update({
                    "session_id":    sid,
                    "dn":            dn,
                    "pdf_bytes":     uploaded.getvalue(),
                    "pdf_name":      uploaded.name,
                    "generated_pdf": None,
                    "validation_errors": {},
                })
                if doc_type == "ACK":
                    st.success(f"✅ Loaded **{uploaded.name}** — Acknowledgement Note · {len(dn.receiving_items)} SKUs")
                elif doc_type == "SCOOTSY":
                    st.success(f"✅ Loaded **{uploaded.name}** — Scootsy DN · {len(dn.line_items)} item(s)")
            else:
                extractor = DNExtractor(tmp)
                dn        = extractor.extract()
                if dn.is_scanned:
                    st.warning("⚠️ Scanned PDF detected — running OCR…")
                    from ocr import run_ocr_pipeline
                    run_ocr_pipeline(tmp)
                sid = str(uuid.uuid4())
                create_session(sid, uploaded.name)
                rows = _rows_to_dicts(dn)
                st.session_state.update({
                    "session_id":    sid,
                    "dn":            dn,
                    "edited_rows":   rows,
                    "original_rows": _rows_to_dicts(dn),
                    "pdf_bytes":     uploaded.getvalue(),
                    "pdf_name":      uploaded.name,
                    "generated_pdf": None,
                    "validation_errors": {},
                })
                st.success(f"✅ Loaded **{uploaded.name}** — Discrepancy Note · {len(dn.line_items)} line item(s)")

        except Exception as e:
            log.exception("Extraction failed")
            st.error(f"❌ {e}")

# ══════════════════════════════════════════════════════════════════════════════
# MAIN CONTENT — shown after upload
# ══════════════════════════════════════════════════════════════════════════════

def render_scootsy_ui(dn, pdf_bytes, pdf_name, session_id):
    """Full UI for Scootsy/Delhivery DN editing."""
    import pandas as pd
    from pdf_generator_scootsy import generate_scootsy_pdf

    tab_edit, tab_preview, tab_info = st.tabs(["✏️  Edit & Generate", "📄  PDF Preview", "ℹ️  Document Info"])

    with tab_preview:
        st.markdown("#### Original PDF")
        _pdf_preview(pdf_bytes, height=500)

    with tab_info:
        st.markdown("#### Document Information")
        c1, c2 = st.columns(2)
        with c1:
            st.table({"Field": ["DN No","DN Date","Vendor","Vendor GSTIN","Invoice No","Invoice Date"],
                      "Value": [dn.dn_no, dn.dn_date, dn.vendor_name, dn.vendor_gstin,
                                dn.invoice_no, dn.invoice_date]})
        with c2:
            st.table({"Field": ["GRN No","GRN Date","PO No","GRN Qty","GRN Amt","DN Amt","Invoice Amt"],
                      "Value": [dn.grn_no, dn.grn_date, dn.po_no, dn.grn_qty,
                                dn.grn_amt, dn.dn_amt, dn.invoice_amt]})

    with tab_edit:
        st.markdown("#### Line Items")
        st.caption("Edit DN Qty and Unit Price. Taxable Value, Tax, and Total recalculate automatically.")

        rows = [{
            "Sr":            item.sr_no,
            "SKU Code":      item.sku_code,
            "HSN":           item.hsn,
            "Description":   item.sku_desc,
            "Reason":        item.reason,
            "Exp Qty (PO)":  item.exp_qty,
            "DN Qty ✏️":     item.dn_qty,
            "Lot MRP":       item.lot_mrp,
            "Unit Price ✏️": item.unit_price,
            "Taxable Value": item.taxable_value,
            "Tax Amount":    item.tax_amount,
            "CGST Rate":     item.cgst_rate,
            "CGST Amt":      item.cgst_amount,
            "SGST Rate":     item.sgst_rate,
            "SGST Amt":      item.sgst_amount,
            "IGST Rate":     item.igst_rate,
            "IGST Amt":      item.igst_amount,
            "CESS Rate":     item.cess_rate,
            "CESS Amt":      item.cess_amount,
            "Total":         item.total,
        } for item in dn.line_items]

        col_config = {
            "Sr":            st.column_config.NumberColumn("Sr",          disabled=True, width="small"),
            "SKU Code":      st.column_config.TextColumn("SKU Code",      disabled=True, width="medium"),
            "HSN":           st.column_config.TextColumn("HSN",           disabled=True, width="small"),
            "Description":   st.column_config.TextColumn("Description",   disabled=True, width="large"),
            "Reason":        st.column_config.TextColumn("Reason",        disabled=True, width="small"),
            "Exp Qty (PO)":  st.column_config.NumberColumn("Exp Qty",     disabled=True, width="small"),
            "DN Qty ✏️":     st.column_config.NumberColumn("DN Qty ✏️",   min_value=0, step=1, format="%g", width="small"),
            "Lot MRP":       st.column_config.NumberColumn("Lot MRP",     disabled=True, format="%.2f", width="small"),
            "Unit Price ✏️": st.column_config.NumberColumn("Unit Price ✏️", min_value=0, format="%.2f", width="medium"),
            "Taxable Value": st.column_config.NumberColumn("Taxable Val", disabled=True, format="%.2f", width="medium"),
            "Tax Amount":    st.column_config.NumberColumn("Tax Amt",     disabled=True, format="%.2f", width="medium"),
            "CGST Rate":     st.column_config.NumberColumn("CGST%",       disabled=True, format="%.2f", width="small"),
            "CGST Amt":      st.column_config.NumberColumn("CGST Amt",    disabled=True, format="%.2f", width="small"),
            "SGST Rate":     st.column_config.NumberColumn("SGST%",       disabled=True, format="%.2f", width="small"),
            "SGST Amt":      st.column_config.NumberColumn("SGST Amt",    disabled=True, format="%.2f", width="small"),
            "IGST Rate":     st.column_config.NumberColumn("IGST%",       disabled=True, format="%.2f", width="small"),
            "IGST Amt":      st.column_config.NumberColumn("IGST Amt",    disabled=True, format="%.2f", width="medium"),
            "CESS Rate":     st.column_config.NumberColumn("CESS%",       disabled=True, format="%.2f", width="small"),
            "CESS Amt":      st.column_config.NumberColumn("CESS Amt",    disabled=True, format="%.2f", width="small"),
            "Total":         st.column_config.NumberColumn("Total",       disabled=True, format="%.2f", width="medium"),
        }

        edited_df = st.data_editor(
            pd.DataFrame(rows), column_config=col_config,
            use_container_width=True, num_rows="fixed",
            hide_index=True, key="scootsy_editor",
        )

        # Recalculate
        import math
        edited_rows = edited_df.to_dict("records")
        for r, item in zip(edited_rows, dn.line_items):
            taxable = round(float(r["DN Qty ✏️"]) * float(r["Unit Price ✏️"]), 2)
            tax_amt = round(math.floor(taxable * item.igst_rate / 100 * 100) / 100, 2)
            r["Taxable Value"] = taxable
            r["Tax Amount"]    = tax_amt
            r["IGST Amt"]      = tax_amt
            r["Total"]         = round(taxable + tax_amt, 2)

        # Totals
        tot_dn  = sum(r["DN Qty ✏️"] for r in edited_rows)
        tot_tax = sum(r["Taxable Value"] for r in edited_rows)
        tot_gst = sum(r["Tax Amount"] for r in edited_rows)
        tot_amt = sum(r["Total"] for r in edited_rows)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total DN Qty",     str(int(tot_dn)))
        c2.metric("Taxable Value",    f"₹ {tot_tax:,.2f}")
        c3.metric("Total GST (IGST)", f"₹ {tot_gst:,.2f}")
        c4.metric("Grand Total",      f"₹ {tot_amt:,.2f}")

        st.divider()
        st.markdown("#### Actions")
        b1, b2, b3 = st.columns(3)

        with b1:
            if st.button("🔄 Reset", use_container_width=True, key="scootsy_reset"):
                st.session_state.generated_pdf = None
                st.rerun()

        with b2:
            if st.button("⚙️ Generate PDF", type="primary", use_container_width=True, key="scootsy_gen"):
                with st.spinner("Generating PDF…"):
                    try:
                        tmp_path = st.session_state.pdf_path
                        gen_input = [{"dn_qty": r["DN Qty ✏️"], "unit_price": r["Unit Price ✏️"]}
                                     for r in edited_rows]
                        out = generate_scootsy_pdf(tmp_path, dn, gen_input)
                        st.session_state.generated_pdf = out
                        st.success(f"✅ PDF ready: **{out.name}**")
                    except Exception as e:
                        log.exception("Scootsy PDF generation failed")
                        st.error(f"❌ {e}")

        with b3:
            gen = st.session_state.get("generated_pdf")
            if gen and Path(gen).exists():
                st.download_button("⬇️ Download PDF", data=Path(gen).read_bytes(),
                                   file_name=Path(gen).name, mime="application/pdf",
                                   use_container_width=True)
            else:
                st.button("⬇️ Download PDF", disabled=True, use_container_width=True)

        gen = st.session_state.get("generated_pdf")
        if gen and Path(gen).exists():
            st.divider()
            st.markdown("#### 📄 Generated PDF Preview")
            _pdf_preview(Path(gen).read_bytes(), height=500)


def render_ack_ui(dn, pdf_bytes, pdf_name, session_id):
    """Full UI for TBOF Acknowledgement Note editing."""
    import pandas as pd
    from extractor_ack import AckDocument
    from pdf_generator_ack import generate_ack_pdf

    tab_edit, tab_preview, tab_info = st.tabs(["✏️  Edit & Generate", "📄  PDF Preview", "ℹ️  Document Info"])

    with tab_preview:
        st.markdown("#### Original PDF")
        _pdf_preview(pdf_bytes, height=700)

    with tab_info:
        st.markdown("#### Document Information")
        c1, c2 = st.columns(2)
        with c1:
            st.table({"Field": ["Vendor","PO","Invoice","ASN","PO Date","ASN Date","Close Date"],
                      "Value": [dn.vendor_name, dn.po, dn.invoice, dn.asn,
                                dn.po_created_date, dn.asn_created_date, dn.asn_close_date]})
        with c2:
            st.table({"Field": ["SKU Received","GRN Quantity","GRN Value","Disc SKU","Disc Quantity","Disc Value"],
                      "Value": [dn.sku_received, dn.grn_quantity, dn.grn_value,
                                dn.discrepancy_sku, dn.discrepancy_quantity, dn.discrepancy_value]})

    with tab_edit:
        # ── Receiving Details ──────────────────────────────────────────────────
        st.markdown("#### Receiving Details")
        st.caption("Edit Received Qty and Unit Price. Value recalculates automatically.")

        recv_rows = [{
            "Sr": item.sr_no,
            "EAN": item.ean,
            "Description": item.description.split("SKU")[0].strip(),
            "ASN Qty": item.asn_qty,
            "Received Qty ✏️": item.received_qty,
            "Unit Price ✏️": item.unit_price,
            "Value": round(item.received_qty * item.unit_price, 2),
        } for item in dn.receiving_items]

        recv_col_config = {
            "Sr":              st.column_config.NumberColumn("Sr",          disabled=True, width="small"),
            "EAN":             st.column_config.TextColumn("EAN",           disabled=True, width="medium"),
            "Description":     st.column_config.TextColumn("Description",   disabled=True, width="large"),
            "ASN Qty":         st.column_config.NumberColumn("ASN Qty",     disabled=True, width="small"),
            "Received Qty ✏️": st.column_config.NumberColumn("Rcvd Qty ✏️", min_value=0, step=1, format="%g", width="small"),
            "Unit Price ✏️":   st.column_config.NumberColumn("Unit Price ✏️", min_value=0, format="%.2f", width="medium"),
            "Value":           st.column_config.NumberColumn("Value",       disabled=True, format="%.2f", width="medium"),
        }

        recv_df = st.data_editor(
            pd.DataFrame(recv_rows),
            column_config=recv_col_config,
            use_container_width=True,
            num_rows="fixed",
            hide_index=True,
            key="ack_recv_editor",
        )

        # Recalculate values
        recv_edited = recv_df.to_dict("records")
        for r in recv_edited:
            r["Value"] = round(float(r["Received Qty ✏️"]) * float(r["Unit Price ✏️"]), 2)

        # Show live totals
        total_rcvd  = sum(r["Received Qty ✏️"] for r in recv_edited)
        total_value = sum(r["Value"] for r in recv_edited)
        tc1, tc2, tc3 = st.columns(3)
        tc1.metric("Total ASN Qty",      str(int(dn.receiving_total_asn)))
        tc2.metric("Total Received Qty", str(int(total_rcvd)))
        tc3.metric("Total Value",        f"₹ {total_value:,.2f}")

        st.divider()

        # ── Discrepancy Details ────────────────────────────────────────────────
        st.markdown("#### Discrepancy Details")
        st.caption("Edit Discrepancy Qty and Unit Price.")

        disc_rows = [{
            "Sr": item.sr_no,
            "EAN": item.ean,
            "Description": item.description,
            "Type": item.discrepancy_type,
            "Disc Qty ✏️": item.discrepancy_qty,
            "Unit Price ✏️": item.unit_price,
            "Value": round(item.discrepancy_qty * item.unit_price, 2),
        } for item in dn.discrepancy_items]

        disc_col_config = {
            "Sr":             st.column_config.NumberColumn("Sr",         disabled=True, width="small"),
            "EAN":            st.column_config.TextColumn("EAN",          disabled=True, width="medium"),
            "Description":    st.column_config.TextColumn("Description",  disabled=True, width="large"),
            "Type":           st.column_config.TextColumn("Type",         disabled=True, width="small"),
            "Disc Qty ✏️":    st.column_config.NumberColumn("Disc Qty ✏️", min_value=0, step=1, format="%g", width="small"),
            "Unit Price ✏️":  st.column_config.NumberColumn("Unit Price ✏️", min_value=0, format="%.2f", width="medium"),
            "Value":          st.column_config.NumberColumn("Value",      disabled=True, format="%.2f", width="medium"),
        }

        disc_df = st.data_editor(
            pd.DataFrame(disc_rows),
            column_config=disc_col_config,
            use_container_width=True,
            num_rows="fixed",
            hide_index=True,
            key="ack_disc_editor",
        )

        disc_edited = disc_df.to_dict("records")
        for r in disc_edited:
            r["Value"] = round(float(r["Disc Qty ✏️"]) * float(r["Unit Price ✏️"]), 2)

        disc_total_qty   = sum(r["Disc Qty ✏️"] for r in disc_edited)
        disc_total_value = sum(r["Value"] for r in disc_edited)
        dc1, dc2 = st.columns(2)
        dc1.metric("Total Disc Qty",   str(int(disc_total_qty)))
        dc2.metric("Total Disc Value", f"₹ {disc_total_value:,.2f}")

        st.divider()

        # ── Actions ────────────────────────────────────────────────────────────
        st.markdown("#### Actions")
        b1, b2, b3 = st.columns(3)

        with b1:
            if st.button("🔄 Reset", use_container_width=True):
                st.session_state.generated_pdf = None
                st.rerun()

        with b2:
            if st.button("⚙️ Generate PDF", type="primary", use_container_width=True):
                with st.spinner("Generating PDF…"):
                    try:
                        tmp_path = st.session_state.pdf_path

                        edited_recv_input = [{
                            "received_qty": r["Received Qty ✏️"],
                            "unit_price":   r["Unit Price ✏️"],
                        } for r in recv_edited]

                        edited_disc_input = [{
                            "discrepancy_qty": r["Disc Qty ✏️"],
                            "unit_price":      r["Unit Price ✏️"],
                        } for r in disc_edited]

                        out = generate_ack_pdf(tmp_path, dn, edited_recv_input, edited_disc_input)
                        st.session_state.generated_pdf = out
                        st.success(f"✅ PDF ready: **{out.name}**")
                    except Exception as e:
                        log.exception("ACK PDF generation failed")
                        st.error(f"❌ {e}")

        with b3:
            gen = st.session_state.get("generated_pdf")
            if gen and Path(gen).exists():
                st.download_button(
                    "⬇️ Download PDF",
                    data=Path(gen).read_bytes(),
                    file_name=Path(gen).name,
                    mime="application/pdf",
                    use_container_width=True,
                )
            else:
                st.button("⬇️ Download PDF", disabled=True, use_container_width=True)

        # Generated preview
        gen = st.session_state.get("generated_pdf")
        if gen and Path(gen).exists():
            st.divider()
            st.markdown("#### 📄 Generated PDF Preview")
            _pdf_preview(Path(gen).read_bytes(), height=600)


dn = st.session_state.get("dn")
doc_type = st.session_state.get("doc_type", "DN")

if not dn:
    st.info("👆 Upload a Debit Note or Acknowledgement Note PDF above to get started.")
    st.stop()

# Route to correct UI based on doc type
if doc_type == "ACK":
    render_ack_ui(dn, st.session_state.pdf_bytes, st.session_state.pdf_name, st.session_state.session_id)
    st.markdown("---")
    st.caption("DN Editor · Powered by Shivam")
    if st.button("🚪 Logout"):
        st.session_state.clear()
        st.rerun()
    st.stop()

if doc_type == "SCOOTSY":
    render_scootsy_ui(dn, st.session_state.pdf_bytes, st.session_state.pdf_name, st.session_state.session_id)
    st.markdown("---")
    st.caption("DN Editor · Powered by Shivam")
    if st.button("🚪 Logout"):
        st.session_state.clear()
        st.rerun()
    st.stop()

# ── DN Tabs ────────────────────────────────────────────────────────────────────
from extractor import DNDocument
tab_edit, tab_preview, tab_info = st.tabs(["✏️  Edit & Generate", "📄  PDF Preview", "ℹ️  Document Info"])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Edit & Generate
# ══════════════════════════════════════════════════════════════════════════════

with tab_edit:

    # ── Editable grid ──────────────────────────────────────────────────────────
    st.markdown("#### Line Items")
    st.caption("✏️ columns marked with pencil icon are editable. All others are read-only.")

    col_config = {
        "Sr":            st.column_config.NumberColumn("Sr",           disabled=True,  width="small"),
        "UPC":           st.column_config.TextColumn("UPC",            disabled=True,  width="medium"),
        "Product Name":  st.column_config.TextColumn("Product Name",   disabled=True,  width="large"),
        "HSN":           st.column_config.TextColumn("HSN",            disabled=True,  width="small"),
        "Qty":           st.column_config.NumberColumn("Qty ✏️",       min_value=0, step=1,   format="%g",   width="small"),
        "Rate":          st.column_config.NumberColumn("Rate ✏️",      min_value=0,           format="%.2f", width="medium"),
        "Sub Total":     st.column_config.NumberColumn("Sub Total",    disabled=True,         format="%.2f", width="medium"),
        "GST %":         st.column_config.NumberColumn("GST % ✏️",    min_value=0, max_value=28, format="%.2f", width="small"),
        "Net Amount":    st.column_config.NumberColumn("Net Amount",   disabled=True,         format="%.2f", width="medium"),
        "Other Charges": st.column_config.NumberColumn("Other Charges ✏️", min_value=0,      format="%.2f", width="medium"),
        "Reason":        st.column_config.TextColumn("Reason",         disabled=True,  width="medium"),
        "Remark":        st.column_config.TextColumn("Remark ✏️",                      width="medium"),
    }

    rows = st.session_state.edited_rows

    edited_df = st.data_editor(
        pd.DataFrame(rows),
        column_config=col_config,
        use_container_width=True,
        num_rows="fixed",
        hide_index=True,
        key="grid_editor",
        column_order=["Sr","UPC","Product Name","HSN","Qty","Rate","Sub Total","GST %","Net Amount","Other Charges","Reason","Remark"],
    )

    # Detect & log changes
    new_rows = edited_df.to_dict("records")
    for i, (old, new) in enumerate(zip(rows, new_rows)):
        for field in ["Qty","Rate","GST %","Other Charges","Remark"]:
            if old.get(field) != new.get(field):
                log_edit(st.session_state.session_id, st.session_state.pdf_name,
                         field, old.get(field), new.get(field), row_index=i)

    # Recalculate
    recalculated = _recalc(new_rows, dn.gst_type)
    st.session_state.edited_rows = recalculated

    # Validation errors
    calc_input = _to_calc_input(recalculated)
    errors = st.session_state.validation_errors
    if errors:
        for msg in errors.values():
            st.error(f"🔴 {msg}")

    st.divider()

    # ── Totals ─────────────────────────────────────────────────────────────────
    st.markdown("#### Calculated Totals")
    totals = calc_totals(calc_input, gst_type=dn.gst_type)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Sub Total (excl. tax)",   f"₹ {format_currency(totals.sub_total)}")
    c2.metric("Total GST",               f"₹ {format_currency(totals.gst_total)}")
    c3.metric("Grand Total (incl. tax)", f"₹ {format_currency(totals.total_payable)}")
    c4.metric("Total Items (qty)",       str(totals.num_items))

    if dn.gst_type == "IGST":
        st.info(f"**IGST:** ₹ {format_currency(totals.igst_total)}")
    else:
        ca, cb = st.columns(2)
        ca.info(f"**CGST:** ₹ {format_currency(totals.cgst_total)}")
        cb.info(f"**SGST:** ₹ {format_currency(totals.sgst_total)}")

    st.divider()

    # ── Action buttons ─────────────────────────────────────────────────────────
    st.markdown("#### Actions")
    b1, b2, b3, b4, b5 = st.columns(5)

    with b1:
        if st.button("🔄 Reset", use_container_width=True):
            st.session_state.edited_rows   = list(st.session_state.original_rows)
            st.session_state.generated_pdf = None
            st.session_state.validation_errors = {}
            st.rerun()

    with b2:
        if st.button("⚙️ Generate PDF", type="primary", use_container_width=True):
            vr = validate_rows(calc_input)
            if not vr.valid:
                st.session_state.validation_errors = vr.errors
                st.rerun()
            else:
                st.session_state.validation_errors = {}
                with st.spinner("Generating PDF…"):
                    try:
                        tmp_path = TEMP_DIR / st.session_state.pdf_name
                        out = generate_pdf(tmp_path, dn, calc_input, totals)
                        st.session_state.generated_pdf = out
                        st.success(f"✅ PDF ready: **{out.name}**")
                    except Exception as e:
                        log.exception("PDF generation failed")
                        st.error(f"❌ {e}")

    with b3:
        gen = st.session_state.get("generated_pdf")
        if gen and Path(gen).exists():
            st.download_button(
                "⬇️ Download PDF",
                data=Path(gen).read_bytes(),
                file_name=Path(gen).name,
                mime="application/pdf",
                use_container_width=True,
            )
        else:
            st.button("⬇️ Download PDF", disabled=True, use_container_width=True)

    with b4:
        if st.button("📊 Export Excel", use_container_width=True):
            xls = export_audit_excel(st.session_state.session_id, st.session_state.pdf_name)
            st.download_button("⬇️ Download Excel", data=xls.read_bytes(),
                               file_name=xls.name,
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    with b5:
        if st.button("🗂️ Export JSON", use_container_width=True):
            j = json.dumps(calc_input, indent=2, default=str)
            st.download_button("⬇️ Download JSON", data=j.encode(),
                               file_name=f"dn_{st.session_state.pdf_name}.json",
                               mime="application/json")

    # ── Generated PDF preview ──────────────────────────────────────────────────
    gen = st.session_state.get("generated_pdf")
    if gen and Path(gen).exists():
        st.divider()
        st.markdown("#### 📄 Generated PDF Preview")
        _pdf_preview(Path(gen).read_bytes(), height=600)

    # ── Audit log ──────────────────────────────────────────────────────────────
    with st.expander("📋 Audit Log", expanded=False):
        logs = get_audit_log(st.session_state.session_id or "")
        if logs:
            df_log = pd.DataFrame(logs)[["field_name","row_index","old_value","new_value","changed_at"]]
            df_log.columns = ["Field","Row","Old Value","New Value","Changed At"]
            st.dataframe(df_log, use_container_width=True, hide_index=True)
        else:
            st.info("No edits recorded yet.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — PDF Preview
# ══════════════════════════════════════════════════════════════════════════════

with tab_preview:
    st.markdown("#### Original PDF")
    if st.session_state.pdf_bytes:
        _pdf_preview(st.session_state.pdf_bytes, height=700)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Document Info
# ══════════════════════════════════════════════════════════════════════════════

with tab_info:
    st.markdown("#### Extracted Document Information")

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Buyer Details**")
        st.table({
            "Field": ["Document","Buyer ID","Purchase No","Date","Buyer GST","CIN No","PAN No"],
            "Value": [dn.title, dn.buyer_id, dn.purchase_no, dn.date,
                      dn.buyer_gst, dn.cin_no, dn.pan_no],
        })
    with col_b:
        st.markdown("**Supplier & Invoice Details**")
        st.table({
            "Field": ["Supplier GST","Original Invoice ID","Invoice To","GST Type"],
            "Value": [dn.supplier_gst, dn.original_inv_id,
                      dn.inv_to_name, dn.gst_type],
        })

    st.markdown("**Original Totals (from PDF)**")
    st.table({
        "Field": ["Sub Total","GST Total","Total Payable","Number of Items"],
        "Value": [dn.sub_total, dn.gst_total, dn.total_payable, dn.num_items],
    })


# ── Footer ─────────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption("DN PDF Editor Pro · Fully offline · No data leaves your machine")


# ══════════════════════════════════════════════════════════════════════════════
# ACKNOWLEDGEMENT NOTE UI — rendered when doc_type == "ACK"
# ══════════════════════════════════════════════════════════════════════════════
