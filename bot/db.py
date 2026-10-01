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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pdfs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            code              TEXT UNIQUE NOT NULL,
            title             TEXT NOT NULL,
            description       TEXT DEFAULT '',
            curriculum        TEXT DEFAULT 'PL' CHECK (curriculum IN ('PL', 'SO', 'SL')),
            class             TEXT DEFAULT 'F4' CHECK (class IN ('F4', 'F3', 'G8', 'G7')),
            subject           TEXT,
            chapter           TEXT DEFAULT '',
            tags              TEXT DEFAULT '',
            is_premium        INTEGER DEFAULT 0,
            file_id           TEXT,
            file_unique_id    TEXT UNIQUE,
            file_size         INTEGER DEFAULT NULL,
            original_filename TEXT,
            uploaded_by       TEXT NOT NULL DEFAULT 'NUUN',
            uploaded_at       TEXT DEFAULT (datetime('now', 'localtime')),
            published         INTEGER DEFAULT 0,
            published_at      TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_pdfs_code ON pdfs(code)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_pdfs_file_unique_id ON pdfs(file_unique_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_pdfs_subject ON pdfs(subject)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_pdfs_published ON pdfs(published)")

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

        if 'file_size' not in existing_cols:
            cursor.execute("ALTER TABLE pdfs ADD COLUMN file_size INTEGER DEFAULT NULL")
            logger.info("Added file_size column to bot pdfs table")
    except Exception as e:
        logger.warning(f"Could not run pdfs column migrations: {e}")

    conn.commit()
    conn.close()

    init_join_gate_table()
    init_bot_contacts_table()

    logger.info("Bot database initialized (pending_pdfs, pdfs, join_gate, bot_contacts)")


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
    if published_filter is None:
        return "", []
    if published_filter is True:
        return " AND COALESCE(published, 0) = 1", []
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
    return is_pending_duplicate(file_unique_id) or is_bot_duplicate(file_unique_id)


# ============================================================
# FILE SIZE + BATCH LOOKUP
# ============================================================

def list_bot_pdfs_missing_size(limit=50):
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, code, file_id, file_size
            FROM pdfs
            WHERE file_size IS NULL
              AND file_id IS NOT NULL AND file_id != ''
            ORDER BY id ASC
            LIMIT ?
        """, (limit,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def update_bot_pdf_file_size(pdf_id, file_size):
    try:
        conn = _get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE pdfs SET file_size = ? WHERE id = ?",
                    (int(file_size), int(pdf_id)))
        conn.commit()
        n = cur.rowcount
        conn.close()
        return n > 0
    except Exception as e:
        logger.warning(f"update_bot_pdf_file_size failed for #{pdf_id}: {e}")
        return False


def get_bot_pdfs_by_codes(codes):
    if not codes:
        return {}
    result = {}
    try:
        conn = _get_connection()
        cur = conn.cursor()
        code_list = list({str(c).strip() for c in codes if c})
        chunk = 500
        for i in range(0, len(code_list), chunk):
            batch = code_list[i:i + chunk]
            ph = ','.join('?' * len(batch))
            cur.execute(f"SELECT * FROM pdfs WHERE code IN ({ph})", batch)
            for row in cur.fetchall():
                result[row['code']] = dict(row)
        conn.close()
    except Exception as e:
        logger.warning(f"get_bot_pdfs_by_codes failed: {e}")
    return result


# ============================================================
# JOIN GATE
# ============================================================

def init_join_gate_table():
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS join_gate (
                user_id     INTEGER PRIMARY KEY,
                stage       TEXT NOT NULL CHECK (stage IN ('channel', 'group')),
                code        TEXT,
                attempts    INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at  TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_join_gate_updated_at "
            "ON join_gate(updated_at DESC)"
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"init_join_gate_table failed: {e}")


def get_join_gate(user_id):
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, stage, code, attempts, created_at, updated_at "
            "FROM join_gate WHERE user_id = ?",
            (int(user_id),),
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.warning(f"get_join_gate failed for user {user_id}: {e}")
        return None


def set_join_gate(user_id, stage, code):
    if stage not in ('channel', 'group'):
        logger.warning(f"set_join_gate: invalid stage {stage!r}")
        return False
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO join_gate (user_id, stage, code, attempts, created_at, updated_at)
            VALUES (?, ?, ?, 0, datetime('now','localtime'), datetime('now','localtime'))
            ON CONFLICT(user_id) DO UPDATE SET
                stage = excluded.stage,
                code = excluded.code,
                updated_at = datetime('now','localtime')
        """, (int(user_id), stage, code))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"set_join_gate failed for user {user_id}: {e}")
        return False


def delete_join_gate(user_id):
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM join_gate WHERE user_id = ?", (int(user_id),))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"delete_join_gate failed for user {user_id}: {e}")
        return False


def bump_join_gate_attempts(user_id):
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE join_gate SET attempts = attempts + 1, "
            "updated_at = datetime('now','localtime') WHERE user_id = ?",
            (int(user_id),),
        )
        affected = cursor.rowcount
        if affected == 0:
            conn.close()
            return None
        cursor.execute(
            "SELECT attempts FROM join_gate WHERE user_id = ?",
            (int(user_id),),
        )
        row = cursor.fetchone()
        conn.commit()
        conn.close()
        return row['attempts'] if row else None
    except Exception as e:
        logger.warning(f"bump_join_gate_attempts failed for user {user_id}: {e}")
        return None


def clean_stale_join_gates(hours=24):
    try:
        hours = max(1, int(hours))
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM join_gate "
            "WHERE updated_at < datetime('now', 'localtime', ?)",
            (f'-{hours} hours',),
        )
        n = cursor.rowcount or 0
        conn.commit()
        conn.close()
        return n
    except Exception as e:
        logger.warning(f"clean_stale_join_gates failed: {e}")
        return 0


# ============================================================
# BOT CONTACTS (broadcast audience)
# ============================================================
# Every user who has ever talked to the bot is recorded here.
# Eligibility for the new-quiz broadcast is either:
#   · fetched_pdf = 1        — has fetched a PDF via the bot, or
#   · subscribed_broadcast = 1 — opted in via the lobby banner
#                                 deeplink (`?start=subscribe_<id>`)
#                                 or the /subscribe command.
# A blocked chat is never eligible.
#
# platform_user_id links a chat back to the platform user via their
# public_id. It is set when the user subscribes through the lobby
# banner (which carries the public_id in the deeplink payload). It
# lets the web settings toggle reach the same chat.
# ============================================================

def init_bot_contacts_table():
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_contacts (
                chat_id       INTEGER PRIMARY KEY,
                username      TEXT DEFAULT '',
                first_name    TEXT DEFAULT '',
                last_name     TEXT DEFAULT '',
                first_seen_at TEXT DEFAULT (datetime('now','localtime')),
                last_seen_at  TEXT DEFAULT (datetime('now','localtime')),
                fetched_pdf   INTEGER NOT NULL DEFAULT 0,
                blocked       INTEGER NOT NULL DEFAULT 0
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_contacts_fetched "
            "ON bot_contacts(fetched_pdf, blocked)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_contacts_last_seen "
            "ON bot_contacts(last_seen_at DESC)"
        )

        # Idempotent column additions for the broadcast-subscription
        # feature. Same PRAGMA pattern used for the pdfs table above.
        cursor.execute("PRAGMA table_info(bot_contacts)")
        existing_cols = {row[1] for row in cursor.fetchall()}

        if 'subscribed_broadcast' not in existing_cols:
            cursor.execute(
                "ALTER TABLE bot_contacts "
                "ADD COLUMN subscribed_broadcast INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Added subscribed_broadcast column to bot_contacts")

        if 'platform_user_id' not in existing_cols:
            cursor.execute(
                "ALTER TABLE bot_contacts "
                "ADD COLUMN platform_user_id TEXT DEFAULT NULL"
            )
            logger.info("Added platform_user_id column to bot_contacts")

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_contacts_platform_user "
            "ON bot_contacts(platform_user_id)"
        )

        conn.commit()
        conn.close()
        logger.info("bot_contacts table ready")
    except Exception as e:
        logger.error(f"init_bot_contacts_table failed: {e}")


def upsert_bot_contact(chat_id, username='', first_name='', last_name=''):
    """
    Insert or update a bot contact on every incoming message.
    Never raises.

    On conflict, the name fields and last_seen_at are refreshed.
    Neither subscribed_broadcast nor platform_user_id is touched —
    those are managed by mark_bot_contact_subscribed and the
    settings sync path, not by ordinary updates.
    """
    if not chat_id:
        return False
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return False
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO bot_contacts
                (chat_id, username, first_name, last_name,
                 first_seen_at, last_seen_at, fetched_pdf, blocked)
            VALUES (?, ?, ?, ?, datetime('now','localtime'),
                    datetime('now','localtime'), 0, 0)
            ON CONFLICT(chat_id) DO UPDATE SET
                username     = excluded.username,
                first_name   = excluded.first_name,
                last_name    = excluded.last_name,
                last_seen_at = datetime('now','localtime')
        """, (
            cid,
            (username or '')[:64],
            (first_name or '')[:64],
            (last_name or '')[:64],
        ))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"upsert_bot_contact failed for chat {chat_id}: {e}")
        return False


def mark_bot_contact_fetched_pdf(chat_id):
    """
    Set fetched_pdf = 1 for a chat. Called after a successful
    PDF delivery via the bot.
    """
    if not chat_id:
        return False
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return False
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE bot_contacts
            SET fetched_pdf = 1,
                last_seen_at = datetime('now','localtime')
            WHERE chat_id = ?
        """, (cid,))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0
    except Exception as e:
        logger.warning(f"mark_bot_contact_fetched_pdf failed for {chat_id}: {e}")
        return False


def mark_bot_contact_subscribed(chat_id, public_id=None):
    """
    Set subscribed_broadcast = 1 for a chat and, when provided,
    link it to a platform user via public_id. Called when a user
    taps the lobby banner deeplink or sends /subscribe.
    """
    if not chat_id:
        return False
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return False
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        if public_id:
            cursor.execute("""
                UPDATE bot_contacts
                SET subscribed_broadcast = 1,
                    platform_user_id = ?,
                    last_seen_at = datetime('now','localtime')
                WHERE chat_id = ?
            """, (str(public_id)[:64], cid))
        else:
            cursor.execute("""
                UPDATE bot_contacts
                SET subscribed_broadcast = 1,
                    last_seen_at = datetime('now','localtime')
                WHERE chat_id = ?
            """, (cid,))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0
    except Exception as e:
        logger.warning(f"mark_bot_contact_subscribed failed for {chat_id}: {e}")
        return False


def mark_bot_contact_unsubscribed(chat_id):
    """
    Set subscribed_broadcast = 0 for a chat. fetched_pdf is
    untouched — a user who has fetched a PDF stays eligible for
    broadcasts until they explicitly block or the admin removes them.
    """
    if not chat_id:
        return False
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return False
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE bot_contacts
            SET subscribed_broadcast = 0,
                last_seen_at = datetime('now','localtime')
            WHERE chat_id = ?
        """, (cid,))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0
    except Exception as e:
        logger.warning(f"mark_bot_contact_unsubscribed failed for {chat_id}: {e}")
        return False


def get_bot_contact(chat_id):
    """Return the bot_contacts row for a chat, or None."""
    if not chat_id:
        return None
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return None
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bot_contacts WHERE chat_id = ?", (cid,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.warning(f"get_bot_contact failed for {chat_id}: {e}")
        return None


def set_broadcast_preference_by_public_id(public_id, enabled):
    """
    Sync a web-side settings toggle to the bot-side contact row.
    Called from services/settings_service.py when the user flips
    `notifications.telegram_broadcast`.

    Returns True if a matching row was updated, False otherwise
    (including the common case where the user has no Telegram link
    yet — the preference is still saved server-side and applied the
    next time the user subscribes via the bot).
    """
    if not public_id:
        return False
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE bot_contacts
            SET subscribed_broadcast = ?,
                last_seen_at = datetime('now','localtime')
            WHERE platform_user_id = ?
        """, (1 if enabled else 0, str(public_id)[:64]))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0
    except Exception as e:
        logger.warning(
            f"set_broadcast_preference_by_public_id failed "
            f"for public_id={public_id!r}: {e}"
        )
        return False


def mark_bot_contact_blocked(chat_id):
    """
    Set blocked = 1 for a chat that returned HTTP 403 on delivery.
    Prunes it from future broadcasts.
    """
    if not chat_id:
        return False
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return False
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bot_contacts SET blocked = 1 WHERE chat_id = ?",
            (cid,),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"mark_bot_contact_blocked failed for {chat_id}: {e}")
        return False


def get_broadcast_recipients():
    """
    Return the list of chat_ids that should receive a new-quiz
    broadcast: users who have fetched at least one PDF OR explicitly
    subscribed to the broadcast, and are not blocked.
    """
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT chat_id
            FROM bot_contacts
            WHERE blocked = 0
              AND (fetched_pdf = 1 OR subscribed_broadcast = 1)
            ORDER BY last_seen_at DESC
        """)
        rows = cursor.fetchall()
        conn.close()
        return [int(r['chat_id']) for r in rows]
    except Exception as e:
        logger.warning(f"get_broadcast_recipients failed: {e}")
        return []


def count_broadcast_recipients():
    """Return the number of eligible broadcast recipients."""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS n FROM bot_contacts "
            "WHERE blocked = 0 "
            "AND (fetched_pdf = 1 OR subscribed_broadcast = 1)"
        )
        row = cursor.fetchone()
        conn.close()
        return int(row['n']) if row else 0
    except Exception:
        return 0