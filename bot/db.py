# bot/db.py – two-database PDF system with published-flag safety layer

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

    # ---- Pending PDFs (intake) ----
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
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_pending_pdfs_uploaded_at "
        "ON pending_pdfs(uploaded_at DESC)"
    )

    # ---- Fulfilled Bot PDFs (staging) ----
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
            uploaded_at TEXT DEFAULT (datetime('now', 'localtime')),
            original_filename TEXT DEFAULT '',
            published INTEGER NOT NULL DEFAULT 0,
            published_at TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_pdfs_code ON pdfs(code)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_pdfs_file_unique_id ON pdfs(file_unique_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_pdfs_subject ON pdfs(subject)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_pdfs_published ON pdfs(published)")

    # ---- Idempotent migrations for existing installs ----
    try:
        cursor.execute("PRAGMA table_info(pdfs)")
        existing_cols = {row[1] for row in cursor.fetchall()}

        if 'original_filename' not in existing_cols:
            cursor.execute("ALTER TABLE pdfs ADD COLUMN original_filename TEXT DEFAULT ''")
            logger.info("Added original_filename column to bot pdfs table")

        if 'published' not in existing_cols:
            cursor.execute(
                "ALTER TABLE pdfs ADD COLUMN published INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Added published column to bot pdfs table")

        if 'published_at' not in existing_cols:
            cursor.execute("ALTER TABLE pdfs ADD COLUMN published_at TEXT")
            logger.info("Added published_at column to bot pdfs table")
    except Exception as e:
        logger.warning(f"Could not run pdfs column migrations: {e}")

    conn.commit()
    conn.close()
    logger.info("Bot database initialized with pending_pdfs and pdfs tables")


# ============================================================
# Pending PDFs
# ============================================================

def insert_pending_pdf(file_id, file_unique_id, filename, uploaded_by):
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO pending_pdfs "
            "(file_id, file_unique_id, filename, uploaded_by) "
            "VALUES (?, ?, ?, ?)",
            (file_id, file_unique_id, filename, uploaded_by),
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


def get_pending_pdf_list(limit=100, offset=0, search=''):
    """Return pending PDFs. Optional filename search."""
    conn = _get_connection()
    cursor = conn.cursor()
    if search:
        like = "%" + search + "%"
        cursor.execute("""
            SELECT * FROM pending_pdfs
            WHERE filename LIKE ?
            ORDER BY uploaded_at DESC
            LIMIT ? OFFSET ?
        """, (like, limit, offset))
    else:
        cursor.execute("""
            SELECT * FROM pending_pdfs
            ORDER BY uploaded_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def count_pending_pdfs(search=''):
    """Total pending count. If search is set, counts matches only."""
    conn = _get_connection()
    cursor = conn.cursor()
    if search:
        like = "%" + search + "%"
        cursor.execute(
            "SELECT COUNT(*) as count FROM pending_pdfs WHERE filename LIKE ?",
            (like,),
        )
    else:
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
    cursor.execute(
        "SELECT id FROM pending_pdfs WHERE file_unique_id = ?",
        (file_unique_id,),
    )
    result = cursor.fetchone() is not None
    conn.close()
    return result


# ============================================================
# Bot PDFs (fulfilled / staged)
# ============================================================

def insert_bot_pdf(data):
    """
    data keys: code, title, description, curriculum, class, subject,
               chapter, tags, is_premium, file_id, file_unique_id,
               uploaded_by, original_filename
    """
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO pdfs (
                code, title, description, curriculum, class, subject,
                chapter, tags, is_premium, file_id, file_unique_id,
                uploaded_by, original_filename, published
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            data['code'],
            data['title'],
            data.get('description', ''),
            data.get('curriculum', 'PL'),
            data.get('class', ''),
            data['subject'],
            data.get('chapter', ''),
            data.get('tags', ''),
            data.get('is_premium', 0),
            data['file_id'],
            data['file_unique_id'],
            data.get('uploaded_by'),
            data.get('original_filename', ''),
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


def _build_published_clause(published_filter):
    """
    published_filter:
        None  → no filter (return all rows)
        False → only unpublished (published = 0 or NULL)
        True  → only published   (published = 1)
    """
    if published_filter is None:
        return "", []
    if published_filter is True:
        return " AND COALESCE(published, 0) = 1", []
    # False
    return " AND COALESCE(published, 0) = 0", []


def get_bot_pdfs(limit=100, offset=0, search='', subject='',
                 curriculum='', class_filter='', published_filter=None):
    conn = _get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM pdfs WHERE 1=1"
    params = []
    if search:
        query += (" AND (title LIKE ? OR description LIKE ? "
                  "OR code LIKE ? OR subject LIKE ?)")
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

    pub_clause, pub_params = _build_published_clause(published_filter)
    query += pub_clause
    params.extend(pub_params)

    query += " ORDER BY uploaded_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def count_bot_pdfs(search='', subject='', curriculum='', class_filter='',
                   published_filter=None):
    conn = _get_connection()
    cursor = conn.cursor()
    query = "SELECT COUNT(*) as count FROM pdfs WHERE 1=1"
    params = []
    if search:
        query += (" AND (title LIKE ? OR description LIKE ? "
                  "OR code LIKE ? OR subject LIKE ?)")
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

    pub_clause, pub_params = _build_published_clause(published_filter)
    query += pub_clause
    params.extend(pub_params)

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
        allowed = ['title', 'description', 'curriculum', 'class',
                   'subject', 'chapter', 'tags', 'is_premium']
        for key in allowed:
            if key in data:
                fields.append(f"{key} = ?")
                params.append(data[key])
        if not fields:
            return False
        params.append(pdf_id)
        cursor.execute(
            f"UPDATE pdfs SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to update bot PDF: {e}")
        return False


def mark_bot_pdf_published(pdf_id, published=True):
    """
    Flag a staging row as published (or revert). Does NOT delete.
    The row stays in bot_data.db as a permanent backup of the file_id.
    """
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        if published:
            cursor.execute(
                "UPDATE pdfs SET published = 1, "
                "published_at = datetime('now', 'localtime') WHERE id = ?",
                (pdf_id,),
            )
        else:
            cursor.execute(
                "UPDATE pdfs SET published = 0, published_at = NULL WHERE id = ?",
                (pdf_id,),
            )
        conn.commit()
        affected = cursor.rowcount
        conn.close()
        return affected > 0
    except Exception as e:
        logger.error(f"Failed to mark bot PDF {pdf_id} published={published}: {e}")
        return False


def delete_bot_pdf(pdf_id):
    """
    Hard-delete a staging row. Used by the admin panel's manual delete.
    Normal publish flow does NOT call this — it marks as published instead.
    """
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
    cursor.execute(
        "SELECT id FROM pdfs WHERE file_unique_id = ?",
        (file_unique_id,),
    )
    result = cursor.fetchone() is not None
    conn.close()
    return result


def is_duplicate_in_bot(file_unique_id):
    """Check both pending and bot pdfs."""
    return is_pending_duplicate(file_unique_id) or is_bot_duplicate(file_unique_id)