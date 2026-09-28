#!/usr/bin/env python3
# backfill_pdf_sizes.py
# ============================================================
# One-time script that populates the `file_size` column on the
# bot_data.db `pdfs` table by calling Telegram's getFile API.
#
# Usage:
#   python backfill_pdf_sizes.py                    # run normally
#   python backfill_pdf_sizes.py --dry-run          # preview only
#   python backfill_pdf_sizes.py --verify           # report stats only
#   python backfill_pdf_sizes.py --reset            # clear all sizes, redo
#   python backfill_pdf_sizes.py --limit 100        # cap the batch size
#   python backfill_pdf_sizes.py -v                 # verbose
# ============================================================

import os
import sys
import time
import shutil
import argparse
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / '.env')
except Exception:
    pass

from config import Config

# ------------------------------------------------------------
# CONSTANTS
# ------------------------------------------------------------
BOT_DB_PATH = Config.BOT_DATABASE_PATH
RATE_LIMIT_SLEEP = 0.15     # Telegram allows ~30 req/s; be polite
UNKNOWN_SIZE = -1           # sentinel: getFile failed (deleted / inaccessible)

log = logging.getLogger('backfill_pdf_sizes')


# ------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------
def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )


# ------------------------------------------------------------
# DB HELPERS
# ------------------------------------------------------------
def _get_conn():
    conn = sqlite3.connect(BOT_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _ensure_column():
    """Add file_size column if it doesn't exist."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(pdfs)")
        cols = {row[1] for row in cur.fetchall()}
        if 'file_size' not in cols:
            cur.execute("ALTER TABLE pdfs ADD COLUMN file_size INTEGER DEFAULT NULL")
            conn.commit()
            log.info("Added file_size column to bot pdfs table")
            return True
        return False
    finally:
        conn.close()


def _count_stats():
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM pdfs")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM pdfs WHERE file_size IS NULL")
        missing = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM pdfs WHERE file_size > 0")
        known = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM pdfs WHERE file_size = ?", (UNKNOWN_SIZE,))
        unknown = cur.fetchone()[0]
        return {'total': total, 'missing': missing, 'known': known, 'unknown': unknown}
    finally:
        conn.close()


def _list_missing(limit=None):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        sql = """
            SELECT id, code, title, file_id
            FROM pdfs
            WHERE file_size IS NULL
              AND file_id IS NOT NULL AND file_id != ''
            ORDER BY id ASC
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _set_size(pdf_id, size):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE pdfs SET file_size = ? WHERE id = ?",
                    (int(size), int(pdf_id)))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _reset_all():
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE pdfs SET file_size = NULL")
        n = cur.rowcount
        conn.commit()
        log.info(f"Reset file_size on {n} row(s)")
        return n
    finally:
        conn.close()


# ------------------------------------------------------------
# BACKUP
# ------------------------------------------------------------
def _backup_bot_db():
    if not os.path.exists(BOT_DB_PATH):
        log.error(f"Bot DB not found: {BOT_DB_PATH}")
        return None

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup_dir = os.path.join(Config.BACKUP_DIR, f'pre_pdf_sizes_{stamp}')
    os.makedirs(backup_dir, exist_ok=True)

    copied = []
    for suffix in ('', '-wal', '-shm'):
        src = BOT_DB_PATH + suffix
        if os.path.exists(src):
            dst = os.path.join(backup_dir, os.path.basename(src))
            shutil.copy2(src, dst)
            copied.append(dst)

    log.info(f"Backup written to {backup_dir} ({len(copied)} file(s))")
    return backup_dir


# ------------------------------------------------------------
# BOT
# ------------------------------------------------------------
def _get_bot():
    try:
        from bot.bot import get_bot
        return get_bot()
    except Exception as e:
        log.error(f"Could not initialize Telegram bot: {e}")
        return None


def _human_size(n):
    if n is None or n < 0:
        return '—'
    for unit in ('B', 'KB', 'MB', 'GB'):
        if abs(n) < 1024.0:
            return f'{n:3.1f} {unit}'
        n /= 1024.0
    return f'{n:.1f} TB'


# ------------------------------------------------------------
# MAIN FLOW
# ------------------------------------------------------------
def run_backfill(dry_run=False, limit=None):
    bot = _get_bot()
    if bot is None:
        return 1

    rows = _list_missing(limit=limit)
    total = len(rows)
    if total == 0:
        log.info("Nothing to backfill — every row already has a file_size")
        return 0

    log.info(f"Backfilling {total} row(s){' (dry-run)' if dry_run else ''}")
    ok, failed = 0, 0

    for i, row in enumerate(rows, 1):
        rid = row['id']
        code = row['code'] or '—'
        fid = row['file_id']

        try:
            info = bot.get_file(fid)
            size = getattr(info, 'file_size', None)
            if size and int(size) > 0:
                if not dry_run:
                    _set_size(rid, int(size))
                log.info(f"[{i}/{total}] #{rid} {code} → {_human_size(int(size))}")
                ok += 1
            else:
                if not dry_run:
                    _set_size(rid, UNKNOWN_SIZE)
                log.warning(f"[{i}/{total}] #{rid} {code} → no size reported")
                failed += 1
        except Exception as e:
            if not dry_run:
                _set_size(rid, UNKNOWN_SIZE)
            log.warning(f"[{i}/{total}] #{rid} {code} → getFile failed: {e}")
            failed += 1

        time.sleep(RATE_LIMIT_SLEEP)

    log.info(f"Done — {ok} succeeded, {failed} failed")
    return 0 if failed == 0 else 2


def run_verify():
    stats = _count_stats()
    print()
    print("=" * 56)
    print("  PDF SIZE BACKFILL — STATUS")
    print("=" * 56)
    print(f"  Total bot PDFs        : {stats['total']}")
    print(f"  Known size (>0)       : {stats['known']}")
    print(f"  Unknown size (-1)     : {stats['unknown']}")
    print(f"  Not yet processed     : {stats['missing']}")
    print("=" * 56)
    if stats['missing']:
        print("  → run `python backfill_pdf_sizes.py` to fill the rest")
    if stats['unknown']:
        print("  → unknown rows are files Telegram no longer serves")
    print()
    return 0


# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description='Backfill bot PDF file sizes')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview only, do not write to DB')
    parser.add_argument('--verify', action='store_true',
                        help='Report status, do not change anything')
    parser.add_argument('--reset', action='store_true',
                        help='Clear all file_size values, then backfill')
    parser.add_argument('--limit', type=int, default=None,
                        help='Max rows to process in this run')
    parser.add_argument('--no-backup', action='store_true',
                        help='Skip bot DB backup (not recommended)')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    setup_logging(args.verbose)

    if args.verify:
        return run_verify()

    if not os.path.exists(BOT_DB_PATH):
        log.error(f"Bot DB not found: {BOT_DB_PATH}")
        return 1

    added = _ensure_column()
    if added:
        log.info("Column added — proceeding")

    if args.reset and not args.dry_run:
        if not args.no_backup:
            _backup_bot_db()
        _reset_all()

    if not args.dry_run and not args.no_backup:
        _backup_bot_db()

    return run_backfill(dry_run=args.dry_run, limit=args.limit)


if __name__ == '__main__':
    sys.exit(main())