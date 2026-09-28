#!/usr/bin/env python3
# ============================================================
# migrate_two_tier.py
# ============================================================
# One-time migration: collapse the tier system from three
# tiers (free / premium / pro) to two (free / premium).
#
# What it does:
#   1. Snapshots the main DB + WAL + SHM to
#      BACKUPS/pre_two_tier_migration_<utc>/
#   2. Rebuilds 7 tables whose CHECK constraints list 'pro':
#         students, groups, achievements, discount_codes,
#         upgrade_requests, entitlement_policies,
#         entitlement_overrides
#   3. Adds 4 columns:
#         students.onboarding_dismissed    INTEGER DEFAULT 0
#         students.first_discount_used     INTEGER DEFAULT 0
#         groups.is_visible                INTEGER DEFAULT 1
#         groups.requires_verified         INTEGER DEFAULT 0
#   4. Rewrites every 'pro' value in the affected tables:
#         students.tier                 pro -> premium (then reset)
#         groups.tier_required          pro -> premium
#         achievements.tier_required    pro -> premium
#         discount_codes.applies_to     pro -> premium
#         upgrade_requests.requested_tier  pro -> premium
#         entitlement_policies.tier     DELETE row (pro only)
#         entitlement_overrides.tier    DELETE row (pro only)
#   5. Resets every student to tier='free', tier_expires_at=NULL
#      (per the operator: "no tier users yet, we'll reset all")
#   6. Verifies the result and prints a summary
#
# The migration is IDEMPOTENT — running it twice does nothing
# on the second run. The first run creates a marker row in
# the new `_schema_migrations` table.
#
# Usage:
#   python migrate_two_tier.py                    # run it
#   python migrate_two_tier.py --dry-run          # preview only
#   python migrate_two_tier.py --verify           # check a done migration
#   python migrate_two_tier.py --no-backup        # skip the snapshot
#   python migrate_two_tier.py -v                 # verbose logging
# ============================================================

import argparse
import logging
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ------------------------------------------------------------
# PATH SETUP — mirror the pattern from migrate_question_grade.py
# ------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / '.env')
except Exception:
    pass

from config import Config


MIGRATION_ID = 'two_tier_v1'
MIGRATION_LABEL = 'Collapse free/premium/pro to free/premium'


# ============================================================
# LOGGING
# ============================================================
log = logging.getLogger('migrate_two_tier')


def _setup_logging(verbose: bool = False) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S',
    ))
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)


# ============================================================
# TABLE SPECIFICATIONS
# ============================================================
# For each table we rebuild:
#   'columns'      — exact column list of the NEW table
#   'select_expr'  — SQL SELECT expression producing those columns
#                    from the old table (named <table>__old)
#   'create_ddl'   — the CREATE TABLE statement for the NEW table
#   'indexes'      — CREATE INDEX statements to recreate after
#
# The rebuild pattern for each table is:
#   ALTER TABLE t RENAME TO t__old
#   <create_ddl>          (creates new t)
#   INSERT INTO t SELECT <select_expr> FROM t__old
#   DROP TABLE t__old
#   <indexes>
# ============================================================

STUDENTS_COLUMNS = [
    'id', 'public_id', 'phone_number', 'password',
    'first_name', 'middle_name', 'last_name',
    'location', 'city', 'school', 'grade',
    'total_points', 'is_admin', 'is_verified', 'curriculum',
    'tier', 'tier_expires_at', 'tier_updated_at',
    'last_login_at', 'last_login_ip', 'session_version', 'admin_note',
    'onboarding_dismissed', 'first_discount_used',
    'created_at',
]

# Per operator: reset all tiers to 'free' with no expiry.
STUDENTS_SELECT = """
    id, public_id, phone_number, password,
    first_name, middle_name, last_name,
    location, city, school, grade,
    total_points, is_admin, is_verified, curriculum,
    'free'        AS tier,
    NULL          AS tier_expires_at,
    tier_updated_at,
    last_login_at, last_login_ip, session_version, admin_note,
    0             AS onboarding_dismissed,
    0             AS first_discount_used,
    created_at
"""

STUDENTS_DDL = """
CREATE TABLE students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT UNIQUE NOT NULL,
    phone_number TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    first_name TEXT NOT NULL,
    middle_name TEXT DEFAULT '',
    last_name TEXT NOT NULL,
    location TEXT DEFAULT '',
    city TEXT DEFAULT '',
    school TEXT DEFAULT '',
    grade TEXT DEFAULT '',
    total_points INTEGER DEFAULT 0,
    is_admin INTEGER DEFAULT 0,
    is_verified INTEGER NOT NULL DEFAULT 0,
    curriculum TEXT,
    tier TEXT NOT NULL DEFAULT 'free' CHECK (tier IN ('free', 'premium')),
    tier_expires_at TEXT,
    tier_updated_at TEXT,
    last_login_at TEXT,
    last_login_ip TEXT,
    session_version INTEGER DEFAULT 0,
    admin_note TEXT DEFAULT '',
    onboarding_dismissed INTEGER NOT NULL DEFAULT 0,
    first_discount_used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
)
"""

STUDENTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_students_phone       ON students(phone_number)",
    "CREATE INDEX IF NOT EXISTS idx_students_public_id   ON students(public_id)",
    "CREATE INDEX IF NOT EXISTS idx_students_tier        ON students(tier)",
    "CREATE INDEX IF NOT EXISTS idx_students_verified    ON students(is_verified)",
    "CREATE INDEX IF NOT EXISTS idx_students_created     ON students(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_students_points      ON students(total_points DESC)",
    "CREATE INDEX IF NOT EXISTS idx_students_location    ON students(location)",
]


GROUPS_COLUMNS = [
    'id', 'name', 'platform', 'invite_link', 'description',
    'category', 'curriculum', 'subjects', 'tier_required',
    'group_type', 'icon', 'display_order', 'is_active', 'is_featured',
    'is_visible', 'requires_verified',
    'click_count', 'created_by', 'created_at', 'updated_at',
]

# Rewrite 'pro' -> 'premium' on tier_required.
GROUPS_SELECT = """
    id, name, platform, invite_link, description,
    category, curriculum, subjects,
    CASE WHEN tier_required = 'pro' THEN 'premium'
         ELSE tier_required END AS tier_required,
    group_type, icon, display_order, is_active, is_featured,
    1  AS is_visible,
    0  AS requires_verified,
    click_count, created_by, created_at, updated_at
"""

GROUPS_DDL = """
CREATE TABLE groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    platform TEXT NOT NULL CHECK (platform IN ('whatsapp', 'telegram')),
    invite_link TEXT NOT NULL,
    description TEXT DEFAULT '',
    category TEXT DEFAULT '',
    curriculum TEXT DEFAULT '',
    subjects TEXT DEFAULT '',
    tier_required TEXT NOT NULL DEFAULT 'free'
        CHECK (tier_required IN ('free', 'premium')),
    group_type TEXT DEFAULT 'community',
    icon TEXT DEFAULT '📚',
    display_order INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    is_featured INTEGER DEFAULT 0,
    is_visible INTEGER NOT NULL DEFAULT 1,
    requires_verified INTEGER NOT NULL DEFAULT 0,
    click_count INTEGER DEFAULT 0,
    created_by INTEGER,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
)
"""

GROUPS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_groups_platform   ON groups(platform)",
    "CREATE INDEX IF NOT EXISTS idx_groups_category   ON groups(category)",
    "CREATE INDEX IF NOT EXISTS idx_groups_active     ON groups(is_active)",
    "CREATE INDEX IF NOT EXISTS idx_groups_featured   ON groups(is_featured)",
    "CREATE INDEX IF NOT EXISTS idx_groups_click_count ON groups(click_count DESC)",
]


ACHIEVEMENTS_COLUMNS = [
    'id', 'name', 'description', 'icon', 'tier_required',
    'unlock_condition', 'created_at',
]

ACHIEVEMENTS_SELECT = """
    id, name, description, icon,
    CASE WHEN tier_required = 'pro' THEN 'premium'
         ELSE tier_required END AS tier_required,
    unlock_condition, created_at
"""

ACHIEVEMENTS_DDL = """
CREATE TABLE achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    icon TEXT,
    tier_required TEXT DEFAULT 'free' CHECK (tier_required IN ('free', 'premium')),
    unlock_condition TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
)
"""

ACHIEVEMENTS_INDEXES = []


DISCOUNT_CODES_COLUMNS = [
    'id', 'code', 'discount_type', 'discount_value', 'applies_to',
    'max_uses', 'used_count', 'expires_at', 'is_active',
    'created_by', 'created_at', 'updated_at',
]

# 'pro' applies_to becomes 'premium' since pro no longer exists.
DISCOUNT_CODES_SELECT = """
    id, code, discount_type, discount_value,
    CASE WHEN applies_to = 'pro' THEN 'premium'
         ELSE applies_to END AS applies_to,
    max_uses, used_count, expires_at, is_active,
    created_by, created_at, updated_at
"""

DISCOUNT_CODES_DDL = """
CREATE TABLE discount_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    discount_type TEXT NOT NULL CHECK (discount_type IN ('percentage', 'fixed')),
    discount_value INTEGER NOT NULL,
    applies_to TEXT NOT NULL CHECK (applies_to IN ('all', 'premium')),
    max_uses INTEGER,
    used_count INTEGER DEFAULT 0,
    expires_at TEXT,
    is_active INTEGER DEFAULT 1,
    created_by INTEGER,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT
)
"""

DISCOUNT_CODES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_discount_codes_code    ON discount_codes(code)",
    "CREATE INDEX IF NOT EXISTS idx_discount_codes_expires ON discount_codes(expires_at)",
]


UPGRADE_REQUESTS_COLUMNS = [
    'id', 'request_id', 'user_id', 'requested_tier', 'duration',
    'original_price_cents', 'discount_code_id', 'discount_amount_cents',
    'final_price_cents', 'user_note', 'status', 'admin_id', 'admin_note',
    'expiry_date', 'approved_at', 'rejected_at', 'created_at', 'updated_at',
]

UPGRADE_REQUESTS_SELECT = """
    id, request_id, user_id,
    CASE WHEN requested_tier = 'pro' THEN 'premium'
         ELSE requested_tier END AS requested_tier,
    duration,
    original_price_cents, discount_code_id, discount_amount_cents,
    final_price_cents, user_note, status, admin_id, admin_note,
    expiry_date, approved_at, rejected_at, created_at, updated_at
"""

UPGRADE_REQUESTS_DDL = """
CREATE TABLE upgrade_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT UNIQUE NOT NULL,
    user_id INTEGER NOT NULL,
    requested_tier TEXT NOT NULL CHECK (requested_tier IN ('premium')),
    duration TEXT NOT NULL CHECK (duration IN ('monthly', 'term', 'yearly')),
    original_price_cents INTEGER NOT NULL,
    discount_code_id INTEGER,
    discount_amount_cents INTEGER DEFAULT 0,
    final_price_cents INTEGER NOT NULL,
    user_note TEXT,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
    admin_id INTEGER,
    admin_note TEXT,
    expiry_date TEXT,
    approved_at TEXT,
    rejected_at TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT,
    FOREIGN KEY (user_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (discount_code_id) REFERENCES discount_codes(id) ON DELETE SET NULL,
    FOREIGN KEY (admin_id) REFERENCES students(id) ON DELETE SET NULL
)
"""

UPGRADE_REQUESTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_upgrade_requests_user       ON upgrade_requests(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_upgrade_requests_status     ON upgrade_requests(status)",
    "CREATE INDEX IF NOT EXISTS idx_upgrade_requests_created    ON upgrade_requests(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_upgrade_requests_request_id ON upgrade_requests(request_id)",
]


ENTITLEMENT_POLICIES_COLUMNS = [
    'id', 'feature_id', 'tier', 'is_enabled',
    'level_value', 'limit_value', 'limit_unit', 'updated_at',
]

# Drop the 'pro' rows entirely — the tier no longer exists.
ENTITLEMENT_POLICIES_SELECT = """
    id, feature_id, tier, is_enabled,
    level_value, limit_value, limit_unit, updated_at
"""

ENTITLEMENT_POLICIES_DDL = """
CREATE TABLE entitlement_policies (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_id   INTEGER NOT NULL,
    tier         TEXT NOT NULL CHECK (tier IN ('free','premium')),
    is_enabled   INTEGER NOT NULL DEFAULT 1,
    level_value  INTEGER,
    limit_value  INTEGER,
    limit_unit   TEXT,
    updated_at   TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (feature_id) REFERENCES entitlement_features(id)
        ON DELETE CASCADE,
    UNIQUE(feature_id, tier)
)
"""

ENTITLEMENT_POLICIES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_entitlement_policies_feature ON entitlement_policies(feature_id)",
]


ENTITLEMENT_OVERRIDES_COLUMNS = [
    'feature_key', 'tier', 'field', 'value', 'updated_by', 'updated_at',
]

ENTITLEMENT_OVERRIDES_SELECT = """
    feature_key, tier, field, value, updated_by, updated_at
"""

ENTITLEMENT_OVERRIDES_DDL = """
CREATE TABLE entitlement_overrides (
    feature_key  TEXT     NOT NULL,
    tier         TEXT     NOT NULL CHECK (tier IN ('free', 'premium')),
    field        TEXT     NOT NULL CHECK (field IN (
                              'is_enabled', 'level_value',
                              'limit_value', 'limit_unit'
                          )),
    value        TEXT,
    updated_by   INTEGER,
    updated_at   TEXT     DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (feature_key, tier, field),
    FOREIGN KEY (updated_by) REFERENCES students(id) ON DELETE SET NULL
)
"""

ENTITLEMENT_OVERRIDES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_entitlement_overrides_feature ON entitlement_overrides(feature_key)",
]


# Full ordered list of table specs.
TABLE_SPECS = [
    {
        'name': 'students',
        'columns': STUDENTS_COLUMNS,
        'select_expr': STUDENTS_SELECT,
        'create_ddl': STUDENTS_DDL,
        'indexes': STUDENTS_INDEXES,
        'drop_pro_rows': False,
        'rewrite_pro': True,
    },
    {
        'name': 'groups',
        'columns': GROUPS_COLUMNS,
        'select_expr': GROUPS_SELECT,
        'create_ddl': GROUPS_DDL,
        'indexes': GROUPS_INDEXES,
        'drop_pro_rows': False,
        'rewrite_pro': True,
    },
    {
        'name': 'achievements',
        'columns': ACHIEVEMENTS_COLUMNS,
        'select_expr': ACHIEVEMENTS_SELECT,
        'create_ddl': ACHIEVEMENTS_DDL,
        'indexes': ACHIEVEMENTS_INDEXES,
        'drop_pro_rows': False,
        'rewrite_pro': True,
    },
    {
        'name': 'discount_codes',
        'columns': DISCOUNT_CODES_COLUMNS,
        'select_expr': DISCOUNT_CODES_SELECT,
        'create_ddl': DISCOUNT_CODES_DDL,
        'indexes': DISCOUNT_CODES_INDEXES,
        'drop_pro_rows': False,
        'rewrite_pro': True,
    },
    {
        'name': 'upgrade_requests',
        'columns': UPGRADE_REQUESTS_COLUMNS,
        'select_expr': UPGRADE_REQUESTS_SELECT,
        'create_ddl': UPGRADE_REQUESTS_DDL,
        'indexes': UPGRADE_REQUESTS_INDEXES,
        'drop_pro_rows': False,
        'rewrite_pro': True,
    },
    {
        'name': 'entitlement_policies',
        'columns': ENTITLEMENT_POLICIES_COLUMNS,
        'select_expr': ENTITLEMENT_POLICIES_SELECT,
        'create_ddl': ENTITLEMENT_POLICIES_DDL,
        'indexes': ENTITLEMENT_POLICIES_INDEXES,
        'drop_pro_rows': True,   # delete pro rows
        'rewrite_pro': False,
    },
    {
        'name': 'entitlement_overrides',
        'columns': ENTITLEMENT_OVERRIDES_COLUMNS,
        'select_expr': ENTITLEMENT_OVERRIDES_SELECT,
        'create_ddl': ENTITLEMENT_OVERRIDES_DDL,
        'indexes': ENTITLEMENT_OVERRIDES_INDEXES,
        'drop_pro_rows': True,   # delete pro rows
        'rewrite_pro': False,
    },
]


# ============================================================
# HELPERS
# ============================================================

def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=60, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    )
    return cur.fetchone() is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row['name'] == column for row in cur.fetchall())


def _count_table(conn: sqlite3.Connection, table: str) -> int:
    try:
        cur = conn.execute(f"SELECT COUNT(*) AS n FROM {table}")
        return int(cur.fetchone()['n'])
    except Exception:
        return -1


# ============================================================
# BACKUP
# ============================================================

def _create_backup(db_path: Path, backup_dir: Path) -> Optional[Path]:
    """
    Copy nuunplatform.db + WAL + SHM to a timestamped folder.
    Returns the folder path, or None on failure.
    """
    stamp = _utc_stamp()
    target = backup_dir / f'pre_two_tier_migration_{stamp}'
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log.error(f"Could not create backup folder {target}: {e}")
        return None

    copied = 0
    for suffix in ('', '-wal', '-shm'):
        src = Path(str(db_path) + suffix)
        if src.exists():
            try:
                shutil.copy2(src, target / src.name)
                copied += 1
            except Exception as e:
                log.error(f"Backup failed for {src.name}: {e}")
                return None

    if copied == 0:
        log.error("Backup produced 0 files — aborting.")
        return None

    log.info(f"Backup created: {target} ({copied} file(s))")
    return target


# ============================================================
# MIGRATION MARKER TABLE
# ============================================================

def _ensure_marker_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _schema_migrations (
            id          TEXT PRIMARY KEY,
            label       TEXT,
            applied_at  TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)


def _is_already_applied(conn: sqlite3.Connection) -> bool:
    if not _table_exists(conn, '_schema_migrations'):
        return False
    cur = conn.execute(
        "SELECT 1 FROM _schema_migrations WHERE id = ? LIMIT 1",
        (MIGRATION_ID,),
    )
    return cur.fetchone() is not None


def _mark_applied(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO _schema_migrations (id, label) VALUES (?, ?)",
        (MIGRATION_ID, MIGRATION_LABEL),
    )


# ============================================================
# TABLE REBUILD
# ============================================================

def _rebuild_one(conn: sqlite3.Connection, spec: dict, dry_run: bool) -> Tuple[bool, str]:
    """
    Rebuild a single table. Returns (ok, message).
    Must be called inside a transaction.
    """
    name = spec['name']
    old_name = f'{name}__old'

    if not _table_exists(conn, name):
        return True, f"skip (table {name} does not exist)"

    # Row count before
    before_n = _count_table(conn, name)

    # Count 'pro' rows for the log
    pro_n = 0
    if spec.get('rewrite_pro') or spec.get('drop_pro_rows'):
        # Find the column that has a 'pro' value
        pro_column = None
        if name == 'students':               pro_column = 'tier'
        elif name == 'groups':               pro_column = 'tier_required'
        elif name == 'achievements':         pro_column = 'tier_required'
        elif name == 'discount_codes':       pro_column = 'applies_to'
        elif name == 'upgrade_requests':     pro_column = 'requested_tier'
        elif name == 'entitlement_policies': pro_column = 'tier'
        elif name == 'entitlement_overrides':pro_column = 'tier'

        if pro_column:
            try:
                cur = conn.execute(
                    f"SELECT COUNT(*) AS n FROM {name} WHERE {pro_column} = 'pro'"
                )
                pro_n = int(cur.fetchone()['n'])
            except Exception:
                pass

    if dry_run:
        msg = f"would rebuild {name} ({before_n} rows"
        if pro_n:
            if spec.get('drop_pro_rows'):
                msg += f", would delete {pro_n} pro rows"
            else:
                msg += f", would rewrite {pro_n} pro -> premium"
        msg += ")"
        return True, msg

    # 1. Drop any leftover temp table from a failed prior run
    if _table_exists(conn, old_name):
        conn.execute(f"DROP TABLE {old_name}")
    
    # 2. Rename original to temp.
    #    legacy_alter_table = ON prevents SQLite from rewriting FK
    #    references in other tables to point at the temp name.
    conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        conn.execute(f"ALTER TABLE {name} RENAME TO {old_name}")
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")

    # 3. Create new table
    conn.execute(spec['create_ddl'])

    # 4. Copy rows with transformations
    cols = ', '.join(spec['columns'])

    if spec.get('drop_pro_rows'):
        # Determine pro column for this table
        pro_column = None
        if name == 'entitlement_policies':   pro_column = 'tier'
        elif name == 'entitlement_overrides':pro_column = 'tier'

        if pro_column:
            conn.execute(
                f"INSERT INTO {name} ({cols}) "
                f"SELECT {spec['select_expr']} FROM {old_name} "
                f"WHERE {pro_column} != 'pro'"
            )
        else:
            conn.execute(
                f"INSERT INTO {name} ({cols}) "
                f"SELECT {spec['select_expr']} FROM {old_name}"
            )
    else:
        conn.execute(
            f"INSERT INTO {name} ({cols}) "
            f"SELECT {spec['select_expr']} FROM {old_name}"
        )

    # 5. Drop temp
    conn.execute(f"DROP TABLE {old_name}")

    # 6. Recreate indexes
    for idx_sql in spec['indexes']:
        conn.execute(idx_sql)

    after_n = _count_table(conn, name)
    msg = f"rebuilt {name}: {before_n} -> {after_n} rows"
    if pro_n:
        if spec.get('drop_pro_rows'):
            msg += f" (deleted {pro_n} pro rows)"
        else:
            msg += f" (rewrote {pro_n} pro -> premium)"
    return True, msg


# ============================================================
# VERIFICATION
# ============================================================

def verify(db_path: Path) -> Tuple[bool, List[str]]:
    """
    Read-only verification. Returns (all_ok, list_of_issues).
    """
    issues: List[str] = []
    if not db_path.exists():
        return False, [f"database not found: {db_path}"]

    conn = _open_db(db_path)
    try:
        # 1. Marker exists
        if not _table_exists(conn, '_schema_migrations'):
            issues.append("_schema_migrations table missing")
        else:
            cur = conn.execute(
                "SELECT applied_at FROM _schema_migrations WHERE id = ?",
                (MIGRATION_ID,),
            )
            row = cur.fetchone()
            if not row:
                issues.append(f"marker '{MIGRATION_ID}' not present")
            else:
                log.info(f"Migration applied at: {row['applied_at']}")

        # 2. New columns exist
        for tbl, col in [
            ('students', 'onboarding_dismissed'),
            ('students', 'first_discount_used'),
            ('groups', 'is_visible'),
            ('groups', 'requires_verified'),
        ]:
            if not _table_exists(conn, tbl):
                issues.append(f"table {tbl} missing")
                continue
            if not _column_exists(conn, tbl, col):
                issues.append(f"column {tbl}.{col} missing")

        # 3. No 'pro' values remain in any CHECK column
        checks = [
            ('students', 'tier'),
            ('groups', 'tier_required'),
            ('achievements', 'tier_required'),
            ('discount_codes', 'applies_to'),
            ('upgrade_requests', 'requested_tier'),
            ('entitlement_policies', 'tier'),
            ('entitlement_overrides', 'tier'),
        ]
        for tbl, col in checks:
            if not _table_exists(conn, tbl):
                continue
            try:
                cur = conn.execute(
                    f"SELECT COUNT(*) AS n FROM {tbl} WHERE {col} = 'pro'"
                )
                n = int(cur.fetchone()['n'])
                if n > 0:
                    issues.append(f"{tbl}.{col} still has {n} 'pro' row(s)")
            except Exception as e:
                issues.append(f"could not verify {tbl}.{col}: {e}")

        # 4. All students are tier='free'
        if _table_exists(conn, 'students'):
            cur = conn.execute(
                "SELECT COUNT(*) AS n FROM students WHERE tier != 'free'"
            )
            n = int(cur.fetchone()['n'])
            if n > 0:
                issues.append(
                    f"{n} student(s) still have tier != 'free' "
                    f"(expected 0 after reset)"
                )

    finally:
        conn.close()

    return (len(issues) == 0), issues


# ============================================================
# MAIN MIGRATION
# ============================================================

def run(db_path: Path, backup_dir: Path,
        skip_backup: bool = False,
        dry_run: bool = False) -> int:
    """
    Execute the migration. Returns process exit code.
    """
    t_start = time.time()
    log.info('=' * 60)
    log.info(f'Migrating: {MIGRATION_LABEL}')
    log.info(f'Database:  {db_path}')
    log.info(f'Dry run:   {dry_run}')
    log.info('=' * 60)

    if not db_path.exists():
        log.error(f"Database not found: {db_path}")
        return 2

    # ── Step 0: idempotency check ──
    conn = _open_db(db_path)
    try:
        if _is_already_applied(conn):
            log.info('✓ Migration already applied — nothing to do.')
            return 0
    finally:
        conn.close()

    # ── Step 1: backup ──
    backup_path: Optional[Path] = None
    if not skip_backup and not dry_run:
        backup_path = _create_backup(db_path, backup_dir)
        if backup_path is None:
            log.error('Backup failed — aborting for safety.')
            return 3
    elif dry_run:
        log.info('[dry-run] would create backup in ' + str(backup_dir))
    else:
        log.warning('Backup skipped by --no-backup flag.')

    # ── Step 2: run the rebuild inside one transaction ──
    conn = _open_db(db_path)
    try:
        conn.execute("BEGIN")

        # 2a. Marker table
        if not dry_run:
            _ensure_marker_table(conn)

        # 2b. Rebuild every table in the spec list
        for spec in TABLE_SPECS:
            ok, msg = _rebuild_one(conn, spec, dry_run)
            prefix = '[dry-run]' if dry_run else '✓'
            if ok:
                log.info(f'{prefix} {msg}')
            else:
                log.error(f'✗ {msg}')
                conn.execute("ROLLBACK")
                return 4

        # 2c. Drop the 'pro' rows from entitlement_policies (if any remain)
        #     — already handled inside _rebuild_one via drop_pro_rows

        # 2d. Ensure the WAL/SHM tables are consistent
        if not dry_run:
            conn.execute("PRAGMA optimize")

        # 2e. Mark applied
        if not dry_run:
            _mark_applied(conn)

        conn.execute("COMMIT")
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        log.error(f'Migration failed: {e}', exc_info=True)
        if backup_path:
            log.error(f'Restore from: {backup_path}')
        return 5
    finally:
        conn.close()

    # ── Step 3: verify ──
    if not dry_run:
        log.info('-' * 60)
        log.info('Running verification...')
        ok, issues = verify(db_path)
        if ok:
            log.info('✓ Verification passed')
        else:
            log.error('✗ Verification found issues:')
            for issue in issues:
                log.error(f'  - {issue}')
            if backup_path:
                log.error(f'Restore from: {backup_path}')
            return 6

    elapsed = time.time() - t_start
    log.info('=' * 60)
    log.info(f'✓ Migration complete in {elapsed:.2f}s')
    log.info('=' * 60)
    return 0


# ============================================================
# CLI
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description='Collapse free/premium/pro -> free/premium'
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Print changes without applying them')
    parser.add_argument('--verify', action='store_true',
                        help='Only verify a completed migration')
    parser.add_argument('--no-backup', action='store_true',
                        help='Skip the pre-migration backup (dangerous)')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    _setup_logging(verbose=args.verbose)

    db_path = Path(Config.DATABASE_PATH)
    backup_dir = Path(Config.BACKUP_DIR)

    if args.verify:
        ok, issues = verify(db_path)
        if ok:
            log.info('✓ All checks passed.')
            return 0
        log.error('✗ Issues found:')
        for issue in issues:
            log.error(f'  - {issue}')
        return 1

    return run(
        db_path=db_path,
        backup_dir=backup_dir,
        skip_backup=args.no_backup,
        dry_run=args.dry_run,
    )


if __name__ == '__main__':
    sys.exit(main())