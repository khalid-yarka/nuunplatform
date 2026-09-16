# safe_db.py — shadow mirror of writes to safety/safety.db
#
# Fire-and-forget. Never raises, never blocks the main write path.
#
# ─── DEFENSIVE BEHAVIOUR ────────────────────────────────────────
#   • Lazy init — DB is created on the first mirrored write.
#   • Schema is auto-synced from the main DB per table:
#       - Table missing → created from main's exact DDL.
#       - Table exists but missing columns → missing columns added
#         via ALTER TABLE (constraints relaxed, since SQLite won't
#         let us add NOT NULL / UNIQUE / PK columns post-hoc).
#   • Per-write verification: if a needed column is still missing
#     after a sync attempt, the write is skipped with a SINGLE
#     warning per (table, column) pair — no log spam.
#   • Any exception is swallowed. The main DB is never affected.
# ─────────────────────────────────────────────────────────────────

import os
import re
import sqlite3
import logging
import threading
from config import Config

logger = logging.getLogger(__name__)

SAFETY_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'safety', 'safety.db'
)

MAIN_DB_PATH = Config.DATABASE_PATH

# Only these tables are mirrored. Everything else is ignored.
WHITELISTED_TABLES = {
    'students',
    'questions',
    'pdfs',
    'groups',
    'question_interactions',
    'upgrade_requests',
}


# ============================================
# MODULE STATE (thread-safe)
# ============================================

_lock = threading.RLock()
_shadow_conn = None
_initialized = False
_warned = set()          # dedup for warnings
_synced_tables = set()   # tables we've already checked this session


def _warn_once(key, message):
    """Log a warning at most once per process for the given key."""
    with _lock:
        if key in _warned:
            return
        _warned.add(key)
    try:
        logger.warning(message)
    except Exception:
        pass


# ============================================
# CONNECTION HELPERS
# ============================================

def _open_connection(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = NORMAL")
    except Exception:
        pass
    return conn


def _open_main_connection():
    if not os.path.exists(MAIN_DB_PATH):
        return None
    try:
        return _open_connection(MAIN_DB_PATH)
    except Exception:
        return None


def _get_shadow_connection():
    """Lazy-open the shadow DB. Returns None on any failure."""
    global _shadow_conn

    with _lock:
        if _shadow_conn is not None:
            return _shadow_conn

        try:
            os.makedirs(os.path.dirname(SAFETY_DB_PATH), exist_ok=True)
            _shadow_conn = _open_connection(SAFETY_DB_PATH)
        except Exception as e:
            _warn_once('shadow_open', f"safe_db: could not open shadow DB: {e}")
            _shadow_conn = None

        return _shadow_conn


# ============================================
# SCHEMA INTROSPECTION
# ============================================

def _columns_of(conn, table):
    """Return list of column names for a table, or [] if missing."""
    if conn is None:
        return []
    try:
        cur = conn.execute("PRAGMA table_info(" + table + ")")
        return [r['name'] for r in cur.fetchall()]
    except Exception:
        return []


def _table_exists(conn, table):
    if conn is None:
        return False
    try:
        cur = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        return cur.fetchone() is not None
    except Exception:
        return False


def _fetch_ddl(conn, table):
    """Return the CREATE TABLE statement for a table, or None."""
    if conn is None:
        return None
    try:
        cur = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        row = cur.fetchone()
        return row['sql'] if row else None
    except Exception:
        return None


def _fetch_indexes(conn, table):
    """Return list of CREATE INDEX statements for a table."""
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
            (table,),
        )
        return [r['sql'] for r in cur.fetchall() if r['sql']]
    except Exception:
        return []


def _column_definition(conn, table, column):
    """
    Return a safe SQL fragment for adding a column.

    SQLite's ALTER TABLE ADD COLUMN doesn't allow NOT NULL without a
    default, UNIQUE, or PRIMARY KEY. We relax those — the mirror just
    needs the column to exist so writes don't fail.
    """
    if conn is None:
        return None
    try:
        cur = conn.execute("PRAGMA table_info(" + table + ")")
        for r in cur.fetchall():
            if r['name'] == column:
                col_type = r['type'] or 'TEXT'
                default = r['dflt_value']
                frag = column + " " + col_type
                if default is not None:
                    frag += " DEFAULT " + str(default)
                return frag
    except Exception:
        pass
    # Fallback: bare TEXT column
    return column + " TEXT"


# ============================================
# SCHEMA SYNC
# ============================================

def _sync_table_schema(shadow, table, force=False):
    """
    Ensure the shadow table exists and has all columns the main table has.

    - Table missing → CREATE from main's DDL + indexes.
    - Columns missing → ALTER TABLE ADD COLUMN for each.

    Returns True on success (or nothing-to-do), False on hard failure.
    """
    if shadow is None:
        return False

    if not force and table in _synced_tables:
        return True

    main = _open_main_connection()
    if main is None:
        return False

    try:
        # ---- Table missing in shadow ----
        if not _table_exists(shadow, table):
            ddl = _fetch_ddl(main, table)
            if not ddl:
                return False
            try:
                shadow.execute(ddl)
                shadow.commit()
            except sqlite3.OperationalError as e:
                # Race: another thread created it just now. Fall through
                # to column check below.
                if 'already exists' not in str(e).lower():
                    _warn_once(
                        f'schema_create_{table}',
                        f"safe_db: could not create {table}: {e}"
                    )
                    return False

            # Copy indexes (best-effort, ignore conflicts)
            for idx_sql in _fetch_indexes(main, table):
                try:
                    shadow.execute(idx_sql)
                except Exception:
                    pass
            try:
                shadow.commit()
            except Exception:
                pass

        # ---- Column drift ----
        shadow_cols = set(_columns_of(shadow, table))
        main_cols = set(_columns_of(main, table))
        missing = main_cols - shadow_cols

        if missing:
            added = 0
            for col in sorted(missing):
                frag = _column_definition(main, table, col)
                if not frag:
                    continue
                try:
                    shadow.execute(
                        "ALTER TABLE " + table + " ADD COLUMN " + frag
                    )
                    added += 1
                except sqlite3.OperationalError as e:
                    err = str(e).lower()
                    if 'duplicate column' in err:
                        added += 1
                        continue
                    _warn_once(
                        f'alter_{table}_{col}',
                        f"safe_db: could not add column {table}.{col}: {e}"
                    )
            if added:
                try:
                    shadow.commit()
                except Exception:
                    pass

        with _lock:
            _synced_tables.add(table)
        return True
    finally:
        try:
            main.close()
        except Exception:
            pass


# ============================================
# SQL PARSING
# ============================================

_INSERT_RE = re.compile(
    r'^\s*INSERT\s+(?:OR\s+\w+\s+)?INTO\s+["`\[]?(\w+)["`\]]?\s*\(([^)]+)\)',
    re.IGNORECASE | re.DOTALL,
)
_UPDATE_RE = re.compile(
    r'^\s*UPDATE\s+["`\[]?(\w+)["`\]]?\s+SET\s+',
    re.IGNORECASE,
)
_COL_NAME_RE = re.compile(r'["`\[]?(\w+)["`\]]?')


def _table_of(sql):
    """Extract the table name from an INSERT or UPDATE statement."""
    if not sql:
        return None
    m = _INSERT_RE.match(sql) or _UPDATE_RE.match(sql)
    if not m:
        return None
    return m.group(1)


def _columns_in_insert(sql):
    """Extract explicit column list from an INSERT (may be empty)."""
    m = _INSERT_RE.match(sql)
    if not m:
        return []
    raw = m.group(2)
    return [c.strip(' "`[]') for c in raw.split(',') if c.strip()]


# ============================================
# WRITE MIRROR
# ============================================

def _do_mirror(conn, sql, params):
    """Run one mirror write. Returns True on success."""
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        return True
    except sqlite3.OperationalError as e:
        err = str(e).lower()
        # Schema drift signals — caller may retry after a force-sync.
        if ('no column named' in err
                or 'has no column' in err
                or 'no such table' in err
                or 'no such column' in err):
            raise
        # Anything else: log once and skip.
        _warn_once('op_' + str(e)[:60], f"safe_db: write error: {e}")
        return False
    except Exception as e:
        _warn_once('op_' + str(e)[:60], f"safe_db: write error: {e}")
        return False


def mirror_write(sql, params=()):
    """
    Mirror a single INSERT / UPDATE to the shadow DB.
    Never raises. Never affects the main write path.
    """
    try:
        table = _table_of(sql)
        if not table or table not in WHITELISTED_TABLES:
            return

        conn = _get_shadow_connection()
        if conn is None:
            return

        # ---- Attempt 1: normal sync ----
        try:
            _sync_table_schema(conn, table)
            if _do_mirror(conn, sql, params):
                return
        except sqlite3.OperationalError as e:
            err = str(e).lower()
            if not ('no column' in err or 'no such' in err):
                _warn_once(f'mirror1_{table}', f"safe_db: {table}: {e}")
                return

        # ---- Attempt 2: force re-sync (column drift, race) ----
        try:
            with _lock:
                _synced_tables.discard(table)
            _sync_table_schema(conn, table, force=True)
            _do_mirror(conn, sql, params)
        except sqlite3.OperationalError as e:
            # Still failing — one warning, give up.
            err = str(e).lower()
            if 'no column' in err:
                # Extract the column name if possible for a cleaner message.
                m = re.search(r'no column named\s+(\w+)', err)
                col = m.group(1) if m else '?'
                _warn_once(
                    f'col_{table}_{col}',
                    f"safe_db: skipping mirrors for {table}.{col} "
                    f"(column missing in shadow)."
                )
            else:
                _warn_once(f'mirror2_{table}', f"safe_db: {table}: {e}")
        except Exception as e:
            _warn_once(f'mirror2_{table}', f"safe_db: {table}: {e}")
    except Exception as e:
        # Absolute last-resort guard — should never fire.
        _warn_once('mirror_fatal', f"safe_db: fatal: {e}")


def mirror_write_batch(sql, params_list):
    """
    Mirror many INSERTs / UPDATEs with the same SQL.
    Never raises. Never affects the main write path.
    """
    if not params_list:
        return

    try:
        table = _table_of(sql)
        if not table or table not in WHITELISTED_TABLES:
            return

        conn = _get_shadow_connection()
        if conn is None:
            return

        # Ensure schema once
        try:
            _sync_table_schema(conn, table)
        except Exception as e:
            _warn_once(f'batch_sync_{table}', f"safe_db: {table}: {e}")
            return

        # Attempt in one transaction.
        try:
            cur = conn.cursor()
            cur.executemany(sql, params_list)
            conn.commit()
            return
        except sqlite3.OperationalError as e:
            err = str(e).lower()
            if not ('no column' in err or 'no such' in err):
                _warn_once(f'batch_{table}', f"safe_db: {table}: {e}")
                return
            # Roll back the partial batch, then retry below.
            try:
                conn.rollback()
            except Exception:
                pass

        # Force re-sync, retry once.
        try:
            with _lock:
                _synced_tables.discard(table)
            _sync_table_schema(conn, table, force=True)
            cur = conn.cursor()
            cur.executemany(sql, params_list)
            conn.commit()
        except sqlite3.OperationalError as e:
            err = str(e).lower()
            m = re.search(r'no column named\s+(\w+)', err)
            if m:
                _warn_once(
                    f'batch_col_{table}_{m.group(1)}',
                    f"safe_db: batch skipping {table}.{m.group(1)} "
                    f"(column missing in shadow)."
                )
            else:
                _warn_once(f'batch2_{table}', f"safe_db: {table}: {e}")
        except Exception as e:
            _warn_once(f'batch2_{table}', f"safe_db: {table}: {e}")
    except Exception as e:
        _warn_once('batch_fatal', f"safe_db: batch fatal: {e}")


# ============================================
# READ-ONLY VIEWER (used by admin safety panel)
# ============================================

def list_tables():
    """Return all tables present in the shadow DB."""
    conn = _get_shadow_connection()
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
        return [r['name'] for r in cur.fetchall()]
    except Exception:
        return []


def table_columns(table):
    """Return column names for a shadow table."""
    conn = _get_shadow_connection()
    return _columns_of(conn, table)


def table_rows(table, limit=100, offset=0):
    """Return up to `limit` rows from a shadow table."""
    conn = _get_shadow_connection()
    if conn is None or table not in list_tables():
        return []
    try:
        cur = conn.execute(
            "SELECT * FROM " + table + " LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def stats():
    """Return basic stats about the shadow DB."""
    conn = _get_shadow_connection()
    if conn is None:
        return {'exists': False}

    try:
        out = {
            'exists': True,
            'path': SAFETY_DB_PATH,
            'tables': {},
        }
        for t in list_tables():
            try:
                row = conn.execute("SELECT COUNT(*) FROM " + t).fetchone()
                out['tables'][t] = row[0] if row else 0
            except Exception:
                out['tables'][t] = None
        return out
    except Exception:
        return {'exists': True, 'error': 'read failed'}


# ============================================
# STARTUP (optional)
# ============================================

def prime_schema():
    """
    Optional: sync all whitelisted tables once at startup.
    Not required — lazy sync happens on first write — but calling this
    after app startup avoids the first-write hiccup.
    """
    conn = _get_shadow_connection()
    if conn is None:
        return
    for t in WHITELISTED_TABLES:
        try:
            _sync_table_schema(conn, t)
        except Exception:
            pass