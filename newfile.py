#!/usr/bin/env python3
"""
One-time migration: add `original_filename` column to bot_data.db pdfs table.

Run once:
    python migrate_bot_pdf_original.py

Safe to re-run — it checks for the column first.
"""

import os
import sys
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from config import Config


def main():
    db_path = Config.BOT_DATABASE_PATH

    if not os.path.isabs(db_path):
        db_path = str(BASE_DIR / db_path)

    print(f"Bot DB: {db_path}")

    if not os.path.exists(db_path):
        print("❌ Bot database not found. Nothing to migrate.")
        return 1

    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    try:
        cursor.execute("PRAGMA table_info(pdfs)")
        cols = {row[1] for row in cursor.fetchall()}

        if 'original_filename' in cols:
            print("✅ Column already exists. Nothing to do.")
            return 0

        print("Adding column `original_filename` …")
        cursor.execute(
            "ALTER TABLE pdfs ADD COLUMN original_filename TEXT DEFAULT ''"
        )
        conn.commit()

        cursor.execute("PRAGMA table_info(pdfs)")
        cols = {row[1] for row in cursor.fetchall()}
        if 'original_filename' in cols:
            print("✅ Migration complete.")
            return 0
        else:
            print("❌ Column still missing after ALTER. Something went wrong.")
            return 1

    except Exception as e:
        print(f"❌ Migration failed: {e}")
        return 1
    finally:
        conn.close()


if __name__ == '__main__':
    sys.exit(main())