#!/usr/bin/env python3
"""
One-shot migration: add the live_quiz_chat_send entitlement feature.

Idempotent. Does NOT touch existing features, policies, or overrides.

    python migrate_add_chat_feature.py
    python migrate_add_chat_feature.py --dry-run
"""
import sys
import sqlite3

from config import Config


FEATURE = {
    "feature_key": "live_quiz_chat_send",
    "display_name": "Send messages in waiting-room chat",
    "description": "Allow a user to SEND messages in the live-quiz "
                   "waiting room. Reading is always allowed; this gates "
                   "sending only.",
    "category": "Live Quiz",
    "policy_type": "permission",
    "unit_hint": None,
    "sort_order": 50,
    "notes": "Default ON for both tiers. Flip Free off to make chat "
             "read-only for free users.",
}

DRY_RUN = '--dry-run' in sys.argv


def main():
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row

    row = conn.execute(
        "SELECT id FROM entitlement_features WHERE feature_key = ?",
        (FEATURE['feature_key'],)
    ).fetchone()
    if row:
        print(f"Feature '{FEATURE['feature_key']}' already exists "
              f"(id={row['id']}). Nothing to do.")
        conn.close()
        return 0

    print(f"Adding feature '{FEATURE['feature_key']}'...")
    if DRY_RUN:
        print("  [dry-run] would INSERT into entitlement_features")
        print("  [dry-run] would INSERT policies for tiers: free, premium")
        conn.close()
        return 0

    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO entitlement_features
            (feature_key, display_name, description, category,
             policy_type, unit_hint, is_global_active, sort_order, notes)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
    """, (
        FEATURE['feature_key'],
        FEATURE['display_name'],
        FEATURE['description'],
        FEATURE['category'],
        FEATURE['policy_type'],
        FEATURE['unit_hint'],
        FEATURE['sort_order'],
        FEATURE['notes'],
    ))
    feature_id = cursor.lastrowid

    for tier in ('free', 'premium'):
        cursor.execute("""
            INSERT INTO entitlement_policies
                (feature_id, tier, is_enabled, level_value,
                 limit_value, limit_unit)
            VALUES (?, ?, 1, NULL, NULL, NULL)
        """, (feature_id, tier))

    conn.commit()
    conn.close()

    print(f"Done. Feature id={feature_id}, both tiers enabled by default.")
    print("Restart the Flask worker to pick up the new entitlement policy.")
    return 0


if __name__ == '__main__':
    sys.exit(main())