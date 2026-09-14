#!/usr/bin/env python3
"""
grant_capabilities.py — Grant admin capabilities globally.

The modular admin system stores capability defaults in
services/admin/registry.py. Capabilities marked default_for_admin=False
(e.g. upgrades.view) are NOT granted to regular admins on first boot.
This script grants a chosen list of them globally.

Usage (from project root):
    python grant_capabilities.py

After running, restart the Flask app so the cache is reloaded.
(Safe to run repeatedly — it is idempotent.)
"""

import os
import sys
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from config import Config


# ------------------------------------------------------------------
# Capabilities to grant globally (to every admin).
# Every key here must exist in services/admin/registry.py.
# ------------------------------------------------------------------
CAPS_TO_GRANT = [
    # Upgrades (this is what unblocks /upgrade/admin/upgrade-requests)
    'upgrades.view',
    'upgrades.approve',
    'upgrades.reject',
    'upgrades.export',
    'upgrades.bulk',

    # Discount codes (linked from the same sidebar section)
    'discounts.view',
    'discounts.create',
    'discounts.edit',
    'discounts.delete',

    # Diagnostics that regular admins commonly need
    'errors.view',
    'errors.resolve',
    'errors.dismiss',
    'backups.view',
    'backups.create',
    'logs.view',
    'system.info',

    # Governance (read-only)
    'audit.view',
    'entitlements.read',
]


def main():
    db_path = Config.DATABASE_PATH
    if not os.path.exists(db_path):
        print(f"❌ Database not found: {db_path}")
        sys.exit(1)

    # Validate keys against the registry so typos are caught here,
    # not silently ignored in the DB.
    try:
        from services.admin.registry import REGISTRY_KEYS
    except Exception as e:
        print(f"❌ Could not import capability registry: {e}")
        sys.exit(1)

    unknown = [k for k in CAPS_TO_GRANT if k not in REGISTRY_KEYS]
    if unknown:
        print("❌ These keys are not in the registry:")
        for k in unknown:
            print(f"     {k}")
        sys.exit(1)

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    # Sanity-check that the tables exist
    try:
        conn.execute("SELECT 1 FROM admin_capability_grants LIMIT 1")
        conn.execute("SELECT 1 FROM admin_capability_version LIMIT 1")
    except sqlite3.OperationalError:
        print("❌ Capability tables not found.")
        print("   Run `python migrate.py` first, then re-run this script.")
        conn.close()
        sys.exit(1)

    print(f"Granting {len(CAPS_TO_GRANT)} capabilities globally...")
    print()

    changed = 0
    for key in CAPS_TO_GRANT:
        row = conn.execute(
            "SELECT is_enabled FROM admin_capability_grants "
            "WHERE capability_key = ?",
            (key,),
        ).fetchone()

        if row is None:
            conn.execute("""
                INSERT INTO admin_capability_grants
                    (capability_key, is_enabled, updated_at)
                VALUES (?, 1, datetime('now', 'localtime'))
            """, (key,))
            print(f"  + {key}")
            changed += 1
        elif not row['is_enabled']:
            conn.execute("""
                UPDATE admin_capability_grants
                SET is_enabled = 1,
                    updated_at = datetime('now', 'localtime')
                WHERE capability_key = ?
            """, (key,))
            print(f"  ↑ {key}  (was disabled → enabled)")
            changed += 1
        else:
            print(f"  = {key}  (already enabled)")

    # Bump the version so the running process picks up the change
    # on the next request (no restart strictly required, but recommended).
    conn.execute("""
        UPDATE admin_capability_version
        SET version = version + 1
        WHERE id = 1
    """)
    conn.commit()
    conn.close()

    print()
    print(f"✅ Done. {changed} capability(ies) changed.")
    print("   Restart the Flask app for an immediate effect.")
    print("   (Otherwise the change is picked up within ~5 minutes.)")


if __name__ == '__main__':
    main()