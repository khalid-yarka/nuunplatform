#!/usr/bin/env python3
# ============================================================
# migrate_batches.py
# One-time migration — run from repo root:
#     python migrate_batches.py
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
    dest = os.path.join(backup_root, f'pre_migration_batches_{stamp}')
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
    log.info("NuunPlatform — Content Batches Migration")
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

    # ─── content_batches ──────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS content_batches (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            kind        TEXT NOT NULL CHECK (kind IN ('questions','pdfs','mixed')),
            admin_id    INTEGER,
            notes       TEXT DEFAULT '',
            pinned      INTEGER NOT NULL DEFAULT 0,
            item_count  INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at  TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (admin_id) REFERENCES students(id) ON DELETE SET NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_batches_admin ON content_batches(admin_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_batches_kind ON content_batches(kind)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_batches_updated ON content_batches(updated_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_batches_pinned ON content_batches(pinned DESC, updated_at DESC)")
    log.info("content_batches ready.")

    # ─── content_batch_items ──────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS content_batch_items (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id   INTEGER NOT NULL,
            item_type  TEXT NOT NULL CHECK (item_type IN ('question','pdf')),
            item_id    INTEGER NOT NULL,
            added_at   TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(batch_id, item_type, item_id),
            FOREIGN KEY (batch_id) REFERENCES content_batches(id) ON DELETE CASCADE
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_batch_items_batch ON content_batch_items(batch_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_batch_items_lookup ON content_batch_items(item_type, item_id)")
    log.info("content_batch_items ready.")

    # ─── content_batch_edits (undo snapshots) ─────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS content_batch_edits (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id     INTEGER NOT NULL,
            admin_id     INTEGER,
            action       TEXT NOT NULL,
            item_type    TEXT NOT NULL,
            item_ids     TEXT NOT NULL,
            before_data  TEXT NOT NULL,
            undo_until   TEXT NOT NULL,
            undone       INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (batch_id) REFERENCES content_batches(id) ON DELETE CASCADE,
            FOREIGN KEY (admin_id) REFERENCES students(id) ON DELETE SET NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_batch_edits_batch ON content_batch_edits(batch_id, created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_batch_edits_undo_until ON content_batch_edits(undo_until)")
    log.info("content_batch_edits ready.")

    conn.commit()

    # ─── Verify ───────────────────────────────────────────
    for t in ('content_batches', 'content_batch_items', 'content_batch_edits'):
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (t,),
        )
        if not cur.fetchone():
            log.error(f"VERIFY FAILED: table '{t}' missing after migration.")
            conn.close()
            sys.exit(1)

    cur.execute("SELECT COUNT(*) FROM content_batches")
    log.info(f"Existing batches: {cur.fetchone()[0]}")

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