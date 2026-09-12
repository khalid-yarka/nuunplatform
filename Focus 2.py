#!/usr/bin/env python3
# migrate_focus_v2.py
# ---------------------------------------------------------------
# Adds `focus_bookmarks` feature and updates `focus_suggestions` pro cap to 30.
# Idempotent. Safe to run multiple times.
#
# Usage:
#   python migrate_focus_v2.py            # run
#   python migrate_focus_v2.py --dry-run  # preview only
# ---------------------------------------------------------------

import os
import sys
import sqlite3
import argparse
import shutil
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import Config


NEW_FEATURE = {
    'feature_key': 'focus_bookmarks',
    'display_name': 'Focus Bookmarks',
    'description': 'How many saved and liked questions appear on the Focus page.',
    'category': 'dashboard',
    'policy_type': 'quota',
    'unit_hint': 'items',
    'sort_order': 372,
    'notes': 'Combined quota for saved + liked questions shown on Focus.',
    'policies': {
        'free':    {'is_enabled': 1, 'limit_value': 10,  'limit_unit': 'items'},
        'premium': {'is_enabled': 1, 'limit_value': 50,  'limit_unit': 'items'},
        'pro':     {'is_enabled': 1, 'limit_value': 200, 'limit_unit': 'items'},
    },
}

# Update existing focus_suggestions: pro was null (unlimited) → 30
SUGGESTIONS_PRO_LIMIT = 30


def snapshot_db(db_path):
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    bak = f"{db_path}.bak.{ts}"
    shutil.copy2(db_path, bak)
    return bak


def feature_exists(conn, key):
    cursor = conn.execute(
        "SELECT id FROM entitlement_features WHERE feature_key = ?", (key,)
    )
    return cursor.fetchone() is not None


def seed_bookmarks_feature(conn, dry_run=False):
    key = NEW_FEATURE['feature_key']
    if feature_exists(conn, key):
        print(f"  [OK] Feature '{key}' already exists")
        return False
    print(f"  [+] Seeding feature '{key}'")
    if dry_run:
        return True

    cursor = conn.execute("""
        INSERT INTO entitlement_features
            (feature_key, display_name, description, category,
             policy_type, unit_hint, is_global_active, sort_order, notes)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
    """, (
        key,
        NEW_FEATURE['display_name'],
        NEW_FEATURE['description'],
        NEW_FEATURE['category'],
        NEW_FEATURE['policy_type'],
        NEW_FEATURE['unit_hint'],
        NEW_FEATURE['sort_order'],
        NEW_FEATURE['notes'],
    ))
    feature_id = cursor.lastrowid

    for tier in ('free', 'premium', 'pro'):
        p = NEW_FEATURE['policies'][tier]
        conn.execute("""
            INSERT INTO entitlement_policies
                (feature_id, tier, is_enabled, level_value, limit_value, limit_unit)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            feature_id, tier,
            1 if p.get('is_enabled') else 0,
            p.get('level_value'),
            p.get('limit_value'),
            p.get('limit_unit'),
        ))
    return True


def update_suggestions_pro(conn, dry_run=False):
    """Change focus_suggestions Pro limit from NULL (unlimited) to 30."""
    cursor = conn.execute("""
        SELECT ep.id, ep.limit_value
        FROM entitlement_policies ep
        JOIN entitlement_features ef ON ep.feature_id = ef.id
        WHERE ef.feature_key = 'focus_suggestions' AND ep.tier = 'pro'
    """)
    row = cursor.fetchone()
    if not row:
        print("  [SKIP] focus_suggestions/pro policy not found")
        return False
    if row['limit_value'] == SUGGESTIONS_PRO_LIMIT:
        print(f"  [OK] focus_suggestions/pro already = {SUGGESTIONS_PRO_LIMIT}")
        return False
    print(f"  [+] Updating focus_suggestions/pro: {row['limit_value']} → {SUGGESTIONS_PRO_LIMIT}")
    if dry_run:
        return True
    conn.execute(
        "UPDATE entitlement_policies SET limit_value = ?, updated_at = datetime('now','localtime') WHERE id = ?",
        (SUGGESTIONS_PRO_LIMIT, row['id'])
    )
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    db_path = Config.DATABASE_PATH
    print(f"Database: {db_path}")
    if not os.path.exists(db_path):
        print("ERROR: DB not found.")
        sys.exit(1)

    if not args.dry_run:
        bak = snapshot_db(db_path)
        print(f"Snapshot: {bak}")

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    print()
    print("=" * 60)
    print("FOCUS v2 MIGRATION")
    print("=" * 60)

    changed = False
    print("\n[1/2] Seeding focus_bookmarks")
    if seed_bookmarks_feature(conn, dry_run=args.dry_run):
        changed = True

    print("\n[2/2] Updating focus_suggestions Pro cap")
    if update_suggestions_pro(conn, dry_run=args.dry_run):
        changed = True

    if not args.dry_run:
        conn.commit()
        print("\nMigration complete." if changed else "\nNothing to do.")
    else:
        conn.rollback()
        print("\nDry run -- no changes written.")

    conn.close()


if __name__ == '__main__':
    main()