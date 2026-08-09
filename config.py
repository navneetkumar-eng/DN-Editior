"""
DN PDF Editor Pro - Configuration
Central configuration for all application settings.
"""

import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
ASSETS_DIR  = BASE_DIR / "assets"
OUTPUT_DIR  = BASE_DIR / "output"
TEMP_DIR    = BASE_DIR / "temp"
DB_DIR      = BASE_DIR / "database"
LOGS_DIR    = BASE_DIR / "logs"

DATABASE_PATH = DB_DIR / "dn_editor.db"
LOG_FILE      = LOGS_DIR / "app.log"

# ── PDF Processing ─────────────────────────────────────────────────────────────
MAX_PDF_PAGES   = 200
PDF_READ_TIMEOUT = 5   # seconds
PDF_GEN_TIMEOUT  = 3   # seconds
OCR_DPI          = 300
OVERLAY_PADDING  = 2   # pts extra white rect padding per side

# ── Validation ─────────────────────────────────────────────────────────────────
MAX_GST_PERCENT  = 28.0
MIN_QTY          = 0.0
MIN_RATE         = 0.0

# ── UI ─────────────────────────────────────────────────────────────────────────
APP_TITLE    = "DN PDF Editor Pro"
APP_ICON     = "📄"
THEME_COLOR  = "#1f4e79"

# ── Editable Fields ────────────────────────────────────────────────────────────
EDITABLE_FIELDS = ["Qty", "Rate", "GST_%", "Sub_Total", "Other_Charges", "Remarks"]

# ── GST Types ──────────────────────────────────────────────────────────────────
GST_IGST     = "IGST"
GST_CGST_SGST = "CGST+SGST"

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_LEVEL  = "INFO"
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

# Ensure runtime directories exist
for _d in (OUTPUT_DIR, TEMP_DIR, DB_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
