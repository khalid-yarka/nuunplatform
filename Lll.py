#!/usr/bin/env python3
# migrate_user_usage_keys.py
# ---------------------------------------------------------------
# One-shot data migration: normalize user_usage.metric_code
# vocabulary to match the entitlement feature keys.
#
#   quiz_attempt       → quiz_attempts
#   resource_download  → resource_downloads
#
# Context:
#   services/entitlement_service.py uses canonical feature keys from
#   entitlements_seed.json as metric_codes. Historical rows written by
#   the old tier_service used the shorter legacy names. Without this
#   migration, every user's used-count reads as 0 the first time the
#   entitlement service consults it — silently granting a fresh quota.
#
# Safe:
#   - Snapshot .bak of the DB before touching anything
#   - Idempotent — second run is a no-op
#   - Refuses to migrate when old and new rows would collide on the
#     (user_id, metric_code, period_start) unique index
#   - Prints counts before and after, verifies zero legacy rows remain
#
# Usage:
#   python migrate_user_usage_keys.py            # run
#   python migrate_user_usage_keys.py --dry-run  # preview only
# ---------------------------------------------------------------

import os
import sys
import shutil
import sqlite3
import argparse
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import Config


MIGRATIONS = [
    ('quiz_attempt',      'quiz_attempts'),
    ('resource_download', 'resource_downloads'),
]


def snapshot_db(db_path: str) -> str:
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    bak = f"{db_path}.bak.{ts}"
    shutil.copy2(db_path, bak)
    return bak


def count_metric(conn, metric_code: str) -> int:
    try:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM user_usage WHERE metric_code = ?",
            (metric_code,)
        )
        return cursor.fetchone()[0]
    except sqlite3.OperationalError as e:
        print(f"⚠️  count failed for {metric_code}: {e}")
        return -1


def has_conflict(conn, old_code: str, new_code: str) -> bool:
    """
    Detect rows that would collide on the unique index
    (user_id, metric_code, period_start) after the rename.
    """
    cursor = conn.execute("""
        SELECT COUNT(*)
        FROM user_usage old_u
        JOIN user_usage new_u
          ON old_u.user_id      = new_u.user_id
         AND old_u.period_start = new_u.period_start
        WHERE old_u.metric_code = ?
          AND new_u.metric_code = ?
    """, (old_code, new_code))
    return cursor.fetchone()[0] > 0


def run_migration(conn, old_code: str, new_code: str, dry_run: bool) -> int:
    if dry_run:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM user_usage WHERE metric_code = ?",
            (old_code,)
        )
        return cursor.fetchone()[0]

    cursor = conn.execute(
        "UPDATE user_usage SET metric_code = ? WHERE metric_code = ?",
        (new_code, old_code)
    )
    return cursor.rowcount


def main():
    parser = argparse.ArgumentParser(
        description='Normalize user_usage.metric_code to canonical feature keys.'
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would change without writing.')
    args = parser.parse_args()

    db_path = Config.DATABASE_PATH
    print(f"📁 Database: {db_path}")

    if not os.path.exists(db_path):
        print("❌ Database file not found.")
        sys.exit(1)

    if not args.dry_run:
        bak = snapshot_db(db_path)
        print(f"💾 Snapshot created: {bak}")
    else:
        print("🔍 DRY RUN — no snapshot created.")

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    print()
    print("=" * 70)
    print("MIGRATE — user_usage.metric_code → entitlement feature keys")
    print("=" * 70)

    total_updated = 0
    any_conflict = False

    for old_code, new_code in MIGRATIONS:
        old_count = count_metric(conn, old_code)
        new_count = count_metric(conn, new_code)

        if old_count < 0:
            print(f"⚠️  {old_code}: skipped (query error)")
            continue

        if old_count == 0:
            print(f"✅ {old_code}: nothing to migrate "
                  f"({new_count} row(s) already under {new_code})")
            continue

        print(f"📋 {old_code} → {new_code}")
        print(f"     legacy rows: {old_count}")
        print(f"     target rows: {new_count}")

        if has_conflict(conn, old_code, new_code):
            any_conflict = True
            print(f"     ❌ CONFLICT: some users already have rows under "
                  f"both names for the same period.")
            print(f"     Refusing to migrate — resolve manually.")
            continue

        try:
            updated = run_migration(conn, old_code, new_code, dry_run=args.dry_run)
            total_updated += updated
            if args.dry_run:
                print(f"     → would update {updated} row(s)")
            else:
                print(f"     → updated {updated} row(s)")
        except sqlite3.OperationalError as e:
            print(f"     ❌ Failed: {e}")
            if not args.dry_run:
                conn.rollback()
            continue

    if not args.dry_run:
        conn.commit()
        print()
        print("=" * 70)
        print(f"✅ Migration complete. {total_updated} row(s) updated.")
        print("=" * 70)

        print()
        print("VERIFICATION (legacy counts should all be 0):")
        for old_code, new_code in MIGRATIONS:
            remaining = count_metric(conn, old_code)
            target = count_metric(conn, new_code)
            icon = "✅" if remaining == 0 else "❌"
            print(f"  {icon} {old_code}: {remaining} legacy remaining "
                  f"({target} under {new_code})")

        print()
        print("CURRENT metric_code VALUES:")
        cursor = conn.execute(
            "SELECT metric_code, COUNT(*) FROM user_usage "
            "GROUP BY metric_code ORDER BY metric_code"
        )
        for row in cursor.fetchall():
            print(f"  - {row[0]}: {row[1]}")

        conn.close()

        if any_conflict:
            print()
            print("⚠️  At least one mapping had conflicting rows. "
                  "Investigate before proceeding.")
            sys.exit(2)
        else:
            print()
            print("🎉 Safe to proceed.")
            print(f"   Rollback: cp {bak} {db_path}")
    else:
        conn.rollback()
        conn.close()
        print()
        print("🔍 DRY RUN complete. No changes written.")


if __name__ == '__main__':
    main()