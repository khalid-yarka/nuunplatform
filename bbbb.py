#!/usr/bin/env python3
# migrate_1b.py
# ---------------------------------------------------------------
# One-shot data migration: normalize legacy tier vocabulary.
#
#   danbe → free
#   dhexe → premium
#   hore  → pro
#
# Runs on:
#   students.tier
#   groups.tier_required
#   achievements.tier_required
#   discount_codes.applies_to
#   upgrade_requests.requested_tier
#
# Safe:
#   - Makes a .bak snapshot of the DB before touching anything
#   - Idempotent — second run is a no-op
#   - Prints counts before and after
#   - Verifies each UPDATE actually hit the expected rows
#
# Usage:
#   python migrate_1b.py            # run migration
#   python migrate_1b.py --dry-run  # show what would change, no writes
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


# ---------------------------------------------------------------
# Mapping table: which column in which table needs renaming.
# ---------------------------------------------------------------
MIGRATIONS = [
    {
        'table': 'students',
        'column': 'tier',
        'label': 'Students',
    },
    {
        'table': 'groups',
        'column': 'tier_required',
        'label': 'Groups (tier_required)',
    },
    {
        'table': 'achievements',
        'column': 'tier_required',
        'label': 'Achievements (tier_required)',
    },
    {
        'table': 'discount_codes',
        'column': 'applies_to',
        'label': 'Discount codes (applies_to)',
    },
    {
        'table': 'upgrade_requests',
        'column': 'requested_tier',
        'label': 'Upgrade requests (requested_tier)',
    },
]

LEGACY_VALUES = ('danbe', 'dhexe', 'hore')


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------
def snapshot_db(db_path: str) -> str:
    """Create a timestamped .bak copy of the database."""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    bak_path = f"{db_path}.bak.{ts}"
    shutil.copy2(db_path, bak_path)
    return bak_path


def count_legacy(conn, table: str, column: str) -> dict:
    """Return counts per legacy value for a given table.column."""
    counts = {}
    try:
        cursor = conn.execute(
            f"SELECT {column}, COUNT(*) AS c FROM {table} "
            f"WHERE {column} IN (?, ?, ?) "
            f"GROUP BY {column}",
            LEGACY_VALUES
        )
        for row in cursor.fetchall():
            counts[row[0]] = row[1]
    except sqlite3.OperationalError as e:
        counts['__error__'] = str(e)
    return counts


def run_migration(conn, table: str, column: str, dry_run: bool = False) -> int:
    """Run the CASE UPDATE for a single table.column.
    Returns the number of rows that were updated."""
    sql = f"""
        UPDATE {table}
        SET {column} = CASE {column}
            WHEN 'danbe' THEN 'free'
            WHEN 'dhexe' THEN 'premium'
            WHEN 'hore'  THEN 'pro'
            ELSE {column}
        END
        WHERE {column} IN ('danbe', 'dhexe', 'hore')
    """
    if dry_run:
        cursor = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} IN (?, ?, ?)",
            LEGACY_VALUES
        )
        return cursor.fetchone()[0]

    cursor = conn.execute(sql)
    return cursor.rowcount


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description='Phase 1b: normalize legacy tier vocabulary in the DB.'
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would change without writing.')
    args = parser.parse_args()

    db_path = Config.DATABASE_PATH
    print(f"📁 Database: {db_path}")

    if not os.path.exists(db_path):
        print("❌ Database file not found.")
        sys.exit(1)

    # ---- Snapshot ----
    if not args.dry_run:
        bak = snapshot_db(db_path)
        print(f"💾 Snapshot created: {bak}")
    else:
        print("🔍 DRY RUN — no snapshot created.")

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    # ---- Report + migrate ----
    print()
    print("=" * 70)
    print("PHASE 1B — TIER VOCABULARY NORMALIZATION")
    print("=" * 70)

    total_updated = 0

    for m in MIGRATIONS:
        table = m['table']
        column = m['column']
        label = m['label']

        before = count_legacy(conn, table, column)
        if '__error__' in before:
            print(f"⚠️  {label}: skipped ({before['__error__']})")
            continue

        legacy_total = sum(before.values())
        if legacy_total == 0:
            print(f"✅ {label}: already clean (0 legacy rows)")
            continue

        print(f"📋 {label}: {legacy_total} legacy row(s)")
        for value, count in sorted(before.items()):
            print(f"     - {value}: {count}")

        try:
            updated = run_migration(conn, table, column, dry_run=args.dry_run)
            total_updated += updated
            if args.dry_run:
                print(f"   → would update {updated} row(s)")
            else:
                print(f"   → updated {updated} row(s)")
        except sqlite3.OperationalError as e:
            print(f"   ❌ Failed: {e}")
            if not args.dry_run:
                conn.rollback()
            continue

    # ---- Commit ----
    if not args.dry_run:
        conn.commit()
        print()
        print("=" * 70)
        print(f"✅ Migration complete. {total_updated} row(s) updated.")
        print("=" * 70)

        # ---- Verify ----
        print()
        print("VERIFICATION (should all be 0):")
        all_clean = True
        for m in MIGRATIONS:
            after = count_legacy(conn, m['table'], m['column'])
            if '__error__' in after:
                continue
            remaining = sum(after.values())
            icon = "✅" if remaining == 0 else "❌"
            print(f"  {icon} {m['label']}: {remaining} legacy row(s) remaining")
            if remaining > 0:
                all_clean = False

        # ---- Show final tier values ----
        print()
        print("VALID VALUES IN students.tier (should only be free/premium/pro):")
        cursor = conn.execute(
            "SELECT tier, COUNT(*) FROM students GROUP BY tier ORDER BY tier"
        )
        for row in cursor.fetchall():
            print(f"  - {row[0] or '(null)'}: {row[1]}")

        conn.close()

        if all_clean:
            print()
            print("🎉 All tables normalized. Safe to commit.")
            print(f"   Rollback available via: cp {bak} {db_path}")
        else:
            print()
            print("⚠️  Some rows still have legacy values. Investigate before proceeding.")
            sys.exit(2)
    else:
        conn.rollback()
        conn.close()
        print()
        print("🔍 DRY RUN complete. No changes written.")


if __name__ == '__main__':
    main()