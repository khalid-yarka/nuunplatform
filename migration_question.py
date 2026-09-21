#!/usr/bin/env python3
# ============================================================
# migrate_question_grade.py
# One-time migration — run from repo root:
#     python migrate_question_grade.py
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
    dest = os.path.join(backup_root, f'pre_migration_{stamp}')
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


def _normalize(text):
    import re
    if not text:
        return ''
    text = re.sub(r'[\u064B-\u065F\u0670]', '', text)
    text = text.lower()
    text = re.sub(r'[^\w\s\u0600-\u06FF]', ' ', text, flags=re.UNICODE)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _hash(text):
    import hashlib
    return hashlib.sha256(_normalize(text).encode('utf-8')).hexdigest()[:32]


def main():
    start = time.time()
    log.info("=" * 60)
    log.info("NuunPlatform — Question Grade Migration")
    log.info("=" * 60)

    db_path = _resolve_db_path()
    if not os.path.exists(db_path):
        log.error(f"Database not found: {db_path}")
        sys.exit(1)
    log.info(f"Database: {db_path}")

    # ─── 1. Backup ─────────────────────────────────────────
    _backup(db_path)

    # ─── 2. Open ────────────────────────────────────────────
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    cur = conn.cursor()

    # ─── 3. Verify questions table ──────────────────────────
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='questions'")
    if not cur.fetchone():
        log.error("Table 'questions' does not exist. Aborting.")
        conn.close()
        sys.exit(1)

    cur.execute("PRAGMA table_info(questions)")
    existing_cols = {row[1] for row in cur.fetchall()}
    log.info(f"Existing columns: {sorted(existing_cols)}")

    # ─── 4. Add columns ─────────────────────────────────────
    added_cols = []
    for name, ddl in [
        ('grade',                    "TEXT NOT NULL DEFAULT 'F4'"),
        ('question_text_normalized', "TEXT"),
        ('question_hash',            "TEXT"),
    ]:
        if name not in existing_cols:
            try:
                cur.execute(f"ALTER TABLE questions ADD COLUMN {name} {ddl}")
                log.info(f"  + Added column: {name}")
                added_cols.append(name)
            except sqlite3.OperationalError as e:
                log.warning(f"  ! Could not add {name}: {e}")

    if added_cols:
        conn.commit()
        log.info(f"Columns added: {added_cols}")
    else:
        log.info("Columns already present.")

    # ─── 5. Force every existing question to F4 ─────────────
    # This is the explicit guarantee you asked for. If the column
    # was just added, SQLite already filled it. But this also
    # catches rows where grade might have been left NULL/empty by
    # some earlier manual edit.
    cur.execute("""
        UPDATE questions
        SET grade = 'F4'
        WHERE grade IS NULL OR grade = ''
    """)
    forced = cur.rowcount
    conn.commit()
    log.info(f"Grade forced to 'F4' on {forced} row(s) (NULL/empty only).")

    # ─── 6. Backfill hashes ─────────────────────────────────
    cur.execute("""
        SELECT id, question_text
        FROM questions
        WHERE question_hash IS NULL
           OR question_hash = ''
           OR question_text_normalized IS NULL
    """)
    rows = cur.fetchall()
    total = len(rows)
    log.info(f"Rows to backfill hash: {total}")

    if total:
        BATCH = 500
        done = 0
        for i in range(0, total, BATCH):
            batch = rows[i:i + BATCH]
            params = [
                (_normalize(r['question_text'] or ''),
                 _hash(r['question_text'] or ''),
                 r['id'])
                for r in batch
            ]
            cur.executemany(
                "UPDATE questions "
                "SET question_text_normalized = ?, question_hash = ? "
                "WHERE id = ?",
                params,
            )
            conn.commit()
            done += len(batch)
            log.info(f"  Backfilled {done}/{total}")
        log.info(f"Backfill complete: {done} rows")
    else:
        log.info("No hash backfill needed.")

    # ─── 7. Dismissals table ────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS question_duplicate_dismissals (
            a_id         INTEGER NOT NULL,
            b_id         INTEGER NOT NULL,
            dismissed_by INTEGER,
            dismissed_at TEXT DEFAULT (datetime('now', 'localtime')),
            PRIMARY KEY (a_id, b_id),
            CHECK (a_id < b_id)
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_qdd_b
        ON question_duplicate_dismissals(b_id)
    """)
    conn.commit()
    log.info("Dismissals table ready.")

    # ─── 8. Indexes ─────────────────────────────────────────
    for sql in [
        "CREATE INDEX IF NOT EXISTS idx_questions_grade "
        "ON questions(grade)",
        "CREATE INDEX IF NOT EXISTS idx_questions_hash "
        "ON questions(question_hash)",
        "CREATE INDEX IF NOT EXISTS idx_questions_grade_subject "
        "ON questions(grade, subject_code, status)",
    ]:
        try:
            cur.execute(sql)
        except sqlite3.OperationalError as e:
            log.warning(f"Index skipped: {e}")
    conn.commit()
    log.info("Indexes ready.")

    # ─── 9. Verify ──────────────────────────────────────────
    cur.execute("PRAGMA table_info(questions)")
    final_cols = {row[1] for row in cur.fetchall()}
    for col in ('grade', 'question_text_normalized', 'question_hash'):
        if col not in final_cols:
            log.error(f"VERIFY FAILED: column '{col}' missing after migration.")
            conn.close()
            sys.exit(1)

    cur.execute("SELECT COUNT(*) FROM questions WHERE question_hash IS NULL OR question_hash = ''")
    unhashed = cur.fetchone()[0]
    if unhashed:
        log.error(f"VERIFY FAILED: {unhashed} rows still lack a hash.")
        conn.close()
        sys.exit(1)

    cur.execute("SELECT COUNT(*) FROM questions WHERE grade IS NULL OR grade = ''")
    ungraded = cur.fetchone()[0]
    if ungraded:
        log.error(f"VERIFY FAILED: {ungraded} rows still lack a grade.")
        conn.close()
        sys.exit(1)

    cur.execute("SELECT grade, COUNT(*) AS c FROM questions GROUP BY grade ORDER BY grade")
    log.info("Grade distribution:")
    for row in cur.fetchall():
        log.info(f"  {row['grade']}: {row['c']} questions")

    cur.execute("SELECT COUNT(*) FROM question_duplicate_dismissals")
    log.info(f"Dismissals table rows: {cur.fetchone()[0]}")

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