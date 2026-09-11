# bot/db.py – redesigned for two-database PDF system

import os
import sqlite3
import logging
from config import Config

logger = logging.getLogger(__name__)

BOT_DB_PATH = Config.BOT_DATABASE_PATH

def _get_connection():
    db_dir = os.path.dirname(BOT_DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(BOT_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn

def init_bot_db():
    conn = _get_connection()
    cursor = conn.cursor()

    # Pending PDFs (intake)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_pdfs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT NOT NULL,
            file_unique_id TEXT UNIQUE NOT NULL,
            filename TEXT,
            uploaded_by INTEGER,
            uploaded_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pending_pdfs_uploaded_at ON pending_pdfs(uploaded_at DESC)")

    # Fulfilled Bot PDFs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pdfs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            curriculum TEXT DEFAULT 'PL' CHECK (curriculum IN ('PL', 'SO', 'SL')),
            class TEXT DEFAULT '' CHECK (class IN ('', '7aad', '8aad', 'F3', 'F4')),
            subject TEXT NOT NULL,
            chapter TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            is_premium INTEGER DEFAULT 0,
            file_id TEXT NOT NULL,
            file_unique_id TEXT UNIQUE NOT NULL,
            uploaded_by INTEGER,
            uploaded_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_pdfs_code ON pdfs(code)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_pdfs_file_unique_id ON pdfs(file_unique_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_pdfs_subject ON pdfs(subject)")

    conn.commit()
    conn.close()
    logger.info("Bot database initialized with pending_pdfs and pdfs tables")

# ---- Pending PDF functions ----

def insert_pending_pdf(file_id, file_unique_id, filename, uploaded_by):
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO pending_pdfs (file_id, file_unique_id, filename, uploaded_by) VALUES (?, ?, ?, ?)",
            (file_id, file_unique_id, filename, uploaded_by)
        )
        conn.commit()
        pdf_id = cursor.lastrowid
        conn.close()
        return pdf_id
    except Exception as e:
        logger.error(f"Failed to save pending PDF: {e}")
        return 0

def get_pending_pdf_by_id(pending_id):
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM pending_pdfs WHERE id = ?", (pending_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_pending_pdf_list(limit=50, offset=0):
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM pending_pdfs
        ORDER BY uploaded_at DESC
        LIMIT ? OFFSET ?
    """, (limit, offset))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def count_pending_pdfs():
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as count FROM pending_pdfs")
    row = cursor.fetchone()
    conn.close()
    return row['count'] if row else 0

def delete_pending_pdf(pending_id):
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM pending_pdfs WHERE id = ?", (pending_id,))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

def is_pending_duplicate(file_unique_id):
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM pending_pdfs WHERE file_unique_id = ?", (file_unique_id,))
    result = cursor.fetchone() is not None
    conn.close()
    return result

# ---- Bot PDFs (fulfilled) functions ----

def insert_bot_pdf(data):
    """
    data keys: code, title, description, curriculum, class, subject, chapter,
               tags, is_premium, file_id, file_unique_id, uploaded_by
    """
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO pdfs (
                code, title, description, curriculum, class, subject,
                chapter, tags, is_premium, file_id, file_unique_id, uploaded_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data['code'], data['title'], data.get('description', ''),
            data.get('curriculum', 'PL'), data.get('class', ''),
            data['subject'], data.get('chapter', ''), data.get('tags', ''),
            data.get('is_premium', 0), data['file_id'],
            data['file_unique_id'], data.get('uploaded_by')
        ))
        conn.commit()
        pdf_id = cursor.lastrowid
        conn.close()
        return pdf_id
    except Exception as e:
        logger.error(f"Failed to insert bot PDF: {e}")
        return 0

def get_bot_pdf_by_code(code):
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM pdfs WHERE code = ?", (code,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_bot_pdf_by_id(pdf_id):
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM pdfs WHERE id = ?", (pdf_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_bot_pdfs(limit=100, offset=0, search='', subject='', curriculum='', class_filter=''):
    conn = _get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM pdfs WHERE 1=1"
    params = []
    if search:
        query += " AND (title LIKE ? OR description LIKE ? OR code LIKE ? OR subject LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like, like, like])
    if subject:
        query += " AND subject = ?"
        params.append(subject)
    if curriculum:
        query += " AND curriculum = ?"
        params.append(curriculum)
    if class_filter:
        query += " AND class = ?"
        params.append(class_filter)
    query += " ORDER BY uploaded_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def count_bot_pdfs(search='', subject='', curriculum='', class_filter=''):
    conn = _get_connection()
    cursor = conn.cursor()
    query = "SELECT COUNT(*) as count FROM pdfs WHERE 1=1"
    params = []
    if search:
        query += " AND (title LIKE ? OR description LIKE ? OR code LIKE ? OR subject LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like, like, like])
    if subject:
        query += " AND subject = ?"
        params.append(subject)
    if curriculum:
        query += " AND curriculum = ?"
        params.append(curriculum)
    if class_filter:
        query += " AND class = ?"
        params.append(class_filter)
    cursor.execute(query, params)
    row = cursor.fetchone()
    conn.close()
    return row['count'] if row else 0

def update_bot_pdf(pdf_id, data):
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        fields = []
        params = []
        allowed = ['title', 'description', 'curriculum', 'class', 'subject', 'chapter', 'tags', 'is_premium']
        for key in allowed:
            if key in data:
                fields.append(f"{key} = ?")
                params.append(data[key])
        if not fields:
            return False
        params.append(pdf_id)
        cursor.execute(f"UPDATE pdfs SET {', '.join(fields)} WHERE id = ?", params)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to update bot PDF: {e}")
        return False

def delete_bot_pdf(pdf_id):
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM pdfs WHERE id = ?", (pdf_id,))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

def is_bot_duplicate(file_unique_id):
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM pdfs WHERE file_unique_id = ?", (file_unique_id,))
    result = cursor.fetchone() is not None
    conn.close()
    return result

def is_duplicate_in_bot(file_unique_id):
    """Check both pending and bot pdfs."""
    return is_pending_duplicate(file_unique_id) or is_bot_duplicate(file_unique_id)