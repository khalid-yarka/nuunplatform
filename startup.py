# ============================================
# APPLICATION STARTUP VERIFICATION
# ============================================
# Verifies critical components before starting Flask.
# Also bootstraps the entitlement system and admin capabilities.
#
# NOTE: the full SQLite integrity scan and the write test are NOT
# run at boot anymore — they are too slow for PythonAnywhere's WSGI
# timeout. Use `python -m database --verify` from a scheduled task,
# or check the /health endpoint after the app is up.
# ============================================

import os
import sys
import json
import sqlite3
import logging
from typing import Dict, Tuple, List

from config import Config
from database import (
    verify_database_exists,
    verify_database_openable,
    verify_database_writable,
    verify_tables_exist,
    verify_wal_enabled,
    create_database_schema,
    enable_wal_mode,
    ensure_question_interactions_table,
    _get_connection,
)

logger = logging.getLogger(__name__)


# ============================================
# FAST DATABASE BOOTSTRAP
# ============================================

def initialize_database_startup_fast() -> Tuple[bool, List[str]]:
    """
    Fast startup path for the database.

    What it does:
      - ensures the DB file exists (creates schema if missing)
      - verifies the DB is openable
      - verifies required tables exist
      - ensures WAL mode
      - ensures the question_interactions table exists

    What it does NOT do (by design):
      - the full PRAGMA integrity_check (O(DB size))
      - the write test (opens a transaction just to prove it works)
      - column-by-column schema verification

    These slow operations are safe to skip at boot because they are
    redundant on every restart and can be run from /health or a
    scheduled task. Their absence does not prevent serving pages.
    """
    errors: List[str] = []

    # 1. Ensure the DB file exists
    if not verify_database_exists():
        logger.info("Database not found. Creating new database...")
        success, error = create_database_schema()
        if not success:
            return False, [f"Failed to create database schema: {error}"]

        success, error = enable_wal_mode()
        if not success:
            return False, [f"Failed to enable WAL mode: {error}"]

        logger.info("Database created successfully")
    else:
        logger.info("Database already exists.")

    # 2. Must be openable
    openable, err = verify_database_openable()
    if not openable:
        return False, [f"Cannot open database: {err}"]

    # 3. Required tables must exist
    try:
        conn = _get_connection(timeout=5)
        ensure_question_interactions_table(conn)
        ensure_push_tables(conn)
        tables_ok, missing = verify_tables_exist(conn)
        conn.close()
        if not tables_ok:
            # If this is a fresh install with no tables, try creating schema
            if not verify_database_exists():
                pass
            errors.append(f"Missing tables: {', '.join(missing)}")
            return False, errors
    except Exception as e:
        return False, [f"Table verification failed: {e}"]

    # 4. WAL mode
    wal_ok, wal_err = verify_wal_enabled()
    if not wal_ok:
        logger.warning(f"WAL not enabled ({wal_err}); attempting to enable.")
        success, error = enable_wal_mode()
        if not success:
            # Not fatal — the app can still serve, just slower
            logger.warning(f"Could not enable WAL: {error}")
        else:
            logger.info("WAL mode enabled")

    return True, []


# ============================================
# ENTITLEMENT BOOTSTRAP
# ============================================

ENTITLEMENTS_SEED_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'entitlements_seed.json'
)


def _get_user_version(conn: sqlite3.Connection) -> int:
    """Return PRAGMA user_version (0 on first install)."""
    try:
        cursor = conn.execute("PRAGMA user_version")
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _set_user_version(conn: sqlite3.Connection, version: int) -> None:
    """Set PRAGMA user_version."""
    conn.execute(f"PRAGMA user_version = {int(version)}")
    conn.commit()


def _count_features(conn: sqlite3.Connection) -> int:
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM entitlement_features")
        return int(cursor.fetchone()[0])
    except Exception:
        return -1


def bootstrap_entitlements() -> Tuple[bool, str]:
    """
    First-run bootstrap of the entitlement tables.

    - If PRAGMA user_version >= 1: skip (DB is authoritative).
    - If PRAGMA user_version == 0: load seed JSON, insert features + policies,
      set user_version = 1.

    Returns (ok, message).
    """
    db_path = Config.DATABASE_PATH
    if not os.path.exists(db_path):
        return False, f"Database not found: {db_path}"

    if not os.path.exists(ENTITLEMENTS_SEED_FILE):
        return False, f"Seed file not found: {ENTITLEMENTS_SEED_FILE}"

    try:
        with open(ENTITLEMENTS_SEED_FILE, 'r', encoding='utf-8') as f:
            seed = json.load(f)
    except Exception as e:
        return False, f"Could not parse seed file: {e}"

    features = seed.get('features', [])
    if not features:
        return False, "Seed file has no features."

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    try:
        version = _get_user_version(conn)

        if version >= 1:
            existing = _count_features(conn)
            conn.close()
            return True, f"Entitlements already seeded (user_version={version}, {existing} features)"

        # ---- First install: seed ----
        logger.info(f"Bootstrapping entitlements ({len(features)} features)...")

        # Defensive: verify tables exist
        try:
            conn.execute("SELECT 1 FROM entitlement_features LIMIT 1")
        except sqlite3.OperationalError:
            conn.close()
            return False, "Entitlement tables not found. Check schema.sql."

        inserted_features = 0
        inserted_policies = 0

        for feat in features:
            feature_key = feat['feature_key']
            display_name = feat['display_name']
            description = feat.get('description', '')
            category = feat['category']
            policy_type = feat['policy_type']
            unit_hint = feat.get('unit_hint')
            sort_order = feat.get('sort_order', 0)
            notes = feat.get('notes', '')

            cursor = conn.execute(
                "SELECT id FROM entitlement_features WHERE feature_key = ?",
                (feature_key,)
            )
            row = cursor.fetchone()
            if row:
                feature_id = row['id']
            else:
                cursor = conn.execute("""
                    INSERT INTO entitlement_features (
                        feature_key, display_name, description, category,
                        policy_type, unit_hint, is_global_active,
                        sort_order, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """, (
                    feature_key, display_name, description, category,
                    policy_type, unit_hint, sort_order, notes
                ))
                feature_id = cursor.lastrowid
                inserted_features += 1

            policies = feat.get('policies', {})
            for tier in ('free', 'premium', 'pro'):
                p = policies.get(tier, {})
                is_enabled = 1 if p.get('is_enabled', 0) else 0
                level_value = p.get('level_value')
                limit_value = p.get('limit_value')
                limit_unit = p.get('limit_unit')

                cursor = conn.execute(
                    "SELECT id FROM entitlement_policies WHERE feature_id = ? AND tier = ?",
                    (feature_id, tier)
                )
                if cursor.fetchone():
                    continue

                conn.execute("""
                    INSERT INTO entitlement_policies (
                        feature_id, tier, is_enabled,
                        level_value, limit_value, limit_unit
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    feature_id, tier, is_enabled,
                    level_value, limit_value, limit_unit
                ))
                inserted_policies += 1

        conn.commit()
        _set_user_version(conn, 1)

        total_features = _count_features(conn)
        conn.close()

        msg = (f"Entitlements seeded: {inserted_features} features, "
               f"{inserted_policies} policies ({total_features} total features)")
        logger.info(msg)
        return True, msg

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        logger.error(f"Entitlement bootstrap failed: {e}", exc_info=True)
        return False, f"Bootstrap error: {e}"


# ============================================
# ADMIN CAPABILITY BOOTSTRAP
# ============================================

def _count_admin_capability_grants(conn: sqlite3.Connection) -> int:
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM admin_capability_grants")
        return int(cursor.fetchone()[0])
    except Exception:
        return -1


def bootstrap_admin_capabilities() -> Tuple[bool, str]:
    """
    First-run seed of the admin capability grants table.

    - Reads DEFAULT_ENABLED_KEYS from the registry.
    - Inserts one row per enabled capability.
    - Never overwrites an existing row.
    - Logs stale keys (in DB but not in registry) but does not delete.
    """
    db_path = Config.DATABASE_PATH
    if not os.path.exists(db_path):
        return False, f"Database not found: {db_path}"

    try:
        from services.admin.registry import (
            DEFAULT_ENABLED_KEYS,
            REGISTRY_KEYS,
        )
    except Exception as e:
        return False, f"Could not import capability registry: {e}"

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    try:
        try:
            conn.execute("SELECT 1 FROM admin_capability_grants LIMIT 1")
        except sqlite3.OperationalError:
            conn.close()
            return False, (
                "admin_capability_grants table not found. "
                "Run schema migration first."
            )

        inserted = 0
        existing_keys = set()

        cursor = conn.execute(
            "SELECT capability_key FROM admin_capability_grants"
        )
        for row in cursor.fetchall():
            existing_keys.add(row['capability_key'])

        stale_keys = existing_keys - REGISTRY_KEYS
        if stale_keys:
            logger.warning(
                f"Stale capability grants found in DB (not in registry): "
                f"{sorted(stale_keys)}"
            )

        for key in DEFAULT_ENABLED_KEYS:
            if key in existing_keys:
                continue
            conn.execute("""
                INSERT INTO admin_capability_grants
                    (capability_key, is_enabled, updated_at)
                VALUES (?, 1, datetime('now', 'localtime'))
            """, (key,))
            inserted += 1

        conn.execute("""
            INSERT OR IGNORE INTO admin_capability_version (id, version)
            VALUES (1, 1)
        """)

        conn.commit()

        total = _count_admin_capability_grants(conn)
        conn.close()

        msg = (
            f"Admin capabilities seeded: "
            f"{inserted} new grant(s), {total} total, "
            f"{len(DEFAULT_ENABLED_KEYS)} default-enabled"
        )
        logger.info(msg)
        return True, msg

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        logger.error(f"Admin capability bootstrap failed: {e}", exc_info=True)
        return False, f"Bootstrap error: {e}"


# ============================================
# STARTUP VERIFICATION
# ============================================

def verify_startup() -> bool:
    """
    Verify critical components before starting the application.
    Returns True if everything is ready, False otherwise.

    Uses the fast DB path — no integrity scan, no write test.
    """
    errors: List[str] = []

    # 1. Configuration
    logger.info("Validating configuration...")
    config_errors = Config.validate()
    if config_errors:
        for error in config_errors:
            logger.critical(f"Config error: {error}")
            errors.append(f"Config: {error}")

    # 2. Database (fast path)
    logger.info("Initializing database (fast path)...")
    db_success, db_errors = initialize_database_startup_fast()
    if not db_success:
        for error in db_errors:
            logger.critical(f"Database error: {error}")
            errors.append(f"Database: {error}")

    # 3. Error table
    logger.info("Ensuring error table exists...")
    try:
        from error_models import ensure_error_table
        if not ensure_error_table():
            logger.critical("Failed to create error_logs table")
            errors.append("Database: Cannot create error_logs table")
        else:
            logger.info("Error table verified")
    except Exception as e:
        logger.critical(f"Error table creation failed: {e}")
        errors.append(f"Database: Error table - {e}")

    # 4. Entitlements
    logger.info("Bootstrapping entitlement system...")
    try:
        ok, msg = bootstrap_entitlements()
        if ok:
            logger.info(f"Entitlements OK: {msg}")
        else:
            logger.critical(f"Entitlement bootstrap failed: {msg}")
            errors.append(f"Entitlements: {msg}")
    except Exception as e:
        logger.critical(f"Entitlement bootstrap exception: {e}")
        errors.append(f"Entitlements: {e}")

    # 4b. Admin capabilities
    logger.info("Bootstrapping admin capabilities...")
    try:
        ok, msg = bootstrap_admin_capabilities()
        if ok:
            logger.info(f"Admin capabilities OK: {msg}")
        else:
            logger.critical(f"Admin capability bootstrap failed: {msg}")
            errors.append(f"Admin capabilities: {msg}")
    except Exception as e:
        logger.critical(f"Admin capability bootstrap exception: {e}")
        errors.append(f"Admin capabilities: {e}")

    # 5. Backup directory
    logger.info("Verifying backup directory...")
    try:
        if not os.path.exists(Config.BACKUP_DIR):
            os.makedirs(Config.BACKUP_DIR, exist_ok=True)
            logger.info(f"Created backup directory: {Config.BACKUP_DIR}")
    except Exception as e:
        logger.critical(f"Backup directory error: {e}")
        errors.append(f"Backup: Cannot create directory - {e}")

    # 6. Log directory
    logger.info("Verifying log directory...")
    try:
        if not os.path.exists(Config.LOG_DIR):
            os.makedirs(Config.LOG_DIR, exist_ok=True)
            logger.info(f"Created log directory: {Config.LOG_DIR}")
    except Exception as e:
        logger.critical(f"Log directory error: {e}")
        errors.append(f"Logs: Cannot create directory - {e}")

    # Report
    if errors:
        error_message = "\n".join(errors)
        logger.critical(f"Startup verification FAILED:\n{error_message}")

        try:
            from errors import send_error_email
            send_error_email({
                'request_id': 'startup',
                'timestamp': datetime.now().isoformat() if 'datetime' in dir() else '',
                'severity': 'CRITICAL',
                'status_code': 500,
                'url': 'STARTUP',
                'method': 'SYSTEM',
                'user_id': None,
                'ip_address': 'localhost',
                'error_type': 'StartupVerificationFailed',
                'error_message': error_message[:1000],
                'stack_trace': error_message,
                'user_description': 'Application startup failed',
                'occurrence_count': 1,
                'error_hash': 'startup_' + str(int(__import__('time').time()))
            })
        except Exception as e:
            logger.error(f"Failed to send startup error email: {e}")

        return False

    logger.info("Startup verification COMPLETE - All systems ready")
    return True


def get_startup_health() -> dict:
    """
    Get detailed startup health information.
    Used by the /health endpoint. Does not perform the full
    integrity scan (that is available at /health?full=1 if you add it).
    """
    health = {
        'config_valid': False,
        'database': {},
        'error_table': False,
        'entitlements': {'ok': False, 'message': ''},
        'admin_capabilities': {'ok': False, 'message': ''},
        'backup_dir': False,
        'log_dir': False,
        'errors': []
    }

    config_errors = Config.validate()
    health['config_valid'] = len(config_errors) == 0
    if config_errors:
        health['errors'].extend(config_errors)

    try:
        from database import get_database_health
        health['database'] = get_database_health()
    except Exception as e:
        health['errors'].append(f"Database health check: {e}")

    try:
        from error_models import ensure_error_table
        health['error_table'] = ensure_error_table()
        if not health['error_table']:
            health['errors'].append('Error table could not be created')
    except Exception as e:
        health['errors'].append(f'Error table: {e}')

    # Entitlements (read-only)
    try:
        db_path = Config.DATABASE_PATH
        conn = sqlite3.connect(db_path, timeout=5)
        cursor = conn.execute("PRAGMA user_version")
        uv = cursor.fetchone()[0]
        cursor = conn.execute("SELECT COUNT(*) FROM entitlement_features")
        count = cursor.fetchone()[0]
        conn.close()
        health['entitlements'] = {
            'ok': uv >= 1 and count > 0,
            'message': f"user_version={uv}, features={count}",
            'user_version': uv,
            'feature_count': count,
        }
    except Exception as e:
        health['entitlements'] = {'ok': False, 'message': str(e)}
        health['errors'].append(f"Entitlements: {e}")

    # Admin capabilities (read-only)
    try:
        db_path = Config.DATABASE_PATH
        conn = sqlite3.connect(db_path, timeout=5)
        cursor = conn.execute("SELECT COUNT(*) FROM admin_capability_grants")
        grant_count = cursor.fetchone()[0]
        cursor = conn.execute(
            "SELECT version FROM admin_capability_version WHERE id = 1"
        )
        row = cursor.fetchone()
        version = row[0] if row else 0
        conn.close()
        health['admin_capabilities'] = {
            'ok': grant_count > 0,
            'message': f"version={version}, grants={grant_count}",
            'version': version,
            'grant_count': grant_count,
        }
    except Exception as e:
        health['admin_capabilities'] = {'ok': False, 'message': str(e)}
        health['errors'].append(f"Admin capabilities: {e}")

    health['backup_dir'] = os.path.exists(Config.BACKUP_DIR)
    health['log_dir'] = os.path.exists(Config.LOG_DIR)

    if not health['backup_dir']:
        health['errors'].append(f'Backup directory missing: {Config.BACKUP_DIR}')
    if not health['log_dir']:
        health['errors'].append(f'Log directory missing: {Config.LOG_DIR}')

    return health