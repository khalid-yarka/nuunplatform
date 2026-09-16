#!/usr/bin/env python3
"""
migrate_unverified_pdfs.py — one-time migration for NuunPlatform.

Creates the `unverified_pdfs` table in the main database.

This table tracks PDFs that were published directly from the intake
queue via the super-admin "Super Publish" action. Any admin can later
review them and mark them as confirmed.

Idempotent — safe to run multiple times. If the table already exists,
the script reports it and exits without changes.

Usage:
    python migrate_unverified_pdfs.py

    # Or with an explicit database path
    NUUN_DB_PATH=/path/to/nuunplatform.db python migrate_unverified_pdfs.py
"""

import os
import sqlite3
import sys


# ============================================
# DB PATH RESOLUTION
# ============================================

def resolve_db_path():
    """Find the main DB the same way the app does."""
    # 1) Explicit override
    env = os.environ.get('NUUN_DB_PATH')
    if env:
        return os.path.abspath(env)

    # 2) Try the platform's config module
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        from config import Config
        return os.path.abspath(Config.DATABASE_PATH)
    except Exception:
        pass

    # 3) Standard name in the current directory
    return os.path.abspath(os.path.join(os.getcwd(), 'nuunplatform.db'))


DB_PATH = resolve_db_path()


# ============================================
# MIGRATION
# ============================================

DDL_TABLE = """
CREATE TABLE IF NOT EXISTS unverified_pdfs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_id        INTEGER NOT NULL UNIQUE,
    published_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    confirmed     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (pdf_id) REFERENCES pdfs(id) ON DELETE CASCADE
)
"""

DDL_IDX_CONFIRMED = (
    "CREATE INDEX IF NOT EXISTS idx_unverified_pdfs_confirmed "
    "ON unverified_pdfs(confirmed)"
)

DDL_IDX_PUBLISHED = (
    "CREATE INDEX IF NOT EXISTS idx_unverified_pdfs_published "
    "ON unverified_pdfs(published_at DESC)"
)


def open_db():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: database not found at {DB_PATH}")
        print("       Set NUUN_DB_PATH, or run from the platform root.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA synchronous = NORMAL")

    # Sanity check: is this actually the platform DB?
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='students'"
    )
    if cur.fetchone() is None:
        print(f"ERROR: no 'students' table in {DB_PATH}")
        print("       Is this the right database file?")
        conn.close()
        sys.exit(1)

    return conn


def table_exists(conn, name):
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def migrate(conn):
    existed_before = table_exists(conn, 'unverified_pdfs')

    if existed_before:
        print("  Table 'unverified_pdfs' already exists — nothing to do.")
        # Still ensure indexes exist (in case they were added separately)
        try:
            conn.execute(DDL_IDX_CONFIRMED)
            conn.execute(DDL_IDX_PUBLISHED)
            conn.commit()
            print("  Indexes verified.")
        except Exception as e:
            print(f"  Warning: could not verify indexes: {e}")
        return False

    print("  Creating table 'unverified_pdfs'…")
    conn.execute(DDL_TABLE)
    print("  Creating index idx_unverified_pdfs_confirmed…")
    conn.execute(DDL_IDX_CONFIRMED)
    print("  Creating index idx_unverified_pdfs_published…")
    conn.execute(DDL_IDX_PUBLISHED)
    conn.commit()

    print("  ✓ Migration applied.")
    return True


def main():
    print()
    print("=" * 62)
    print("  NuunPlatform — Migration: unverified_pdfs")
    print(f"  Database: {DB_PATH}")
    print("=" * 62)
    print()

    conn = open_db()
    try:
        changed = migrate(conn)

        # Post-check: confirm the table is queryable
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM unverified_pdfs"
            ).fetchone()
            count = row['cnt'] if row else 0
            print()
            print(f"  Current rows in unverified_pdfs: {count}")
        except Exception as e:
            print()
            print(f"  WARNING: could not read from unverified_pdfs: {e}")

        print()
        if changed:
            print("  Done. No restart required — the app picks up the")
            print("  new table on its next request.")
        else:
            print("  Done. Nothing changed.")
        print()
    finally:
        conn.close()


if __name__ == "__main__":
    main()