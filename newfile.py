#!/usr/bin/env python3
"""
migrate_pdf_pipeline.py — one-time fix for the PDF pipeline.

Two databases, two changes:

  MAIN DB (nuunplatform.db):
    - Fix the `pdfs` table if it was created with the bot schema
      (file_id / file_unique_id / original_filename present,
       file_url / view_count missing).
    - Ensure it has file_id and file_unique_id so it's self-sufficient.

  BOT DB (bot_data.db):
    - Add `published` (INTEGER DEFAULT 0) and `published_at` (TEXT)
      columns to `pdfs` so published rows can be hidden from staging.

Idempotent. Safe to run multiple times. Writes a backup of each DB
before any change.

Compatible with Python 3.8+. No f-string backslashes anywhere.
"""

import os
import shutil
import sqlite3
import sys
from datetime import datetime


# ============================================
# PATHS
# ============================================

def _resolve_paths():
    main = os.environ.get('NUUN_DB_PATH')
    bot = os.environ.get('NUUN_BOT_DB_PATH')

    if not main or not bot:
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            if script_dir not in sys.path:
                sys.path.insert(0, script_dir)
            from config import Config
            main = main or getattr(Config, 'DATABASE_PATH', None)
            bot = bot or getattr(Config, 'BOT_DATABASE_PATH', None)
        except Exception:
            pass

    if not main:
        main = os.path.join(os.getcwd(), 'nuunplatform.db')
    if not bot:
        bot = os.path.join(os.getcwd(), 'bot_data.db')

    return os.path.abspath(main), os.path.abspath(bot)


MAIN_DB, BOT_DB = _resolve_paths()


# ============================================
# MAIN DB — schema
# ============================================

MAIN_PDFS_DDL = """
CREATE TABLE pdfs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    code           TEXT UNIQUE NOT NULL,
    title          TEXT NOT NULL,
    description    TEXT DEFAULT '',
    curriculum     TEXT DEFAULT 'PL'
                   CHECK (curriculum IN ('PL', 'SO', 'SL')),
    class          TEXT DEFAULT ''
                   CHECK (class IN ('', '7aad', '8aad', 'F3', 'F4')),
    subject        TEXT NOT NULL,
    chapter        TEXT DEFAULT '',
    tags           TEXT DEFAULT '',
    is_premium     INTEGER DEFAULT 0,
    file_url       TEXT,
    file_id        TEXT,
    file_unique_id TEXT UNIQUE,
    uploaded_by    TEXT NOT NULL DEFAULT 'NUUN',
    uploaded_at    TEXT DEFAULT (datetime('now', 'localtime')),
    view_count     INTEGER DEFAULT 0
);
"""

MAIN_PDFS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_pdfs_code          ON pdfs(code)",
    "CREATE INDEX IF NOT EXISTS idx_pdfs_curriculum    ON pdfs(curriculum)",
    "CREATE INDEX IF NOT EXISTS idx_pdfs_class         ON pdfs(class)",
    "CREATE INDEX IF NOT EXISTS idx_pdfs_subject       ON pdfs(subject)",
    "CREATE INDEX IF NOT EXISTS idx_pdfs_view_count    ON pdfs(view_count DESC)",
    "CREATE INDEX IF NOT EXISTS idx_pdfs_uploaded_at   ON pdfs(uploaded_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_pdfs_file_unique_id ON pdfs(file_unique_id)",
]

MAIN_REQUIRED_COLS = {'file_url', 'view_count', 'file_id', 'file_unique_id'}
MAIN_FORBIDDEN_COLS = {'original_filename'}

UNVERIFIED_DDL = """
CREATE TABLE IF NOT EXISTS unverified_pdfs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_id        INTEGER NOT NULL UNIQUE,
    published_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    confirmed     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (pdf_id) REFERENCES pdfs(id) ON DELETE CASCADE
);
"""

UNVERIFIED_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_unverified_pdfs_confirmed "
    "ON unverified_pdfs(confirmed)",
    "CREATE INDEX IF NOT EXISTS idx_unverified_pdfs_published "
    "ON unverified_pdfs(published_at DESC)",
]


# ============================================
# HELPERS
# ============================================

def open_rw(path):
    if not os.path.exists(path):
        return None
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def table_exists(conn, name):
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def columns_of(conn, name):
    try:
        return [r['name'] for r in conn.execute("PRAGMA table_info(" + name + ")")]
    except Exception:
        return []


def row_count(conn, name):
    try:
        return conn.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
    except Exception:
        return 0


def backup(path):
    if not os.path.exists(path):
        return None
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    dst = path + ".pre_migration." + stamp + ".bak"
    try:
        shutil.copy2(path, dst)
        print("  OK  Backup: " + dst)
        return dst
    except Exception as e:
        print("  !   Backup failed: " + str(e))
        return None


# ============================================
# MAIN DB migration
# ============================================

def migrate_main_pdfs(conn):
    if not table_exists(conn, 'pdfs'):
        print("  [main] pdfs missing - creating.")
        conn.executescript(MAIN_PDFS_DDL)
        for idx in MAIN_PDFS_INDEXES:
            conn.execute(idx)
        conn.commit()
        return 'created'

    cols = set(columns_of(conn, 'pdfs'))
    missing = MAIN_REQUIRED_COLS - cols
    forbidden = MAIN_FORBIDDEN_COLS & cols

    if not missing and not forbidden:
        print("  [main] pdfs already has correct schema.")
        return 'already_ok'

    print("  [main] schema drift detected.")
    if missing:
        print("         missing: " + str(sorted(missing)))
    if forbidden:
        print("         bot-only cols: " + str(sorted(forbidden)))

    count = row_count(conn, 'pdfs')
    print("  [main] current rows: " + str(count))

    if count == 0:
        print("  [main] empty - dropping and recreating.")
        conn.execute("DROP TABLE pdfs")
        conn.executescript(MAIN_PDFS_DDL)
        for idx in MAIN_PDFS_INDEXES:
            conn.execute(idx)
        conn.commit()
        return 'recreated_empty'

    # ---- Non-empty: migrate rows ----
    print("  [main] migrating rows.")

    def col(name, fallback):
        """Return the real column name if it exists, else a SQL literal fallback."""
        return name if name in cols else fallback

    e_id          = col('id', 'NULL')
    e_code        = col('code', "''")
    e_title       = col('title', "''")
    e_desc        = "COALESCE(" + col('description', "''") + ", '')"
    e_curr        = "COALESCE(" + col('curriculum', "'PL'") + ", 'PL')"
    e_class       = "COALESCE(" + col('class', "''") + ", '')"
    e_subject     = col('subject', "''")
    e_chapter     = "COALESCE(" + col('chapter', "''") + ", '')"
    e_tags        = "COALESCE(" + col('tags', "''") + ", '')"
    e_premium     = "COALESCE(" + col('is_premium', '0') + ", 0)"
    e_file_url    = col('file_url', 'NULL')
    e_file_id     = col('file_id', 'NULL')
    e_file_uniq   = col('file_unique_id', 'NULL')
    e_uploaded_by = "CAST(COALESCE(" + col('uploaded_by', "'NUUN'") + ", 'NUUN') AS TEXT)"
    e_uploaded_at = "COALESCE(" + col('uploaded_at', "datetime('now','localtime')") + ", datetime('now','localtime'))"
    e_view_count  = "COALESCE(" + col('view_count', '0') + ", 0)"

    select_exprs = [
        e_id, e_code, e_title, e_desc, e_curr, e_class, e_subject,
        e_chapter, e_tags, e_premium, e_file_url, e_file_id, e_file_uniq,
        e_uploaded_by, e_uploaded_at, e_view_count,
    ]
    select_sql = "SELECT " + ", ".join(select_exprs) + " FROM pdfs"
    rows = conn.execute(select_sql).fetchall()

    conn.execute("DROP TABLE pdfs")
    conn.executescript(MAIN_PDFS_DDL)
    for idx in MAIN_PDFS_INDEXES:
        conn.execute(idx)

    insert_sql = """
        INSERT INTO pdfs (
            id, code, title, description, curriculum, class, subject,
            chapter, tags, is_premium, file_url, file_id, file_unique_id,
            uploaded_by, uploaded_at, view_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    migrated = 0
    for r in rows:
        try:
            conn.execute(insert_sql, tuple(r))
            migrated += 1
        except sqlite3.IntegrityError as e:
            print("         ! skipped row: " + str(e))
    conn.commit()
    print("  [main] migrated " + str(migrated) + " of " + str(len(rows)) + " row(s).")
    return 'migrated'


def ensure_unverified(conn):
    if table_exists(conn, 'unverified_pdfs'):
        print("  [main] unverified_pdfs already exists.")
        for idx in UNVERIFIED_INDEXES:
            conn.execute(idx)
        conn.commit()
        return 'already_ok'
    print("  [main] creating unverified_pdfs.")
    conn.executescript(UNVERIFIED_DDL)
    for idx in UNVERIFIED_INDEXES:
        conn.execute(idx)
    conn.commit()
    return 'created'


# ============================================
# BOT DB migration
# ============================================

def migrate_bot_pdfs(conn):
    if not table_exists(conn, 'pdfs'):
        print("  [bot] pdfs missing - bot/db.py::init_bot_db will create it on next boot.")
        return 'missing'

    cols = set(columns_of(conn, 'pdfs'))
    changed = False

    if 'published' not in cols:
        print("  [bot] adding column: published")
        conn.execute(
            "ALTER TABLE pdfs ADD COLUMN published INTEGER NOT NULL DEFAULT 0"
        )
        changed = True
    else:
        print("  [bot] published column already present.")

    if 'published_at' not in cols:
        print("  [bot] adding column: published_at")
        conn.execute("ALTER TABLE pdfs ADD COLUMN published_at TEXT")
        changed = True
    else:
        print("  [bot] published_at column already present.")

    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_pdfs_published "
            "ON pdfs(published)"
        )
    except Exception:
        pass

    if changed:
        conn.commit()
        return 'migrated'
    return 'already_ok'


# ============================================
# MAIN
# ============================================

def main():
    print("")
    print("=" * 64)
    print("  NuunPlatform - PDF pipeline migration")
    print("  Main DB: " + MAIN_DB)
    print("  Bot DB:  " + BOT_DB)
    print("=" * 64)
    print("")

    # ---- MAIN ----
    print("  Main database...")
    if not os.path.exists(MAIN_DB):
        print("  ! Main DB not found at " + MAIN_DB + " - skipping.")
    else:
        backup(MAIN_DB)
        conn = open_rw(MAIN_DB)
        try:
            r1 = migrate_main_pdfs(conn)
            print("         -> " + r1)
            r2 = ensure_unverified(conn)
            print("         -> " + r2)

            print("")
            print("  Main pdfs final columns:")
            for c in columns_of(conn, 'pdfs'):
                print("     - " + c)
            print("  Main pdfs rows:            " + str(row_count(conn, 'pdfs')))
            print("  Main unverified_pdfs rows: " + str(row_count(conn, 'unverified_pdfs')))
        finally:
            conn.close()

    print("")

    # ---- BOT ----
    print("  Bot database...")
    if not os.path.exists(BOT_DB):
        print("  ! Bot DB not found at " + BOT_DB + " - skipping.")
    else:
        backup(BOT_DB)
        conn = open_rw(BOT_DB)
        try:
            r3 = migrate_bot_pdfs(conn)
            print("         -> " + r3)

            print("")
            print("  Bot pdfs final columns:")
            for c in columns_of(conn, 'pdfs'):
                print("     - " + c)
            print("  Bot pdfs rows:             " + str(row_count(conn, 'pdfs')))
            try:
                pending = conn.execute(
                    "SELECT COUNT(*) FROM pdfs WHERE COALESCE(published, 0) = 0"
                ).fetchone()[0]
                print("  Bot pdfs unpublished:      " + str(pending))
            except Exception:
                pass
        finally:
            conn.close()

    print("")
    print("  Done. Restart the Flask app.")
    print("")


if __name__ == '__main__':
    main()