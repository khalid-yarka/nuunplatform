# safe_db.py — shadow mirror of writes to safety/safety.db
#
# Fire-and-forget. Never raises, never blocks the main write path.
#
# ─── THREAD SAFETY (2024-09 rewrite) ────────────────────────────
#   • Connections are per-thread. A single module-level connection
#     raised "SQLite objects created in a thread can only be used
#     in that same thread" whenever a Flask worker alternated
#     between request threads and background threads.
#   • Main DB introspection still opens its own short-lived
#     connection per schema-sync call — that path never caches.
#   • Global state (the sync-once set and the warn-once set) is
#     guarded by an RLock.
#
# ─── DEFENSIVE BEHAVIOUR ────────────────────────────────────────
#   • Lazy init — DB is created on the first mirrored write.
#   • Schema is auto-synced from the main DB per table:
#       - Table missing → created from main's exact DDL.
#       - Schema drift (e.g. main made a column nullable but the
#         shadow still has NOT NULL) → shadow table is dropped
#         and recreated from main's DDL. The mirror is not a
#         source of truth, so losing rows is acceptable.
#       - Missing columns only → added via ALTER TABLE ADD COLUMN.
#   • Per-write verification: a skipped write logs ONE warning
#     per (table, column) pair — no log spam.
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
# MODULE STATE
# ============================================

# Per-thread connection holder. Each thread gets its own shadow
# connection on first use. No sharing across threads.
_thread_local = threading.local()

# Guards the module-level state dicts/sets below.
_lock = threading.RLock()

# Dedup set for warnings — safe to share, guarded by _lock.
_warned = set()

# Tables we've already verified against main's schema this process.
# Shared across threads: once a table is synced, it's synced in the
# underlying file, regardless of which connection did the sync.
_synced_tables = set()


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
    """Open a SQLite connection with the mirror's pragmas."""
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
    """Short-lived connection to the main DB for schema introspection."""
    if not os.path.exists(MAIN_DB_PATH):
        return None
    try:
        return _open_connection(MAIN_DB_PATH)
    except Exception:
        return None


def _get_shadow_connection():
    """
    Return this thread's shadow connection, creating it lazily.
    Returns None on any failure (never raises).
    """
    conn = getattr(_thread_local, 'conn', None)
    if conn is not None:
        return conn

    try:
        os.makedirs(os.path.dirname(SAFETY_DB_PATH), exist_ok=True)
        conn = _open_connection(SAFETY_DB_PATH)
        _thread_local.conn = conn
        return conn
    except Exception as e:
        _warn_once('shadow_open', f"safe_db: could not open shadow DB: {e}")
        _thread_local.conn = None
        return None


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
    return column + " TEXT"


# ============================================
# SCHEMA DRIFT DETECTION
# ============================================

_NOT_NULL_RE = re.compile(
    r'([A-Za-z_][A-Za-z0-9_]*)\s+[A-Za-z]+'
    r'(?:\s*\([^)]*\))?'                 # optional type args
    r'[^,)]*?'                           # any other modifiers
    r'\bNOT\s+NULL\b',
    re.IGNORECASE,
)


def _extract_not_null_columns(ddl):
    """Return the set of column names declared NOT NULL in a DDL string."""
    if not ddl:
        return set()
    cols = set()
    for m in _NOT_NULL_RE.finditer(ddl):
        cols.add(m.group(1).lower())
    return cols


def _needs_rebuild(shadow, main, table):
    """
    Return True when the shadow table has a NOT NULL column that the
    main table does not. That's the specific drift that breaks writes
    (e.g. main.pdfs.subject became nullable, but safety.pdfs.subject
    still has NOT NULL).
    """
    shadow_ddl = _fetch_ddl(shadow, table)
    main_ddl = _fetch_ddl(main, table)
    if not shadow_ddl or not main_ddl:
        return False

    shadow_nn = _extract_not_null_columns(shadow_ddl)
    main_nn = _extract_not_null_columns(main_ddl)

    # If the shadow enforces NOT NULL on any column main has relaxed,
    # the shadow's schema is stale and must be rebuilt.
    return bool(shadow_nn - main_nn)


# ============================================
# SCHEMA SYNC
# ============================================

def _sync_table_schema(shadow, table, force=False):
    """
    Ensure the shadow table exists and matches the main table's shape.

    Order of operations:
      1. If the table is missing entirely → create from main's DDL.
      2. If a NOT NULL mismatch exists → drop and recreate.
      3. Otherwise, add any missing columns (ALTER ADD COLUMN).

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
        # ── Case 1: table doesn't exist in shadow ────────────────
        if not _table_exists(shadow, table):
            ddl = _fetch_ddl(main, table)
            if not ddl:
                return False
            try:
                shadow.execute(ddl)
                shadow.commit()
            except sqlite3.OperationalError as e:
                if 'already exists' not in str(e).lower():
                    _warn_once(
                        f'schema_create_{table}',
                        f"safe_db: could not create {table}: {e}"
                    )
                    return False

            # Copy indexes (best-effort)
            for idx_sql in _fetch_indexes(main, table):
                try:
                    shadow.execute(idx_sql)
                except Exception:
                    pass
            try:
                shadow.commit()
            except Exception:
                pass

            with _lock:
                _synced_tables.add(table)
            return True

        # ── Case 2: shadow schema has stale NOT NULL constraints ──
        if _needs_rebuild(shadow, main, table):
            ddl = _fetch_ddl(main, table)
            if not ddl:
                return False
            try:
                shadow.execute("DROP TABLE IF EXISTS " + table)
                shadow.execute(ddl)
                for idx_sql in _fetch_indexes(main, table):
                    try:
                        shadow.execute(idx_sql)
                    except Exception:
                        pass
                shadow.commit()
            except Exception as e:
                _warn_once(
                    f'schema_rebuild_{table}',
                    f"safe_db: rebuild failed for {table}: {e}"
                )
                return False

            with _lock:
                _synced_tables.add(table)
            return True

        # ── Case 3: additive column drift ────────────────────────
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

        # ---- Attempt 2: force re-sync ----
        try:
            with _lock:
                _synced_tables.discard(table)
            _sync_table_schema(conn, table, force=True)
            _do_mirror(conn, sql, params)
        except sqlite3.OperationalError as e:
            err = str(e).lower()
            if 'no column' in err:
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

        try:
            _sync_table_schema(conn, table)
        except Exception as e:
            _warn_once(f'batch_sync_{table}', f"safe_db: {table}: {e}")
            return

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
            try:
                conn.rollback()
            except Exception:
                pass

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
# READ-ONLY VIEWER (admin safety panel)
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
# STARTUP
# ============================================

def prime_schema():
    """
    Sync all whitelisted tables once. Called from app startup so the
    first mirrored write doesn't pay the schema-sync cost.
    """
    conn = _get_shadow_connection()
    if conn is None:
        return
    for t in WHITELISTED_TABLES:
        try:
            _sync_table_schema(conn, t)
        except Exception:
            pass


# ============================================
# BACKWARD-COMPAT ALIASES
# ============================================

# Old name for the whitelist.
MIRROR_TABLES = WHITELISTED_TABLES


def _copy_schema_from_main(*args, **kwargs):
    """Legacy no-op. Schema sync now happens per-table on first write."""
    return True


def _ensure_init(*args, **kwargs):
    """Legacy no-op. Initialisation is lazy in this version."""
    return True