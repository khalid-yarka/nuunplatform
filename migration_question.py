#!/usr/bin/env python3
# ============================================================
# migrate_live_quiz_grade.py
# One-time migration — run from repo root:
#     python migrate_live_quiz_grade.py
#
# No arguments. No prompts. Idempotent — safe to re-run.
# ============================================================

import os
import sys
import time
import shutil
import sqlite3
import logging
from datetime import datetime

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y/%m/%d %H:%M:%S',
)
log = logging.getLogger('migrate')


def _resolve_db_path():
    try:
        from config import Config
        return Config.DATABASE_PATH
    except Exception as e:
        log.error(f"Could not import config: {e}")
        sys.exit(1)


def _backup(db_path):
    backup_root = os.path.join(_ROOT, 'BACKUPS')
    stamp = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    dest = os.path.join(backup_root, f'pre_migration_live_quiz_{stamp}')
    try:
        os.makedirs(dest, exist_ok=True)
        for suffix in ('', '-wal', '-shm'):
            src = db_path + suffix
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(dest, os.path.basename(src)))
        log.info(f"Backup created: {dest}")
        return dest
    except Exception as e:
        log.warning(f"Backup failed (continuing anyway): {e}")
        return None


def main():
    start = time.time()
    log.info("=" * 60)
    log.info("NuunPlatform — Live Quiz Grade Migration")
    log.info("=" * 60)

    db_path = _resolve_db_path()
    if not os.path.exists(db_path):
        log.error(f"Database not found: {db_path}")
        sys.exit(1)
    log.info(f"Database: {db_path}")

    _backup(db_path)

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    cur = conn.cursor()

    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='live_quizzes'"
    )
    if not cur.fetchone():
        log.error("Table 'live_quizzes' does not exist. Aborting.")
        conn.close()
        sys.exit(1)

    cur.execute("PRAGMA table_info(live_quizzes)")
    existing_cols = {row[1] for row in cur.fetchall()}
    log.info(f"Existing columns: {sorted(existing_cols)}")

    if 'grade' not in existing_cols:
        try:
            cur.execute(
                "ALTER TABLE live_quizzes ADD COLUMN grade TEXT NOT NULL DEFAULT 'F4'"
            )
            conn.commit()
            log.info("Added column: live_quizzes.grade")
        except sqlite3.OperationalError as e:
            log.warning(f"Could not add grade column: {e}")
    else:
        log.info("Column 'grade' already present.")

    cur.execute(
        "UPDATE live_quizzes SET grade = 'F4' WHERE grade IS NULL OR grade = ''"
    )
    forced = cur.rowcount
    conn.commit()
    if forced:
        log.info(f"Grade forced to 'F4' on {forced} row(s).")
    else:
        log.info("No NULL/empty grade rows to fix.")

    for sql in [
        "CREATE INDEX IF NOT EXISTS idx_live_quizzes_grade "
        "ON live_quizzes(grade)",
    ]:
        try:
            cur.execute(sql)
        except sqlite3.OperationalError as e:
            log.warning(f"Index skipped: {e}")
    conn.commit()
    log.info("Index ready.")

    cur.execute("PRAGMA table_info(live_quizzes)")
    final_cols = {row[1] for row in cur.fetchall()}
    if 'grade' not in final_cols:
        log.error("VERIFY FAILED: column 'grade' missing after migration.")
        conn.close()
        sys.exit(1)

    cur.execute(
        "SELECT COUNT(*) FROM live_quizzes WHERE grade IS NULL OR grade = ''"
    )
    ungraded = cur.fetchone()[0]
    if ungraded:
        log.error(f"VERIFY FAILED: {ungraded} rows still lack a grade.")
        conn.close()
        sys.exit(1)

    cur.execute(
        "SELECT grade, COUNT(*) AS c FROM live_quizzes GROUP BY grade ORDER BY grade"
    )
    log.info("Grade distribution:")
    rows = cur.fetchall()
    if rows:
        for row in rows:
            log.info(f"  {row['grade']}: {row['c']} quizzes")
    else:
        log.info("  (no live quizzes yet)")

    conn.close()

    elapsed = round(time.time() - start, 2)
    log.info("=" * 60)
    log.info(f"MIGRATION COMPLETE — {elapsed}s")
    log.info("=" * 60)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log.error(f"Migration failed: {e}", exc_info=True)
        sys.exit(1)