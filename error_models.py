# ============================================
# ERROR DATABASE MODELS
# ============================================
# Manages the error_logs table for admin error dashboard
# ============================================

import json
import time
import hashlib
import sqlite3
import os
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from config import Config

logger = logging.getLogger(__name__)

# ============================================
# ERROR LOG SCHEMA
# ============================================

ERROR_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS error_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('CRITICAL', 'ERROR', 'WARNING')),
    status_code INTEGER,
    url TEXT,
    method TEXT,
    user_id INTEGER,
    ip_address TEXT,
    error_type TEXT,
    error_message TEXT,
    stack_trace TEXT,
    user_description TEXT,
    occurrence_count INTEGER DEFAULT 1,
    first_seen TEXT,
    last_seen TEXT,
    resolved INTEGER DEFAULT 0,
    dismissed INTEGER DEFAULT 0,
    resolution_note TEXT,
    error_hash TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_error_logs_timestamp ON error_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_error_logs_severity ON error_logs(severity);
CREATE INDEX IF NOT EXISTS idx_error_logs_resolved ON error_logs(resolved);
CREATE INDEX IF NOT EXISTS idx_error_logs_error_hash ON error_logs(error_hash);
CREATE INDEX IF NOT EXISTS idx_error_logs_request_id ON error_logs(request_id);
"""


# ============================================
# DATABASE CONNECTION HELPER
# ============================================

def _get_db_path() -> str:
    """Get the database path from config."""
    return Config.DATABASE_PATH


def _get_connection():
    """Get a direct database connection without Flask context."""
    try:
        db_path = _get_db_path()
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(f"PRAGMA busy_timeout = {Config.DB_BUSY_TIMEOUT}")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        raise


# ============================================
# ERROR LOG FUNCTIONS
# ============================================

def ensure_error_table() -> bool:
    """Ensure the error_logs table exists."""
    try:
        conn = _get_connection()
        conn.executescript(ERROR_LOG_SCHEMA)
        conn.commit()
        conn.close()
        logger.info("Error_logs table verified/created")
        return True
    except Exception as e:
        logger.error(f"Failed to create error_logs table: {e}")
        return False


def generate_error_hash(error_data: Dict) -> str:
    """Generate a unique hash for error deduplication."""
    key = f"{error_data.get('error_type', '')}|{error_data.get('error_message', '')}|{error_data.get('url', '')}|{error_data.get('method', '')}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def store_error_log(error_data: Dict) -> Optional[int]:
    """
    Store an error in the error_logs table.
    Returns the error ID if successful.
    """
    try:
        # Ensure table exists
        ensure_error_table()
        
        # Generate error hash for deduplication
        error_hash = generate_error_hash(error_data)
        
        conn = _get_connection()
        
        # Check if this error already exists
        cursor = conn.execute(
            "SELECT id, occurrence_count FROM error_logs WHERE error_hash = ? AND resolved = 0 AND dismissed = 0",
            (error_hash,)
        )
        existing = cursor.fetchone()
        
        if existing:
            # Update existing error
            conn.execute(
                """
                UPDATE error_logs 
                SET occurrence_count = occurrence_count + 1,
                    last_seen = datetime('now', 'localtime'),
                    timestamp = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (existing['id'],)
            )
            conn.commit()
            conn.close()
            return existing['id']
        
        # Insert new error
        cursor = conn.execute(
            """
            INSERT INTO error_logs (
                request_id, timestamp, severity, status_code,
                url, method, user_id, ip_address,
                error_type, error_message, stack_trace, user_description,
                occurrence_count, first_seen, last_seen,
                resolved, dismissed, error_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                error_data.get('request_id', ''),
                datetime.now().isoformat(),
                error_data.get('severity', 'ERROR'),
                error_data.get('status_code'),
                error_data.get('url', '')[:500],
                error_data.get('method', ''),
                error_data.get('user_id'),
                error_data.get('ip_address', '')[:45],
                error_data.get('error_type', 'Unknown')[:200],
                error_data.get('error_message', '')[:1000],
                error_data.get('stack_trace', '')[:5000],
                error_data.get('user_description', '')[:1000],
                1,
                datetime.now().isoformat(),
                datetime.now().isoformat(),
                0,
                0,
                error_hash
            )
        )
        conn.commit()
        error_id = cursor.lastrowid
        conn.close()
        
        return error_id
        
    except Exception as e:
        logger.error(f"Failed to store error log: {e}")
        return None


def get_error_logs(
    limit: int = 50,
    offset: int = 0,
    severity: Optional[str] = None,
    resolved: Optional[int] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None
) -> List[Dict]:
    """Get error logs with optional filters."""
    try:
        ensure_error_table()
        conn = _get_connection()
        
        query = "SELECT * FROM error_logs WHERE 1=1"
        params = []
        
        if severity:
            query += " AND severity = ?"
            params.append(severity)
        
        if resolved is not None:
            query += " AND resolved = ?"
            params.append(resolved)
        
        if search:
            query += " AND (error_message LIKE ? OR error_type LIKE ? OR request_id LIKE ? OR url LIKE ?)"
            search_pattern = f"%{search}%"
            params.extend([search_pattern, search_pattern, search_pattern, search_pattern])
        
        if date_from:
            query += " AND timestamp >= ?"
            params.append(date_from)
        
        if date_to:
            query += " AND timestamp <= ?"
            params.append(date_to)
        
        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        cursor = conn.execute(query, params)
        results = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in results]
        
    except Exception as e:
        logger.error(f"Failed to get error logs: {e}")
        return []


def get_error_log_count(
    severity: Optional[str] = None,
    resolved: Optional[int] = None,
    search: Optional[str] = None
) -> int:
    """Get the total count of error logs matching filters."""
    try:
        ensure_error_table()
        conn = _get_connection()
        
        query = "SELECT COUNT(*) as count FROM error_logs WHERE 1=1"
        params = []
        
        if severity:
            query += " AND severity = ?"
            params.append(severity)
        
        if resolved is not None:
            query += " AND resolved = ?"
            params.append(resolved)
        
        if search:
            query += " AND (error_message LIKE ? OR error_type LIKE ? OR request_id LIKE ? OR url LIKE ?)"
            search_pattern = f"%{search}%"
            params.extend([search_pattern, search_pattern, search_pattern, search_pattern])
        
        cursor = conn.execute(query, params)
        result = cursor.fetchone()
        conn.close()
        
        return result['count'] if result else 0
        
    except Exception as e:
        logger.error(f"Failed to get error log count: {e}")
        return 0


def get_error_log_by_id(error_id: int) -> Optional[Dict]:
    """Get a single error log by ID."""
    try:
        ensure_error_table()
        conn = _get_connection()
        cursor = conn.execute("SELECT * FROM error_logs WHERE id = ?", (error_id,))
        result = cursor.fetchone()
        conn.close()
        return dict(result) if result else None
    except Exception as e:
        logger.error(f"Failed to get error log: {e}")
        return None


def get_error_log_by_request_id(request_id: str) -> Optional[Dict]:
    """Get an error log by request ID."""
    try:
        ensure_error_table()
        conn = _get_connection()
        cursor = conn.execute(
            "SELECT * FROM error_logs WHERE request_id = ? ORDER BY timestamp DESC LIMIT 1",
            (request_id,)
        )
        result = cursor.fetchone()
        conn.close()
        return dict(result) if result else None
    except Exception as e:
        logger.error(f"Failed to get error log by request ID: {e}")
        return None


def resolve_error_log(error_id: int, note: str = '') -> bool:
    """Mark an error as resolved."""
    try:
        ensure_error_table()
        conn = _get_connection()
        conn.execute(
            "UPDATE error_logs SET resolved = 1, resolution_note = ? WHERE id = ?",
            (note, error_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to resolve error log: {e}")
        return False


def dismiss_error_log(error_id: int) -> bool:
    """Dismiss an error from the dashboard."""
    try:
        ensure_error_table()
        conn = _get_connection()
        conn.execute(
            "UPDATE error_logs SET dismissed = 1 WHERE id = ?",
            (error_id,)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to dismiss error log: {e}")
        return False


def clear_resolved_errors() -> int:
    """Delete all resolved errors. Returns number deleted."""
    try:
        ensure_error_table()
        conn = _get_connection()
        cursor = conn.execute("DELETE FROM error_logs WHERE resolved = 1")
        count = conn.total_changes
        conn.commit()
        conn.close()
        return count
    except Exception as e:
        logger.error(f"Failed to clear resolved errors: {e}")
        return 0


def get_error_stats() -> Dict:
    """Get statistics about errors."""
    try:
        ensure_error_table()
        conn = _get_connection()
        
        cursor = conn.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN severity = 'CRITICAL' THEN 1 ELSE 0 END) as critical,
                SUM(CASE WHEN severity = 'ERROR' THEN 1 ELSE 0 END) as error,
                SUM(CASE WHEN severity = 'WARNING' THEN 1 ELSE 0 END) as warning,
                SUM(CASE WHEN resolved = 1 THEN 1 ELSE 0 END) as resolved,
                SUM(CASE WHEN resolved = 0 AND dismissed = 0 THEN 1 ELSE 0 END) as unresolved
            FROM error_logs
            WHERE dismissed = 0
        """)
        result = cursor.fetchone()
        conn.close()
        
        stats = {
            'total': 0,
            'critical': 0,
            'error': 0,
            'warning': 0,
            'resolved': 0,
            'unresolved': 0
        }
        
        if result:
            stats['total'] = result['total'] or 0
            stats['critical'] = result['critical'] or 0
            stats['error'] = result['error'] or 0
            stats['warning'] = result['warning'] or 0
            stats['resolved'] = result['resolved'] or 0
            stats['unresolved'] = result['unresolved'] or 0
        
        return stats
        
    except Exception as e:
        logger.error(f"Failed to get error stats: {e}")
        return stats


def clean_old_errors(days: int = Config.ERROR_RETENTION_DAYS) -> int:
    """Delete errors older than the retention period."""
    try:
        ensure_error_table()
        conn = _get_connection()
        cursor = conn.execute(
            "DELETE FROM error_logs WHERE timestamp < datetime('now', '-' || ? || ' days') AND resolved = 1",
            (days,)
        )
        count = conn.total_changes
        conn.commit()
        conn.close()
        return count
    except Exception as e:
        logger.error(f"Failed to clean old errors: {e}")
        return 0