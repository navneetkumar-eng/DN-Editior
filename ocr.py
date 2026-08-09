"""
DN PDF Editor Pro - OCR Module
Automatic OCR fallback for scanned / image-only PDFs.
"""

from __future__ import annotations

import fitz
from pathlib import Path
from typing import List

from config import OCR_DPI, TEMP_DIR
from utils import setup_logger

log = setup_logger(__name__)


def pdf_to_images(pdf_path: str | Path) -> List[Path]:
    """
    Render each page of a PDF to a PNG image at OCR_DPI.
    Returns list of image paths.
    """
    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))
    images = []

    for page_num, page in enumerate(doc):
        mat = fitz.Matrix(OCR_DPI / 72, OCR_DPI / 72)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        img_path = TEMP_DIR / f"ocr_page_{page_num}.png"
        pix.save(str(img_path))
        images.append(img_path)
        log.info("Rendered page %d → %s", page_num, img_path)

    doc.close()
    return images


def ocr_images(image_paths: List[Path]) -> List[str]:
    """
    Run Tesseract OCR on a list of images.
    Returns list of extracted text strings (one per page).
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        log.error("OCR dependencies missing: %s", e)
        raise RuntimeError("pytesseract or Pillow not installed.") from e

    results = []
    for img_path in image_paths:
        try:
            img = Image.open(str(img_path))
            text = pytesseract.image_to_string(img, config="--oem 3 --psm 6")
            results.append(text)
            log.info("OCR complete for %s (%d chars)", img_path.name, len(text))
        except Exception as e:
            log.error("OCR failed for %s: %s", img_path, e)
            results.append("")

    return results


def run_ocr_pipeline(pdf_path: str | Path) -> List[str]:
    """
    Full OCR pipeline: render PDF pages → run Tesseract → return text per page.
    Called automatically when a scanned PDF is detected.
    """
    log.info("Starting OCR pipeline for %s", pdf_path)
    images = pdf_to_images(pdf_path)
    texts  = ocr_images(images)

    # Cleanup temp images
    for img in images:
        try:
            img.unlink()
        except Exception:
            pass

    return texts
