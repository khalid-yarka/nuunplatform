#!/usr/bin/env python3
"""
migrate_groups_location.py
==========================
Idempotent migration for the Study Groups schema change.

Changes applied:
  1. Rename  groups.curriculum       → groups.location
     (values were already location codes SO/PL/SL — pure rename)
  2. Add     groups.stream           TEXT NOT NULL DEFAULT ''
     (stream restriction, only meaningful when location = 'PL')
  3. Add     groups.is_visible       INTEGER NOT NULL DEFAULT 1
     (referenced by templates, was missing from the DB)
  4. Add     groups.requires_verified INTEGER NOT NULL DEFAULT 0
     (referenced by templates, was missing from the DB)

Safe to run multiple times. Opens DB in read-write mode, uses
explicit column inspection to skip already-applied steps.
"""

import os
import sqlite3
import sys

DB_PATH = os.environ.get("NUUN_DB", "nuunplatform.db")


def main():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: database not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # ── inspect current schema ──────────────────────────────
    cur.execute("PRAGMA table_info(groups)")
    cols = {row[1] for row in cur.fetchall()}
    print(f"Current groups columns: {sorted(cols)}")

    # ── 1. rename curriculum → location ────────────────────
    if "location" in cols:
        print("· groups.location already exists — skipping rename")
    elif "curriculum" in cols:
        cur.execute("ALTER TABLE groups RENAME COLUMN curriculum TO location")
        conn.commit()
        print("✓ renamed: groups.curriculum → groups.location")
    else:
        print("! neither 'curriculum' nor 'location' found — check DB",
              file=sys.stderr)
        sys.exit(2)

    # Re-read column list after potential rename
    cur.execute("PRAGMA table_info(groups)")
    cols = {row[1] for row in cur.fetchall()}

    # ── 2. add stream column ───────────────────────────────
    if "stream" in cols:
        print("· groups.stream already exists — skipping")
    else:
        cur.execute(
            "ALTER TABLE groups ADD COLUMN stream TEXT NOT NULL DEFAULT ''"
        )
        conn.commit()
        print("✓ added column: groups.stream")

    # ── 3. add is_visible ──────────────────────────────────
    if "is_visible" in cols:
        print("· groups.is_visible already exists — skipping")
    else:
        cur.execute(
            "ALTER TABLE groups ADD COLUMN is_visible "
            "INTEGER NOT NULL DEFAULT 1"
        )
        conn.commit()
        print("✓ added column: groups.is_visible")

    # ── 4. add requires_verified ───────────────────────────
    if "requires_verified" in cols:
        print("· groups.requires_verified already exists — skipping")
    else:
        cur.execute(
            "ALTER TABLE groups ADD COLUMN requires_verified "
            "INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()
        print("✓ added column: groups.requires_verified")

    # ── verify ─────────────────────────────────────────────
    cur.execute("PRAGMA table_info(groups)")
    final_cols = [row[1] for row in cur.fetchall()]
    print(f"\nFinal groups columns: {final_cols}")

    # ── sanity: print group rows with new columns ──────────
    print("\nCurrent groups (id | name | location | stream | active):")
    try:
        cur.execute(
            "SELECT id, name, location, stream, is_active "
            "FROM groups ORDER BY id"
        )
        for row in cur.fetchall():
            loc = row[2] if row[2] else "(all)"
            st = row[3] if row[3] else "—"
            print(f"  #{row[0]:<3} {row[1][:40]:<40} | {loc:<5} | {st:<8} | {row[4]}")
    except Exception as e:
        print(f"! could not read groups: {e}", file=sys.stderr)

    conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    main()