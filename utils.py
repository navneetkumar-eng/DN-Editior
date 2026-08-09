"""
DN PDF Editor Pro - Utilities
Shared helper functions and logging setup.
"""

import logging
import re
from pathlib import Path
from config import LOG_FILE, LOG_LEVEL, LOG_FORMAT


def setup_logger(name: str) -> logging.Logger:
    """Return a named logger wired to both file and console."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    # File handler
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(logging.Formatter(LOG_FORMAT))

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(LOG_FORMAT))

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def parse_float(value: str | float | int, default: float = 0.0) -> float:
    """Safely parse a numeric string to float."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return default


def format_currency(value: float, decimals: int = 2) -> str:
    """Format a float as a currency string."""
    return f"{value:,.{decimals}f}"


def clean_text(text: str) -> str:
    """Strip extra whitespace from extracted text."""
    return re.sub(r"\s+", " ", text or "").strip()


def safe_path(directory: Path, filename: str) -> Path:
    """Build a safe output path inside directory, sanitising filename."""
    safe_name = re.sub(r'[^\w\-_\. ]', '_', filename)
    return directory / safe_name
