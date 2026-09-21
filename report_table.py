#!/usr/bin/env python3
"""
One-time migration: create the pdf_reports table + indexes.
Idempotent. Safe to run multiple times.

Usage:
    python3 migrate_pdf_reports
"""

import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    from config import Config
    DB = Config.DATABASE_PATH
except Exception:
    DB = os.path.join(HERE, 'nuunplatform.db')


DDL = """
CREATE TABLE IF NOT EXISTS pdf_reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    pdf_id       INTEGER NOT NULL,
    reason       TEXT NOT NULL CHECK (reason IN (
                     'wrong_file',
                     'wrong_metadata',
                     'broken_file',
                     'duplicate',
                     'inappropriate',
                     'other'
                 )),
    comment      TEXT DEFAULT '',
    status       TEXT DEFAULT 'pending' CHECK (status IN (
                     'pending', 'resolved', 'dismissed'
                 )),
    admin_reply  TEXT,
    resolved_by  INTEGER,
    resolved_at  TEXT,
    created_at   TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id)     REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (pdf_id)      REFERENCES pdfs(id) ON DELETE CASCADE,
    FOREIGN KEY (resolved_by) REFERENCES students(id) ON DELETE SET NULL
)
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_pdf_reports_status "
    "ON pdf_reports(status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_pdf_reports_pdf "
    "ON pdf_reports(pdf_id)",
    "CREATE INDEX IF NOT EXISTS idx_pdf_reports_user "
    "ON pdf_reports(user_id, created_at DESC)",
]


def main():
    print()
    print("=" * 60)
    print("  Create pdf_reports table")
    print("=" * 60)
    print()
    print(f"  DB: {DB}")
    print()

    if not os.path.exists(DB):
        print(f"  x Database not found: {DB}")
        return 1

    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")

    try:
        existed = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pdf_reports'"
        ).fetchone() is not None

        if existed:
            print("  Table already exists — verifying schema...")
        else:
            print("  Creating table...")

        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(DDL)
            for idx in INDEXES:
                conn.execute(idx)
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

        cols = [r['name'] for r in conn.execute("PRAGMA table_info(pdf_reports)")]
        expected = {'id', 'user_id', 'pdf_id', 'reason', 'comment',
                    'status', 'admin_reply', 'resolved_by',
                    'resolved_at', 'created_at'}
        missing = expected - set(cols)

        if missing:
            print(f"  x Missing columns: {sorted(missing)}")
            return 1

        print(f"  v Table present ({len(cols)} columns)")

        idxs = [r['name'] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='pdf_reports'"
        )]
        print(f"  v Indexes: {', '.join(idxs) or '(none)'}")

        n = conn.execute("SELECT COUNT(*) FROM pdf_reports").fetchone()[0]
        print(f"  v Rows: {n}")
        print()
        print("  Done.")
        print()
        return 0

    except Exception as e:
        print(f"  x Failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        conn.close()


if __name__ == '__main__':
    sys.exit(main())