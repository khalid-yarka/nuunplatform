#!/usr/bin/env python3
"""
One-time initializer for the Web Push tables.

Run once after deploying the push system:

    python scripts/init_push_tables.py

Idempotent — safe to run repeatedly. Uses CREATE TABLE IF NOT EXISTS,
so a second run is a no-op.

Options:
    --db PATH       Target a specific database file
                    (default: Config.DATABASE_PATH)
    --dry-run       Show what would be done, change nothing
    --verify-only   Just check whether the tables exist

Exit codes:
    0   Tables exist (or were created successfully)
    1   Failure
"""

import argparse
import os
import sqlite3
import sys

# Allow the script to be run from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config import Config
    DEFAULT_DB = Config.DATABASE_PATH
except Exception:
    DEFAULT_DB = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'nuunplatform.db',
    )


# ---------------------------------------------------------------------
# DDL — kept byte-identical to schema.sql / database.ensure_push_tables
# ---------------------------------------------------------------------
DDL_TABLE = """
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    endpoint    TEXT    NOT NULL,
    p256dh      TEXT    NOT NULL,
    auth        TEXT    NOT NULL,
    created_at  TEXT    DEFAULT (datetime('now', 'localtime')),
    UNIQUE(user_id, endpoint),
    FOREIGN KEY (user_id) REFERENCES students(id) ON DELETE CASCADE
)
"""

DDL_INDEX_USER = (
    "CREATE INDEX IF NOT EXISTS idx_push_subs_user "
    "ON push_subscriptions(user_id)"
)

DDL_INDEX_ENDPOINT = (
    "CREATE INDEX IF NOT EXISTS idx_push_subs_endpoint "
    "ON push_subscriptions(endpoint)"
)

EXPECTED_COLUMNS = {
    'id':         'INTEGER',
    'user_id':    'INTEGER',
    'endpoint':   'TEXT',
    'p256dh':     'TEXT',
    'auth':       'TEXT',
    'created_at': 'TEXT',
}


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _connect(db_path: str) -> sqlite3.Connection:
    """Mirror database._get_connection pragmas."""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _index_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _existing_columns(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("PRAGMA table_info(push_subscriptions)").fetchall()
    return {row['name']: row['type'] for row in rows}


def _verify(conn: sqlite3.Connection) -> int:
    """Print a verification report. Return 0 if all good, 1 otherwise."""
    problems = []

    if not _table_exists(conn, 'push_subscriptions'):
        print("  ✗ push_subscriptions table is missing")
        problems.append('table')
    else:
        print("  ✓ push_subscriptions table exists")

    if _table_exists(conn, 'push_subscriptions'):
        cols = _existing_columns(conn)
        for name, expected_type in EXPECTED_COLUMNS.items():
            if name not in cols:
                print(f"    ✗ missing column: {name}")
                problems.append(f'column:{name}')
            elif cols[name].upper() != expected_type.upper():
                print(
                    f"    ⚠ column {name} type is {cols[name]}, "
                    f"expected {expected_type}"
                )

    for idx in ('idx_push_subs_user', 'idx_push_subs_endpoint'):
        if _index_exists(conn, idx):
            print(f"  ✓ index {idx} exists")
        else:
            print(f"  ✗ index {idx} is missing")
            problems.append(idx)

    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM push_subscriptions"
        ).fetchone()[0]
        print(f"  · {count} subscription row(s)")
    except sqlite3.OperationalError:
        pass

    return 0 if not problems else 1


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description='Initialise Web Push tables (idempotent).',
    )
    parser.add_argument(
        '--db',
        default=DEFAULT_DB,
        help=f'Path to the SQLite database (default: {DEFAULT_DB})',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without touching the database.',
    )
    parser.add_argument(
        '--verify-only',
        action='store_true',
        help='Only check whether the tables exist; do not create.',
    )
    args = parser.parse_args()

    db_path = os.path.abspath(args.db)

    print()
    print("═" * 64)
    print("  NuunPlatform — Web Push table initializer")
    print("═" * 64)
    print(f"  Database: {db_path}")
    print()

    if not os.path.exists(db_path):
        print(f"  ✗ Database file not found: {db_path}")
        print()
        return 1

    try:
        conn = _connect(db_path)
    except Exception as exc:
        print(f"  ✗ Could not open database: {exc}")
        print()
        return 1

    try:
        # --verify-only: just report and exit.
        if args.verify_only:
            print("  Running in VERIFY-ONLY mode.")
            print()
            rc = _verify(conn)
            print()
            return rc

        # Check what already exists so we can report accurately.
        table_exists_before = _table_exists(conn, 'push_subscriptions')

        if args.dry_run:
            print("  Running in DRY-RUN mode. Nothing will be changed.")
            print()
            if table_exists_before:
                print("  · push_subscriptions already exists — would skip CREATE")
            else:
                print("  · Would CREATE TABLE push_subscriptions")
            if _index_exists(conn, 'idx_push_subs_user'):
                print("  · idx_push_subs_user already exists — would skip")
            else:
                print("  · Would CREATE INDEX idx_push_subs_user")
            if _index_exists(conn, 'idx_push_subs_endpoint'):
                print("  · idx_push_subs_endpoint already exists — would skip")
            else:
                print("  · Would CREATE INDEX idx_push_subs_endpoint")
            print()
            return 0

        # Create.
        print("  Applying DDL...")
        conn.execute(DDL_TABLE)
        conn.execute(DDL_INDEX_USER)
        conn.execute(DDL_INDEX_ENDPOINT)
        conn.commit()

        if table_exists_before:
            print("  · Table already existed — DDL was a no-op")
        else:
            print("  ✓ Created push_subscriptions")

        print("  ✓ Ensured idx_push_subs_user")
        print("  ✓ Ensured idx_push_subs_endpoint")
        print()

        # Verify.
        print("  Verification:")
        rc = _verify(conn)
        print()

        if rc == 0:
            print("  All good. You can now run the app with push enabled.")
            print()
        else:
            print("  ⚠ Verification flagged issues. See above.")
            print()

        return rc

    except Exception as exc:
        print(f"  ✗ Error: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
        print()
        return 1

    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == '__main__':
    sys.exit(main())