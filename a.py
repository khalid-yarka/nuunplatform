#!/usr/bin/env python3
"""
migrate_pdf_events.py
=====================
Idempotent migration that creates the pdf_events table
plus its indexes. Safe to run multiple times.

Run:  python migrate_pdf_events.py
"""

import os
import sqlite3
import sys

DB_PATH = os.environ.get("NUUN_DB", "nuunplatform.db")


DDL_TABLE = """
CREATE TABLE IF NOT EXISTS pdf_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_id            INTEGER,
    pdf_code          TEXT,
    pdf_title         TEXT,
    event_type        TEXT NOT NULL,
    event_category    TEXT NOT NULL,
    actor_id          INTEGER,
    actor_public_id   TEXT,
    actor_name        TEXT,
    actor_role        TEXT,
    source            TEXT,
    ip_address        TEXT,
    user_agent        TEXT,
    metadata          TEXT,
    created_at        TEXT DEFAULT (datetime('now', 'localtime'))
);
"""

DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_pdf_events_pdf     ON pdf_events(pdf_id, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_pdf_events_code    ON pdf_events(pdf_code, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_pdf_events_actor   ON pdf_events(actor_id, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_pdf_events_type    ON pdf_events(event_type, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_pdf_events_created ON pdf_events(created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_pdf_events_cat     ON pdf_events(event_category, created_at DESC);",
]


def main():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: database not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pdf_events'")
    exists = cur.fetchone() is not None

    if exists:
        print("· pdf_events already exists — skipping table create")
    else:
        cur.execute(DDL_TABLE)
        conn.commit()
        print("✓ created table: pdf_events")

    for ddl in DDL_INDEXES:
        cur.execute(ddl)
    conn.commit()
    print(f"✓ ensured {len(DDL_INDEXES)} indexes on pdf_events")

    cur.execute("PRAGMA table_info(pdf_events)")
    cols = [r[1] for r in cur.fetchall()]
    print(f"  columns: {cols}")

    try:
        cur.execute("SELECT COUNT(*) FROM pdf_events")
        row = cur.fetchone()
        print(f"  current rows: {row[0]}")
    except Exception as e:
        print(f"  ! could not count rows: {e}")

    conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    main()