# ============================================
# DATABASE VERIFICATION & MANAGEMENT
# ============================================

import os
import sqlite3
import time
import logging
import fcntl
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from config import Config

logger = logging.getLogger(__name__)

# ============================================
# CONSTANTS
# ============================================

REQUIRED_TABLES = [
    'students',
    'questions',
    'quiz_attempts',
    'groups',
    'pdfs',
    'live_quizzes',
    'live_quiz_participants',
    'deleted_users',
    'quiz_ratings',
    'notifications',
    'notification_preferences',
    'user_usage',
    'saved_content',
    'achievements',
    'user_achievements',
    'question_interactions',
]

REQUIRED_COLUMNS = {
    'students': ['id', 'public_id', 'phone_number', 'password', 'first_name', 'last_name', 'is_admin', 'created_at', 'tier', 'tier_updated_at'],
    'questions': ['id', 'subject_code', 'question_text', 'options', 'correct_answer', 'difficulty', 'status', 'created_at'],
    'quiz_attempts': ['id', 'student_id', 'subject_code', 'score', 'total_questions', 'answers', 'ratings', 'completed_at'],
    'groups': ['id', 'name', 'platform', 'invite_link', 'is_active', 'created_at'],
    'pdfs': ['id', 'code', 'title', 'file_url', 'view_count', 'uploaded_at', 'is_premium'],
    'live_quizzes': ['id', 'creator_id', 'join_code', 'status', 'question_count', 'created_at'],
    'live_quiz_participants': ['id', 'quiz_id', 'student_id', 'score', 'answers', 'ratings', 'ranking'],
    'deleted_users': ['id', 'original_id', 'first_name', 'last_name', 'phone_number', 'data', 'deleted_at'],
    'quiz_ratings': ['id', 'student_id', 'question_id', 'rating', 'created_at'],
    'notifications': ['id', 'user_id', 'type', 'title', 'body', 'is_read', 'created_at'],
    'notification_preferences': ['id', 'user_id', 'notification_type', 'enabled', 'created_at'],
    'user_usage': ['id', 'user_id', 'metric_code', 'period_start', 'usage_count', 'updated_at'],
    'saved_content': ['id', 'user_id', 'content_type', 'content_id', 'saved_at'],
    'achievements': ['id', 'name', 'description', 'icon', 'tier_required', 'unlock_condition', 'created_at'],
    'user_achievements': ['id', 'user_id', 'achievement_id', 'unlocked_at'],
    'question_interactions': [
        'id', 'user_id', 'question_id', 'interaction_type',
        'report_reason', 'report_comment', 'report_status',
        'admin_reply', 'resolved_by', 'resolved_at', 'created_at'
    ],
}

DB_INIT_LOCK_FILE = os.path.join(os.path.dirname(Config.DATABASE_PATH), '.db_init_lock')

# Sentinel returned when locking is unavailable but the caller may proceed.
_NO_LOCK = object()

# ============================================
# DATABASE LOCK FOR STARTUP (best-effort)
# ============================================

def acquire_db_init_lock(timeout: int = 1) -> Any:
    """
    Best-effort exclusive lock for database initialization.
    Never returns None.

    Returns:
        - The open lock file object on success.
        - The sentinel `_NO_LOCK` if locking is unavailable or timed out.
          In that case the caller proceeds without locking.
    """
    lock_dir = os.path.dirname(DB_INIT_LOCK_FILE)
    if lock_dir and not os.path.exists(lock_dir):
        try:
            os.makedirs(lock_dir, exist_ok=True)
        except Exception as e:
            logger.warning(f"Could not create lock directory ({e}); proceeding without lock.")
            return _NO_LOCK

    try:
        fd = open(DB_INIT_LOCK_FILE, 'w')
    except Exception as e:
        logger.warning(f"Could not open lock file ({e}); proceeding without lock.")
        return _NO_LOCK

    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                fd.write(str(os.getpid()))
                fd.flush()
            except Exception:
                pass
            return fd   # file object, not fileno()
        except (IOError, OSError) as e:
            errno = getattr(e, 'errno', None)
            # EAGAIN (11): another process holds the lock -> retry
            if errno == 11:
                time.sleep(0.2)
                continue
            # ENOLCK (37), ENOTSUP/EOPNOTSUPP (95), EINVAL (22):
            # filesystem does not support flock (common on Android FUSE)
            if errno in (37, 95, 22):
                logger.warning(
                    f"flock not supported on this filesystem ({e}); "
                    f"proceeding without an init lock."
                )
                try:
                    fd.close()
                except Exception:
                    pass
                return _NO_LOCK
            # Any other error: retry briefly
            time.sleep(0.2)

    # Timed out while another process held the lock.
    logger.warning(
        f"Lock acquire timed out after {timeout}s; "
        f"proceeding without an init lock."
    )
    try:
        fd.close()
    except Exception:
        pass
    return _NO_LOCK


def release_db_init_lock(fd) -> None:
    """Release the database initialization lock. No-op for the sentinel."""
    if fd is None or fd is _NO_LOCK:
        return
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        logger.warning(f"Failed to release DB init lock: {e}")
    finally:
        try:
            fd.close()
        except Exception:
            pass


# ============================================
# CONNECTION HELPER
# ============================================

def _get_connection(db_path: str = None, timeout: int = 10):
    if db_path is None:
        db_path = Config.DATABASE_PATH

    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {Config.DB_BUSY_TIMEOUT}")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


# ============================================
# VERIFICATION HELPERS
# ============================================

def get_schema_version(conn: sqlite3.Connection) -> Optional[str]:
    try:
        cursor = conn.execute("PRAGMA user_version")
        result = cursor.fetchone()
        return str(result[0]) if result else None
    except Exception:
        return None


def verify_database_exists() -> bool:
    return os.path.exists(Config.DATABASE_PATH)


def verify_database_openable() -> Tuple[bool, Optional[str]]:
    try:
        conn = _get_connection(timeout=5)
        conn.close()
        return True, None
    except Exception as e:
        return False, str(e)


def verify_database_integrity() -> Tuple[bool, Optional[str]]:
    try:
        conn = _get_connection(timeout=10)
        cursor = conn.execute("PRAGMA integrity_check")
        result = cursor.fetchone()
        conn.close()
        if result and result[0] == 'ok':
            return True, None
        return False, result[0] if result else "unknown integrity error"
    except Exception as e:
        return False, str(e)


def verify_wal_enabled() -> Tuple[bool, Optional[str]]:
    try:
        conn = _get_connection(timeout=5)
        cursor = conn.execute("PRAGMA journal_mode")
        result = cursor.fetchone()
        conn.close()
        if result and result[0].upper() == 'WAL':
            return True, None
        return False, f"WAL not enabled: {result[0] if result else 'unknown'}"
    except Exception as e:
        return False, str(e)


def verify_database_writable() -> Tuple[bool, Optional[str]]:
    try:
        conn = _get_connection(timeout=5)
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS _write_test (id INTEGER)")
        conn.execute("INSERT INTO _write_test (id) VALUES (1)")
        conn.execute("DELETE FROM _write_test WHERE id = 1")
        conn.execute("DROP TABLE _write_test")
        conn.commit()
        conn.close()
        return True, None
    except Exception as e:
        return False, str(e)


def verify_tables_exist(conn: sqlite3.Connection) -> Tuple[bool, List[str]]:
    try:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}
        missing_tables = [t for t in REQUIRED_TABLES if t not in existing_tables]
        return len(missing_tables) == 0, missing_tables
    except Exception as e:
        return False, [f"Error checking tables: {e}"]


def verify_columns_exist(conn: sqlite3.Connection) -> Tuple[bool, Dict[str, List[str]]]:
    try:
        missing_columns = {}
        for table, required_cols in REQUIRED_COLUMNS.items():
            cursor = conn.execute(f"PRAGMA table_info({table})")
            existing_cols = {row[1] for row in cursor.fetchall()}
            missing = [c for c in required_cols if c not in existing_cols]
            if missing:
                missing_columns[table] = missing
        return len(missing_columns) == 0, missing_columns
    except Exception as e:
        return False, {"error": [f"Error checking columns: {e}"]}


# ============================================
# QUESTION INTERACTIONS TABLE
# ============================================

def ensure_question_interactions_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS question_interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            quiz_attempt_id INTEGER,
            live_quiz_id INTEGER,
            interaction_type TEXT NOT NULL CHECK (interaction_type IN ('like', 'save', 'report')),
            report_reason TEXT,
            report_comment TEXT,
            report_status TEXT DEFAULT 'pending' CHECK (report_status IN ('pending', 'resolved', 'dismissed')),
            admin_reply TEXT,
            resolved_by INTEGER,
            resolved_at TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (user_id) REFERENCES students(id) ON DELETE CASCADE,
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
            FOREIGN KEY (quiz_attempt_id) REFERENCES quiz_attempts(id) ON DELETE SET NULL,
            FOREIGN KEY (live_quiz_id) REFERENCES live_quizzes(id) ON DELETE SET NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_question_interactions_user ON question_interactions(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_question_interactions_question ON question_interactions(question_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_question_interactions_type ON question_interactions(interaction_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_question_interactions_report_status ON question_interactions(report_status)")
    conn.commit()


# ============================================
# SCHEMA CREATION (Fresh Install)
# ============================================

def create_database_schema() -> Tuple[bool, Optional[str]]:
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    if not os.path.exists(schema_path):
        return False, f"Schema file not found: {schema_path}"

    try:
        db_dir = os.path.dirname(Config.DATABASE_PATH)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

        conn = _get_connection()
        conn.execute("PRAGMA foreign_keys = ON")

        with open(schema_path, 'r') as f:
            schema = f.read()

        conn.executescript(schema)
        ensure_question_interactions_table(conn)
        conn.commit()
        conn.close()
        return True, None
    except Exception as e:
        return False, str(e)


def enable_wal_mode() -> Tuple[bool, Optional[str]]:
    try:
        conn = _get_connection()
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA wal_autocheckpoint = 1000")
        conn.execute(f"PRAGMA busy_timeout = {Config.DB_BUSY_TIMEOUT}")
        conn.commit()
        conn.close()
        return True, None
    except Exception as e:
        return False, str(e)


# ============================================
# FULL VERIFICATION
# ============================================

def verify_database_full() -> Dict[str, Any]:
    results = {
        'exists': False,
        'openable': False,
        'writable': False,
        'integrity': False,
        'wal_enabled': False,
        'tables': {'ok': False, 'missing': []},
        'columns': {'ok': False, 'missing': {}},
        'schema_version': None,
        'errors': []
    }

    results['exists'] = verify_database_exists()
    if not results['exists']:
        results['errors'].append("Database file does not exist")
        return results

    openable, error = verify_database_openable()
    results['openable'] = openable
    if not openable:
        results['errors'].append(f"Cannot open database: {error}")
        return results

    writable, error = verify_database_writable()
    results['writable'] = writable
    if not writable:
        results['errors'].append(f"Database not writable: {error}")
        return results

    try:
        conn = _get_connection()
        ensure_question_interactions_table(conn)
        results['schema_version'] = get_schema_version(conn)

        tables_ok, missing_tables = verify_tables_exist(conn)
        results['tables']['ok'] = tables_ok
        results['tables']['missing'] = missing_tables
        if not tables_ok:
            results['errors'].append(f"Missing tables: {', '.join(missing_tables)}")

        columns_ok, missing_columns = verify_columns_exist(conn)
        results['columns']['ok'] = columns_ok
        results['columns']['missing'] = missing_columns
        if not columns_ok:
            for table, cols in missing_columns.items():
                results['errors'].append(f"Missing columns in {table}: {', '.join(cols)}")

        conn.close()
    except Exception as e:
        results['errors'].append(f"Error during table verification: {e}")
        return results

    integrity_ok, error = verify_database_integrity()
    results['integrity'] = integrity_ok
    if not integrity_ok:
        results['errors'].append(f"Integrity check failed: {error}")

    wal_ok, error = verify_wal_enabled()
    results['wal_enabled'] = wal_ok
    if not wal_ok:
        results['errors'].append(f"WAL not enabled: {error}")

    return results


# ============================================
# STARTUP INITIALIZATION
# ============================================

def initialize_database_startup() -> Tuple[bool, List[str]]:
    """
    Initialize the database on application startup.
    Creates the DB if missing, verifies it, and ensures WAL.
    Does NOT run migrations (that is done by migrate.py).
    """
    errors = []

    lock_fd = acquire_db_init_lock()
    # lock_fd is never None now — it's either a file object or _NO_LOCK.
    # We proceed either way; the lock is best-effort.

    try:
        if not verify_database_exists():
            logger.info("Database not found. Creating new database...")
            success, error = create_database_schema()
            if not success:
                errors.append(f"Failed to create database schema: {error}")
                return False, errors

            success, error = enable_wal_mode()
            if not success:
                errors.append(f"Failed to enable WAL mode: {error}")
                return False, errors

            logger.info("Database created successfully")
        else:
            logger.info("Database already exists. Skipping creation.")

        logger.info("Verifying database...")
        results = verify_database_full()

        if results['errors']:
            errors.extend(results['errors'])
            return False, errors

        if not results['wal_enabled']:
            logger.warning("WAL not enabled, attempting to enable...")
            success, error = enable_wal_mode()
            if not success:
                errors.append(f"Failed to enable WAL mode: {error}")
                return False, errors
            logger.info("WAL mode enabled")

        logger.info("Database verification complete")
        return True, []
    except Exception as e:
        logger.error(f"Database initialization error: {e}", exc_info=True)
        errors.append(f"Unexpected error: {str(e)}")
        return False, errors
    finally:
        release_db_init_lock(lock_fd)


# ============================================
# HEALTH CHECK (for /health endpoint)
# ============================================

def get_database_health() -> Dict[str, Any]:
    health = {
        'exists': False,
        'openable': False,
        'writable': False,
        'integrity': False,
        'wal_enabled': False,
        'tables_ok': False,
        'columns_ok': False,
        'errors': [],
        'details': {}
    }

    health['exists'] = verify_database_exists()
    if not health['exists']:
        health['errors'].append("Database does not exist")
        return health

    openable, error = verify_database_openable()
    health['openable'] = openable
    if not openable:
        health['errors'].append(f"Cannot open database: {error}")
        return health

    writable, error = verify_database_writable()
    health['writable'] = writable
    if not writable:
        health['errors'].append(f"Database not writable: {error}")

    try:
        conn = _get_connection()
        ensure_question_interactions_table(conn)

        tables_ok, missing = verify_tables_exist(conn)
        health['tables_ok'] = tables_ok
        health['details']['missing_tables'] = missing

        columns_ok, missing_cols = verify_columns_exist(conn)
        health['columns_ok'] = columns_ok
        health['details']['missing_columns'] = missing_cols

        conn.close()
    except Exception as e:
        health['errors'].append(f"Table verification error: {e}")

    integrity_ok, error = verify_database_integrity()
    health['integrity'] = integrity_ok
    if not integrity_ok:
        health['errors'].append(f"Integrity check failed: {error}")

    wal_ok, error = verify_wal_enabled()
    health['wal_enabled'] = wal_ok
    if not wal_ok:
        health['errors'].append(f"WAL not enabled: {error}")

    return health


# ============================================
# LIVE QUIZ TABLES
# ============================================

def ensure_live_quiz_tables():
    """Create tables for live quiz events and checkpoints if missing."""
    try:
        conn = _get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS live_quiz_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_id INTEGER NOT NULL,
                user_id INTEGER,
                event_type TEXT NOT NULL,
                question_id INTEGER,
                payload TEXT,
                sequence INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_live_quiz_events_quiz_sequence ON live_quiz_events(quiz_id, sequence)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS live_quiz_checkpoints (
                quiz_id INTEGER PRIMARY KEY,
                checkpoint_data TEXT NOT NULL,
                version INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_live_quizzes_status ON live_quizzes(status)")

        conn.commit()
        conn.close()
        logger.info("Live quiz event and checkpoint tables verified/created.")
    except Exception as e:
        logger.error(f"Failed to create live quiz tables: {e}", exc_info=True)