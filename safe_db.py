# ============================================================
# safe_db.py
# Insert + Update mirror into safety/safety.db.
#
# This module is intentionally self-contained:
#   • Uses raw sqlite3 — never imports db.py.
#   • Never raises — every failure is logged and swallowed.
#   • Only mirrors INSERT and UPDATE against a whitelist.
#   • DELETE is intentionally NOT mirrored, so deleted rows
#     remain recoverable from the shadow.
#
# Called from db.execute_with_retry / execute_many_with_retry
# AFTER the main connection has committed.
# ============================================================

import os
import re
import sqlite3
import threading
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
SAFETY_DIR = BASE_DIR / 'safety'
SAFETY_DB_PATH = str(SAFETY_DIR / 'safety.db')


# ============================================================
# WHITELIST
# ============================================================
# Only these tables are mirrored. Anything else is a no-op.

MIRROR_TABLES = frozenset({
    'students',
    'questions',
    'pdfs',
    'groups',
    'question_interactions',
    'upgrade_requests',
})


# ============================================================
# SQL PARSER
# ============================================================
# Matches only INSERT and UPDATE. Everything else returns None.

_WRITE_RE = re.compile(
    r'^\s*(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE)\s+'
    r'["\'`\[]?([A-Za-z_][A-Za-z0-9_]*)',
    re.IGNORECASE,
)


# ============================================================
# PER-THREAD CONNECTION + ONE-TIME INIT
# ============================================================

_thread_local = threading.local()
_init_lock = threading.Lock()
_initialized = False


def _open_connection():
    """Open (creating if needed) the shadow DB with standard pragmas."""
    os.makedirs(SAFETY_DIR, exist_ok=True)
    conn = sqlite3.connect(SAFETY_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _open_main_connection():
    """Open the main DB read-only, only to copy schema."""
    try:
        from config import Config
        main_path = Config.DATABASE_PATH
        if not os.path.isabs(main_path):
            main_path = str(BASE_DIR / main_path)
        conn = sqlite3.connect(main_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.warning(f"safe_db: cannot open main DB for schema: {e}")
        return None


def _copy_schema_from_main(shadow_conn):
    """
    For every whitelisted table, ensure the shadow has the same
    schema as the main DB. Creates the table if missing, adds any
    columns that have been added to main since the shadow was
    created.
    """
    main_conn = _open_main_connection()
    if main_conn is None:
        return False

    ok = True
    try:
        for table in sorted(MIRROR_TABLES):
            try:
                row = main_conn.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if not row or not row['sql']:
                    logger.warning(
                        f"safe_db: no schema for '{table}' in main DB"
                    )
                    ok = False
                    continue

                # Create in shadow if missing.
                shadow_conn.execute(row['sql'])

                # Column drift: add any columns missing from shadow.
                main_cols = main_conn.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
                shadow_cols = {
                    r['name'] for r in shadow_conn.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                }
                for cinfo in main_cols:
                    col = cinfo['name']
                    if col in shadow_cols:
                        continue
                    ddl = f"ALTER TABLE {table} ADD COLUMN {col} {cinfo['type']}"
                    if cinfo['dflt_value'] is not None:
                        ddl += f" DEFAULT {cinfo['dflt_value']}"
                    try:
                        shadow_conn.execute(ddl)
                        logger.info(
                            f"safe_db: added column {table}.{col}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"safe_db: cannot add {table}.{col}: {e}"
                        )

            except Exception as e:
                logger.warning(
                    f"safe_db: schema copy failed for '{table}': {e}"
                )
                ok = False

        try:
            shadow_conn.commit()
        except Exception:
            pass
    finally:
        try:
            main_conn.close()
        except Exception:
            pass

    return ok


def _ensure_init():
    """Create the shadow DB and its schema on first use."""
    global _initialized
    if _initialized:
        return True
    with _init_lock:
        if _initialized:
            return True
        try:
            conn = _open_connection()
            _copy_schema_from_main(conn)
            conn.commit()
            conn.close()
            _initialized = True
            logger.info(f"safe_db: initialized at {SAFETY_DB_PATH}")
            return True
        except Exception as e:
            logger.error(f"safe_db: initialization failed: {e}")
            return False


def _get_shadow_connection():
    """Per-thread cached connection."""
    if not _ensure_init():
        return None
    conn = getattr(_thread_local, 'conn', None)
    if conn is None:
        try:
            conn = _open_connection()
            _thread_local.conn = conn
        except Exception as e:
            logger.warning(f"safe_db: connection failed: {e}")
            return None
    return conn


# ============================================================
# TABLE DETECTION
# ============================================================

def _table_of(sql):
    """Return the mirrored table name if this SQL should be mirrored."""
    if not sql:
        return None
    m = _WRITE_RE.match(sql)
    if not m:
        return None
    table = m.group(1).lower()
    if table not in MIRROR_TABLES:
        return None
    return table


# ============================================================
# PUBLIC — MIRROR WRITE
# ============================================================

def mirror_write(sql, params=()):
    """
    Mirror a single INSERT/UPDATE to the shadow. Never raises.
    Called by db.execute_with_retry after a successful main commit.
    """
    table = _table_of(sql)
    if table is None:
        return

    conn = _get_shadow_connection()
    if conn is None:
        return

    try:
        conn.execute(sql, params)
        conn.commit()
    except sqlite3.IntegrityError as e:
        # A row already exists in the shadow (unlikely but harmless).
        # Leave the shadow as-is; the older value is the "origin".
        logger.debug(f"safe_db: integrity clash for {table}: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    except sqlite3.OperationalError as e:
        logger.warning(f"safe_db: op error for {table}: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"safe_db: unexpected error for {table}: {e}")
        try:
            conn.rollback()
        except Exception:
            pass


def mirror_write_batch(sql, params_list):
    """
    Mirror a batched INSERT/UPDATE to the shadow. Never raises.
    Called by db.execute_many_with_retry after a successful commit.
    """
    if not params_list:
        return
    table = _table_of(sql)
    if table is None:
        return

    conn = _get_shadow_connection()
    if conn is None:
        return

    try:
        conn.executemany(sql, params_list)
        conn.commit()
    except sqlite3.IntegrityError as e:
        logger.debug(f"safe_db: integrity clash (batch) for {table}: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    except sqlite3.OperationalError as e:
        logger.warning(f"safe_db: op error (batch) for {table}: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"safe_db: unexpected error (batch) for {table}: {e}")
        try:
            conn.rollback()
        except Exception:
            pass


# ============================================================
# READ-ONLY HELPERS FOR THE SUPER-ADMIN VIEWER
# ============================================================

def list_tables():
    """Return [{name, count}] for every whitelisted table."""
    conn = _get_shadow_connection()
    if conn is None:
        return []
    out = []
    for t in sorted(MIRROR_TABLES):
        try:
            n = conn.execute(
                f"SELECT COUNT(*) AS c FROM {t}"
            ).fetchone()['c']
            out.append({'name': t, 'count': int(n or 0)})
        except Exception:
            out.append({'name': t, 'count': 0})
    return out


def table_columns(table):
    """Return the ordered column names for a mirrored table."""
    if table not in MIRROR_TABLES:
        return []
    conn = _get_shadow_connection()
    if conn is None:
        return []
    try:
        return [
            r['name'] for r in conn.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
        ]
    except Exception:
        return []


def table_rows(table, limit=50, offset=0, search=''):
    """
    Return (rows, total) for a mirrored table.
    Read-only. Never raises.
    """
    if table not in MIRROR_TABLES:
        return [], 0
    conn = _get_shadow_connection()
    if conn is None:
        return [], 0

    where = ''
    params = []

    if search:
        cols = table_columns(table)
        if cols:
            where = 'WHERE ' + ' OR '.join(
                f"CAST({c} AS TEXT) LIKE ?" for c in cols
            )
            params.extend([f'%{search}%'] * len(cols))

    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM {table} {where}", params
        ).fetchone()['c']

        rows = conn.execute(
            f"SELECT * FROM {table} {where} "
            f"ORDER BY rowid DESC LIMIT ? OFFSET ?",
            params + [int(limit), int(offset)],
        ).fetchall()

        return [dict(r) for r in rows], int(total or 0)
    except Exception as e:
        logger.warning(f"safe_db: read failed for {table}: {e}")
        return [], 0


def stats():
    """Aggregate counts + file size for the overview header."""
    out = {
        'path': SAFETY_DB_PATH,
        'exists': os.path.exists(SAFETY_DB_PATH),
        'size_mb': 0.0,
        'tables': [],
        'total_rows': 0,
    }
    if out['exists']:
        try:
            out['size_mb'] = round(
                os.path.getsize(SAFETY_DB_PATH) / (1024 * 1024), 2
            )
        except Exception:
            pass
    tables = list_tables()
    out['tables'] = tables
    out['total_rows'] = sum(t['count'] for t in tables)
    return out