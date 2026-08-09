"""
DN PDF Editor Pro - Database
SQLite-backed audit log and session persistence.
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from config import DATABASE_PATH
from utils import setup_logger

log = setup_logger(__name__)


# ── Schema ─────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    pdf_name    TEXT    NOT NULL,
    field_name  TEXT    NOT NULL,
    row_index   INTEGER DEFAULT NULL,
    old_value   TEXT,
    new_value   TEXT,
    changed_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    pdf_name    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    meta        TEXT          -- JSON blob for any extra info
);
"""


def get_connection() -> sqlite3.Connection:
    """Open (and initialise) the SQLite database."""
    conn = sqlite3.connect(str(DATABASE_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    conn.commit()
    return conn


# ── Session ─────────────────────────────────────────────────────────────────────

def create_session(session_id: str, pdf_name: str, meta: dict | None = None) -> None:
    """Register a new editing session."""
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sessions (session_id, pdf_name, created_at, meta) VALUES (?,?,?,?)",
                (session_id, pdf_name, datetime.utcnow().isoformat(), json.dumps(meta or {}))
            )
    except Exception as e:
        log.error("create_session failed: %s", e)


# ── Audit Log ──────────────────────────────────────────────────────────────────

def log_edit(
    session_id: str,
    pdf_name: str,
    field_name: str,
    old_value: Any,
    new_value: Any,
    row_index: int | None = None,
) -> None:
    """Record a single field edit in the audit log."""
    try:
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO audit_log
                   (session_id, pdf_name, field_name, row_index, old_value, new_value, changed_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    session_id,
                    pdf_name,
                    field_name,
                    row_index,
                    str(old_value),
                    str(new_value),
                    datetime.utcnow().isoformat(),
                ),
            )
    except Exception as e:
        log.error("log_edit failed: %s", e)


def get_audit_log(session_id: str) -> List[Dict]:
    """Return all audit entries for a session as a list of dicts."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        log.error("get_audit_log failed: %s", e)
        return []


def get_all_sessions() -> List[Dict]:
    """Return summary of all sessions."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT session_id, pdf_name, created_at FROM sessions ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        log.error("get_all_sessions failed: %s", e)
        return []
