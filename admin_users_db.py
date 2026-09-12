# admin_users_db.py
# Advanced user management helpers for the admin panel.
# Auto-adds missing columns/tables on first use — no manual migration.
#
# PHASE 2: Tier vocabulary normalized to canonical free/premium/pro.

import csv
import io
import logging
import sqlite3
from datetime import datetime
from typing import Optional, List, Tuple, Dict, Any

from db import (
    execute_with_retry, get_db, get_student_by_id, create_notification,
    delete_user as db_delete_user, now,
)
from subjects_config import get_subject
from tier_config import normalize_tier

logger = logging.getLogger(__name__)


# ============================================
# SCHEMA BOOTSTRAP (idempotent)
# ============================================

_SCHEMA_READY = False


def ensure_admin_user_schema() -> bool:
    """Add columns and tables needed for advanced user admin. Runs once per process."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return True

    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("PRAGMA table_info(students)")
        existing_cols = {row[1] for row in cursor.fetchall()}

        new_cols = [
            ("admin_note", "TEXT DEFAULT ''"),
            ("last_login_at", "TEXT"),
            ("last_login_ip", "TEXT"),
            ("session_version", "INTEGER DEFAULT 0"),
        ]
        for col_name, ddl in new_cols:
            if col_name not in existing_cols:
                try:
                    cursor.execute(f"ALTER TABLE students ADD COLUMN {col_name} {ddl}")
                    logger.info(f"Added column students.{col_name}")
                except sqlite3.OperationalError as e:
                    logger.warning(f"Could not add students.{col_name}: {e}")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                target_user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                note TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_admin_user_actions_target
            ON admin_user_actions(target_user_id, created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_admin_user_actions_admin
            ON admin_user_actions(admin_id, created_at DESC)
        """)

        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_students_created ON students(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_students_points ON students(total_points DESC)",
            "CREATE INDEX IF NOT EXISTS idx_students_location ON students(location)",
        ]:
            try:
                cursor.execute(idx_sql)
            except sqlite3.OperationalError:
                pass

        conn.commit()
        _SCHEMA_READY = True
        return True
    except Exception as e:
        logger.error(f"ensure_admin_user_schema failed: {e}", exc_info=True)
        return False


def log_admin_user_action(
    admin_id: int,
    target_user_id: int,
    action: str,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
    note: Optional[str] = None,
) -> bool:
    """Record an admin action on a user."""
    ensure_admin_user_schema()
    try:
        execute_with_retry("""
            INSERT INTO admin_user_actions
                (admin_id, target_user_id, action, old_value, new_value, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            admin_id,
            target_user_id,
            action,
            str(old_value) if old_value is not None else None,
            str(new_value) if new_value is not None else None,
            note,
            now(),
        ), commit=True)
        return True
    except Exception as e:
        logger.error(f"log_admin_user_action failed: {e}")
        return False


# ============================================
# LIST QUERY (search / filter / sort / paginate)
# ============================================

SORT_MAP = {
    'newest': 'created_at DESC',
    'oldest': 'created_at ASC',
    'name_az': 'first_name ASC, last_name ASC',
    'name_za': 'first_name DESC, last_name DESC',
    'points_high': 'total_points DESC',
    'points_low': 'total_points ASC',
    # PHASE 2: canonical vocabulary
    'tier_high': "CASE tier WHEN 'pro' THEN 3 WHEN 'premium' THEN 2 ELSE 1 END DESC, total_points DESC",
}


def _build_user_filter_sql(
    search: str = '',
    tier_filter: str = '',
    location_filter: str = '',
    curriculum_filter: str = '',
    only_admins: bool = False,
    only_inactive: bool = False,
) -> Tuple[str, List[Any]]:
    """Shared WHERE clause builder for list + export."""
    where = ["1=1"]
    params: List[Any] = []

    if search:
        like = f"%{search}%"
        where.append(
            "(first_name LIKE ? OR middle_name LIKE ? OR last_name LIKE ? "
            "OR phone_number LIKE ? OR public_id LIKE ? OR school LIKE ? OR city LIKE ?)"
        )
        params.extend([like] * 7)

    # PHASE 2: accept canonical or legacy input; compare against canonical.
    if tier_filter:
        canonical = normalize_tier(tier_filter)
        if canonical in ('free', 'premium', 'pro'):
            where.append("tier = ?")
            params.append(canonical)

    if location_filter in ('SO', 'PL', 'SL'):
        where.append("location = ?")
        params.append(location_filter)

    if curriculum_filter in ('general', 'science', 'arts'):
        where.append("curriculum = ?")
        params.append(curriculum_filter)

    if only_admins:
        where.append("is_admin = 1")

    if only_inactive:
        where.append("""
            id NOT IN (
                SELECT DISTINCT student_id FROM quiz_attempts
                WHERE completed_at >= datetime('now', '-30 days')
            )
            AND created_at <= datetime('now', '-30 days')
        """)

    return " AND ".join(where), params


def get_users_admin(
    search: str = '',
    tier_filter: str = '',
    location_filter: str = '',
    curriculum_filter: str = '',
    only_admins: bool = False,
    only_inactive: bool = False,
    sort: str = 'newest',
    page: int = 1,
    per_page: int = 25,
) -> Tuple[List[Dict], int]:
    """Return (list_of_users, total_count) for the admin list page."""
    ensure_admin_user_schema()

    where_sql, params = _build_user_filter_sql(
        search, tier_filter, location_filter, curriculum_filter,
        only_admins, only_inactive,
    )
    order_sql = SORT_MAP.get(sort, SORT_MAP['newest'])

    count_cursor = execute_with_retry(
        f"SELECT COUNT(*) AS c FROM students WHERE {where_sql}", params
    )
    total = count_cursor.fetchone()['c']

    offset = max(0, (page - 1) * per_page)
    cursor = execute_with_retry(
        f"""
        SELECT id, public_id, first_name, middle_name, last_name,
               phone_number, location, city, school, grade, curriculum,
               total_points, is_admin, tier, created_at,
               COALESCE(admin_note, '') AS admin_note,
               last_login_at
        FROM students
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """,
        params + [per_page, offset]
    )
    users = [dict(row) for row in cursor.fetchall()]
    return users, total


def get_users_admin_export(
    search: str = '',
    tier_filter: str = '',
    location_filter: str = '',
    curriculum_filter: str = '',
    only_admins: bool = False,
    only_inactive: bool = False,
    sort: str = 'newest',
) -> List[Dict]:
    """Return all matching users (no pagination) for CSV export."""
    ensure_admin_user_schema()
    where_sql, params = _build_user_filter_sql(
        search, tier_filter, location_filter, curriculum_filter,
        only_admins, only_inactive,
    )
    order_sql = SORT_MAP.get(sort, SORT_MAP['newest'])

    cursor = execute_with_retry(
        f"""
        SELECT id, public_id, first_name, middle_name, last_name,
               phone_number, location, city, school, grade, curriculum,
               total_points, is_admin, tier, created_at, last_login_at
        FROM students
        WHERE {where_sql}
        ORDER BY {order_sql}
        """,
        params
    )
    return [dict(row) for row in cursor.fetchall()]


def users_to_csv(users: List[Dict]) -> str:
    """Convert a list of user dicts to a CSV string."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        'Public ID', 'First Name', 'Middle Name', 'Last Name',
        'Phone', 'Location', 'City', 'School', 'Grade', 'Curriculum',
        'Tier', 'Points', 'Admin', 'Joined', 'Last Login',
    ])
    for u in users:
        writer.writerow([
            u.get('public_id') or '',
            u.get('first_name') or '',
            u.get('middle_name') or '',
            u.get('last_name') or '',
            u.get('phone_number') or '',
            u.get('location') or '',
            u.get('city') or '',
            u.get('school') or '',
            u.get('grade') or '',
            u.get('curriculum') or '',
            normalize_tier(u.get('tier') or 'free').upper(),
            u.get('total_points') or 0,
            'YES' if u.get('is_admin') else 'NO',
            u.get('created_at') or '',
            u.get('last_login_at') or '',
        ])
    return buf.getvalue()


# ============================================
# STATS
# ============================================

def get_users_admin_stats() -> Dict[str, int]:
    """Top-of-page stats for the admin user list (canonical tier keys)."""
    ensure_admin_user_schema()
    try:
        cursor = execute_with_retry("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN is_admin = 1 THEN 1 ELSE 0 END) AS admins,
                SUM(CASE WHEN tier = 'free'    THEN 1 ELSE 0 END) AS free,
                SUM(CASE WHEN tier = 'premium' THEN 1 ELSE 0 END) AS premium,
                SUM(CASE WHEN tier = 'pro'     THEN 1 ELSE 0 END) AS pro,
                SUM(CASE WHEN created_at >= datetime('now', '-7 days') THEN 1 ELSE 0 END) AS this_week,
                SUM(CASE WHEN created_at >= datetime('now', '-1 day')  THEN 1 ELSE 0 END) AS today
            FROM students
        """)
        row = cursor.fetchone()
        if row:
            return {k: (row[k] or 0) for k in row.keys()}
    except Exception as e:
        logger.error(f"get_users_admin_stats failed: {e}")
    return {
        'total': 0, 'admins': 0,
        'free': 0, 'premium': 0, 'pro': 0,
        'this_week': 0, 'today': 0,
    }


# ============================================
# DETAIL PAGE DATA
# ============================================

def get_user_admin_history(user_id: int, limit: int = 50) -> List[Dict]:
    """Recent admin actions targeting this user."""
    ensure_admin_user_schema()
    try:
        cursor = execute_with_retry("""
            SELECT aua.*, s.first_name AS admin_first, s.last_name AS admin_last,
                   s.public_id AS admin_public_id
            FROM admin_user_actions aua
            LEFT JOIN students s ON aua.admin_id = s.id
            WHERE aua.target_user_id = ?
            ORDER BY aua.created_at DESC
            LIMIT ?
        """, (user_id, limit))
        rows = []
        for row in cursor.fetchall():
            d = dict(row)
            d['admin_name'] = f"{d.get('admin_first') or ''} {d.get('admin_last') or ''}".strip() or 'System'
            rows.append(d)
        return rows
    except Exception as e:
        logger.error(f"get_user_admin_history failed: {e}")
        return []


def get_user_recent_quizzes_admin(user_id: int, limit: int = 20) -> List[Dict]:
    """Recent quiz attempts enriched with subject info."""
    try:
        cursor = execute_with_retry("""
            SELECT id, subject_code, score, total_questions, completed_at
            FROM quiz_attempts
            WHERE student_id = ?
            ORDER BY completed_at DESC
            LIMIT ?
        """, (user_id, limit))
        out = []
        for row in cursor.fetchall():
            a = dict(row)
            subj = get_subject(a['subject_code'])
            a['subject_name'] = subj['name'] if subj else a['subject_code']
            a['subject_icon'] = subj.get('icon', '📚') if subj else '📚'
            t = a['total_questions'] or 0
            a['percentage'] = round((a['score'] / t) * 100) if t else 0
            out.append(a)
        return out
    except Exception as e:
        logger.error(f"get_user_recent_quizzes_admin failed: {e}")
        return []


def get_user_recent_live_quizzes(user_id: int, limit: int = 10) -> List[Dict]:
    """Recent live quiz participations."""
    try:
        cursor = execute_with_retry("""
            SELECT lqp.id, lqp.quiz_id, lqp.score, lqp.ranking,
                   lqp.status AS participant_status, lqp.joined_at,
                   lq.title, lq.subject_code, lq.status AS quiz_status
            FROM live_quiz_participants lqp
            JOIN live_quizzes lq ON lqp.quiz_id = lq.id
            WHERE lqp.student_id = ?
            ORDER BY lqp.joined_at DESC
            LIMIT ?
        """, (user_id, limit))
        out = []
        for row in cursor.fetchall():
            d = dict(row)
            subj = get_subject(d.get('subject_code'))
            d['subject_name'] = subj['name'] if subj else d.get('subject_code') or ''
            d['subject_icon'] = subj.get('icon', '📚') if subj else '📚'
            out.append(d)
        return out
    except Exception as e:
        logger.error(f"get_user_recent_live_quizzes failed: {e}")
        return []


# ============================================
# WRITE OPERATIONS (single user)
# ============================================

def set_user_admin_note(user_id: int, note: str, admin_id: int) -> bool:
    """Set the free-text admin note on a user and log the change."""
    ensure_admin_user_schema()
    try:
        user = get_student_by_id(user_id)
        if not user:
            return False
        old = user.get('admin_note') or ''
        execute_with_retry(
            "UPDATE students SET admin_note = ? WHERE id = ?",
            (note, user_id), commit=True
        )
        log_admin_user_action(admin_id, user_id, 'set_note', old[:200], note[:200])
        return True
    except Exception as e:
        logger.error(f"set_user_admin_note failed: {e}")
        return False


def set_user_tier_admin(user_id: int, new_tier: str, admin_id: int) -> bool:
    """Set a user's tier. Accepts canonical or legacy input; stores canonical."""
    ensure_admin_user_schema()

    new_tier = normalize_tier(new_tier)
    if new_tier not in ('free', 'premium', 'pro'):
        return False

    try:
        user = get_student_by_id(user_id)
        if not user:
            return False

        old = normalize_tier(user.get('tier') or 'free')
        if old == new_tier:
            return True

        execute_with_retry(
            "UPDATE students SET tier = ?, tier_updated_at = ? WHERE id = ?",
            (new_tier, now(), user_id), commit=True
        )
        log_admin_user_action(admin_id, user_id, 'set_tier', old, new_tier)

        try:
            from services.entitlement_service import refresh_user
            refresh_user(user_id)
        except Exception as e:
            logger.warning(f"refresh_user failed after tier change: {e}")

        return True
    except Exception as e:
        logger.error(f"set_user_tier_admin failed: {e}")
        return False


def toggle_user_admin_admin(user_id: int, admin_id: int) -> Optional[bool]:
    """Returns the new admin state (True/False) or None on error."""
    ensure_admin_user_schema()
    if user_id == admin_id:
        return None
    try:
        user = get_student_by_id(user_id)
        if not user:
            return None
        old = bool(user.get('is_admin', 0))
        new = not old
        execute_with_retry(
            "UPDATE students SET is_admin = ? WHERE id = ?",
            (1 if new else 0, user_id), commit=True
        )
        log_admin_user_action(admin_id, user_id, 'toggle_admin',
                              '1' if old else '0', '1' if new else '0')
        return new
    except Exception as e:
        logger.error(f"toggle_user_admin_admin failed: {e}")
        return None


def reset_user_password(user_id: int, new_password: str, admin_id: int) -> bool:
    """Set a user's password to a new value (hashed)."""
    ensure_admin_user_schema()
    try:
        if len(new_password) < 8:
            return False
        from werkzeug.security import generate_password_hash
        password_hash = generate_password_hash(new_password)
        execute_with_retry(
            "UPDATE students SET password = ? WHERE id = ?",
            (password_hash, user_id), commit=True
        )
        log_admin_user_action(admin_id, user_id, 'reset_password')
        return True
    except Exception as e:
        logger.error(f"reset_user_password failed: {e}")
        return False


def force_user_logout(user_id: int, admin_id: int) -> bool:
    """Bump session_version so all existing sessions become invalid."""
    ensure_admin_user_schema()
    try:
        execute_with_retry("""
            UPDATE students
            SET session_version = COALESCE(session_version, 0) + 1
            WHERE id = ?
        """, (user_id,), commit=True)
        log_admin_user_action(admin_id, user_id, 'force_logout')
        return True
    except Exception as e:
        logger.error(f"force_user_logout failed: {e}")
        return False


def set_user_public_id(user_id: int, new_id: str, admin_id: int) -> Tuple[bool, str]:
    """Set a user's public ID. Returns (ok, message)."""
    ensure_admin_user_schema()
    import re
    new_id = (new_id or '').strip().upper()
    if not re.fullmatch(r'[A-Z0-9]{4}', new_id):
        return False, 'ID must be exactly 4 uppercase letters/digits.'
    try:
        cursor = execute_with_retry(
            "SELECT id FROM students WHERE public_id = ? AND id != ?",
            (new_id, user_id)
        )
        if cursor.fetchone():
            return False, 'This public ID is already taken.'
        user = get_student_by_id(user_id)
        old = user.get('public_id') if user else None
        execute_with_retry(
            "UPDATE students SET public_id = ? WHERE id = ?",
            (new_id, user_id), commit=True
        )
        log_admin_user_action(admin_id, user_id, 'set_public_id', old, new_id)
        return True, 'Public ID updated.'
    except Exception as e:
        logger.error(f"set_user_public_id failed: {e}")
        return False, 'Update failed.'


# ============================================
# BULK ACTIONS
# ============================================

def bulk_user_action(
    action: str,
    user_ids: List[int],
    admin_id: int,
    extra: Optional[Dict[str, Any]] = None,
) -> Tuple[int, int]:
    """Returns (succeeded, failed). Never raises."""
    ensure_admin_user_schema()
    if not user_ids:
        return 0, 0
    extra = extra or {}

    succeeded = 0
    failed = 0

    for raw_uid in user_ids:
        try:
            uid = int(raw_uid)
        except (ValueError, TypeError):
            failed += 1
            continue

        try:
            user = get_student_by_id(uid)
            if not user:
                failed += 1
                continue

            if uid == admin_id and action in ('delete', 'demote_admin'):
                failed += 1
                continue

            if action == 'set_tier':
                new_tier = normalize_tier(extra.get('tier') or '')
                if new_tier not in ('free', 'premium', 'pro'):
                    failed += 1
                    continue
                old = normalize_tier(user.get('tier') or 'free')
                if old == new_tier:
                    failed += 1
                    continue
                execute_with_retry(
                    "UPDATE students SET tier = ?, tier_updated_at = ? WHERE id = ?",
                    (new_tier, now(), uid), commit=True
                )
                log_admin_user_action(admin_id, uid, 'set_tier', old, new_tier)
                try:
                    from services.entitlement_service import refresh_user
                    refresh_user(uid)
                except Exception:
                    pass
                succeeded += 1

            elif action == 'promote_admin':
                if user.get('is_admin'):
                    failed += 1
                    continue
                execute_with_retry(
                    "UPDATE students SET is_admin = 1 WHERE id = ?",
                    (uid,), commit=True
                )
                log_admin_user_action(admin_id, uid, 'toggle_admin', '0', '1')
                succeeded += 1

            elif action == 'demote_admin':
                if not user.get('is_admin'):
                    failed += 1
                    continue
                execute_with_retry(
                    "UPDATE students SET is_admin = 0 WHERE id = ?",
                    (uid,), commit=True
                )
                log_admin_user_action(admin_id, uid, 'toggle_admin', '1', '0')
                succeeded += 1

            elif action == 'delete':
                ok, _ = db_delete_user(uid, admin_id, keep_ratings=True, delete_attempts=True)
                if ok:
                    log_admin_user_action(admin_id, uid, 'delete', user.get('public_id'), None)
                    succeeded += 1
                else:
                    failed += 1

            elif action == 'notify':
                title = (extra.get('title') or '').strip()
                body = (extra.get('body') or '').strip()
                if not title or not body:
                    failed += 1
                    continue
                create_notification(uid, 'admin_bulk', title, body, '/dashboard', '📢')
                log_admin_user_action(admin_id, uid, 'notify', None, title[:200])
                succeeded += 1

            else:
                failed += 1

        except Exception as e:
            logger.error(f"bulk_user_action {action} on {raw_uid}: {e}")
            failed += 1

    return succeeded, failed