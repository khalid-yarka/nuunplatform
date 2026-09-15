# admin_users_db.py
# Advanced user management helpers for the admin panel.
# Auto-adds missing columns/tables on first use — no manual migration.

import csv
import io
import logging
import re
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


_SCHEMA_READY = False


def ensure_admin_user_schema() -> bool:
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
            "CREATE INDEX IF NOT EXISTS idx_students_verified ON students(is_verified)",
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
    ensure_admin_user_schema()
    try:
        execute_with_retry("""
            INSERT INTO admin_user_actions
                (admin_id, target_user_id, action, old_value, new_value, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            admin_id, target_user_id, action,
            str(old_value) if old_value is not None else None,
            str(new_value) if new_value is not None else None,
            note, now(),
        ), commit=True)
        return True
    except Exception as e:
        logger.error(f"log_admin_user_action failed: {e}")
        return False


SORT_MAP = {
    'newest': 'created_at DESC',
    'oldest': 'created_at ASC',
    'name_az': 'first_name ASC, last_name ASC',
    'name_za': 'first_name DESC, last_name DESC',
    'points_high': 'total_points DESC',
    'points_low': 'total_points ASC',
    'tier_high': "CASE tier WHEN 'pro' THEN 3 WHEN 'premium' THEN 2 ELSE 1 END DESC, total_points DESC",
}


def _build_user_filter_sql(
    search: str = '',
    tier_filter: str = '',
    location_filter: str = '',
    curriculum_filter: str = '',
    only_admins: bool = False,
    only_inactive: bool = False,
    verified_filter: str = '',
) -> Tuple[str, List[Any]]:
    where = ["1=1"]
    params: List[Any] = []

    if search:
        like = f"%{search}%"
        where.append(
            "(first_name LIKE ? OR middle_name LIKE ? OR last_name LIKE ? "
            "OR phone_number LIKE ? OR public_id LIKE ? OR school LIKE ? OR city LIKE ?)"
        )
        params.extend([like] * 7)

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

    if verified_filter == '1':
        where.append("is_verified = 1")
    elif verified_filter == '0':
        where.append("is_verified = 0")

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
    verified_filter: str = '',
    sort: str = 'newest',
    page: int = 1,
    per_page: int = 25,
) -> Tuple[List[Dict], int]:
    ensure_admin_user_schema()

    where_sql, params = _build_user_filter_sql(
        search, tier_filter, location_filter, curriculum_filter,
        only_admins, only_inactive, verified_filter,
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
               total_points, is_admin, is_verified, tier, created_at,
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
    verified_filter: str = '',
    sort: str = 'newest',
) -> List[Dict]:
    ensure_admin_user_schema()
    where_sql, params = _build_user_filter_sql(
        search, tier_filter, location_filter, curriculum_filter,
        only_admins, only_inactive, verified_filter,
    )
    order_sql = SORT_MAP.get(sort, SORT_MAP['newest'])

    cursor = execute_with_retry(
        f"""
        SELECT id, public_id, first_name, middle_name, last_name,
               phone_number, location, city, school, grade, curriculum,
               total_points, is_admin, is_verified, tier, created_at, last_login_at
        FROM students
        WHERE {where_sql}
        ORDER BY {order_sql}
        """,
        params
    )
    return [dict(row) for row in cursor.fetchall()]


def users_to_csv(users: List[Dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        'Public ID', 'First Name', 'Middle Name', 'Last Name',
        'Phone', 'Location', 'City', 'School', 'Grade', 'Curriculum',
        'Tier', 'Points', 'Verified', 'Admin', 'Joined', 'Last Login',
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
            'YES' if u.get('is_verified') else 'NO',
            'YES' if u.get('is_admin') else 'NO',
            u.get('created_at') or '',
            u.get('last_login_at') or '',
        ])
    return buf.getvalue()


def get_users_admin_stats() -> Dict[str, int]:
    ensure_admin_user_schema()
    try:
        cursor = execute_with_retry("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN is_admin = 1 THEN 1 ELSE 0 END) AS admins,
                SUM(CASE WHEN is_verified = 1 THEN 1 ELSE 0 END) AS verified,
                SUM(CASE WHEN is_verified = 0 THEN 1 ELSE 0 END) AS unverified,
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
        'verified': 0, 'unverified': 0,
        'free': 0, 'premium': 0, 'pro': 0,
        'this_week': 0, 'today': 0,
    }


def get_user_admin_history(user_id: int, limit: int = 50) -> List[Dict]:
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
# PROFILE EDIT
# ============================================

_VALID_GRADES = ('G7', 'G8', 'F3', 'F4')
_VALID_LOCATIONS = ('SO', 'PL', 'SL')
_VALID_CURRICULA = ('general', 'science', 'arts')


def _valid_name(name: str) -> bool:
    name = (name or '').strip()
    return len(name) >= 4 and name.isalpha()


def _valid_city(city: str) -> bool:
    city = (city or '').strip()
    return len(city) >= 5 and all(c.isalpha() or c.isspace() for c in city)


def _valid_school(school: str) -> bool:
    words = (school or '').strip().split()
    return len(words) >= 2 and all(
        len(w) >= 4 and w.isalpha() for w in words
    )


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r'\D', '', raw or '')
    if digits.startswith('252'):
        digits = digits[3:]
    return '+252' + digits


def update_user_profile(user_id: int, data: Dict[str, Any], admin_id: int) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Update a user's profile fields as a super admin.
    Returns (ok, message, changed_fields).
    """
    ensure_admin_user_schema()

    user = get_student_by_id(user_id)
    if not user:
        return False, 'User not found.', {}

    old = dict(user)

    new_first = (data.get('first_name') or old.get('first_name') or '').strip()
    new_middle = (data.get('middle_name') or '').strip() if 'middle_name' in data else (old.get('middle_name') or '')
    new_last = (data.get('last_name') or old.get('last_name') or '').strip()
    new_school = (data.get('school') or old.get('school') or '').strip()
    new_grade = (data.get('grade') or old.get('grade') or '').strip()
    new_city = (data.get('city') or old.get('city') or '').strip()
    new_location = (data.get('location') or old.get('location') or '').strip()

    if 'curriculum' in data:
        new_curriculum = (data.get('curriculum') or '').strip() or None
    else:
        new_curriculum = old.get('curriculum')

    new_phone_raw = (data.get('phone_number') or '').strip()
    new_phone = None
    if new_phone_raw:
        digits = re.sub(r'\D', '', new_phone_raw)
        if len(digits) != 9:
            return False, 'Phone number must be exactly 9 digits.', {}
        new_phone = _normalize_phone(digits)

    if not _valid_name(new_first):
        return False, 'First name must be at least 4 letters (A–Z).', {}
    if new_middle and not re.fullmatch(r'[A-Za-z]+', new_middle):
        return False, 'Middle name must contain only letters.', {}
    if not _valid_name(new_last):
        return False, 'Last name must be at least 4 letters (A–Z).', {}
    if not _valid_school(new_school):
        return False, 'School must have at least 2 words, each 4+ letters.', {}
    if new_grade not in _VALID_GRADES:
        return False, f'Grade must be one of: {", ".join(_VALID_GRADES)}.', {}
    if not _valid_city(new_city):
        return False, 'City must be at least 5 letters.', {}
    if new_location not in _VALID_LOCATIONS:
        return False, f'Location must be one of: {", ".join(_VALID_LOCATIONS)}.', {}

    if new_location == 'PL':
        if new_curriculum not in _VALID_CURRICULA:
            return False, 'Curriculum is required for Puntland.', {}
    else:
        new_curriculum = None

    if new_phone:
        cursor = execute_with_retry(
            "SELECT id FROM students WHERE phone_number = ? AND id != ?",
            (new_phone, user_id),
        )
        if cursor.fetchone():
            return False, 'This phone number is already used by another account.', {}

    candidate = {
        'first_name': new_first,
        'middle_name': new_middle,
        'last_name': new_last,
        'school': new_school,
        'grade': new_grade,
        'city': new_city,
        'location': new_location,
        'curriculum': new_curriculum,
    }
    if new_phone:
        candidate['phone_number'] = new_phone

    changed_fields: Dict[str, Any] = {}
    for key, new_val in candidate.items():
        old_val = old.get(key)
        if str(old_val or '') != str(new_val or ''):
            changed_fields[key] = {'before': old_val, 'after': new_val}

    if not changed_fields:
        return True, 'No changes.', {}

    try:
        set_clauses = []
        params = []
        for key in candidate.keys():
            set_clauses.append(f"{key} = ?")
            params.append(candidate[key])
        params.append(user_id)

        execute_with_retry(
            f"UPDATE students SET {', '.join(set_clauses)} WHERE id = ?",
            params, commit=True,
        )

        log_admin_user_action(
            admin_id, user_id, 'edit_profile',
            None, ', '.join(changed_fields.keys()),
        )

        return True, f"Updated {len(changed_fields)} field(s).", changed_fields
    except Exception as e:
        logger.error(f"update_user_profile failed: {e}", exc_info=True)
        return False, 'Database error while saving.', {}


# ============================================
# VERIFICATION
# ============================================

def set_user_verified(user_id: int, is_verified: bool, admin_id: int) -> bool:
    """
    Set a user's verification status. Returns True on success (including
    a no-op when the value is unchanged).
    """
    ensure_admin_user_schema()
    try:
        user = get_student_by_id(user_id)
        if not user:
            return False

        old = int(user.get('is_verified', 0) or 0)
        new = 1 if is_verified else 0

        if old == new:
            return True

        execute_with_retry(
            "UPDATE students SET is_verified = ? WHERE id = ?",
            (new, user_id), commit=True,
        )
        log_admin_user_action(
            admin_id, user_id,
            'verify' if is_verified else 'unverify',
            str(old), str(new),
        )
        return True
    except Exception as e:
        logger.error(f"set_user_verified failed: {e}")
        return False


# ============================================
# SINGLE-USER WRITES
# ============================================

def set_user_admin_note(user_id: int, note: str, admin_id: int) -> bool:
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


# ============================================
# PASSWORD MANAGEMENT
# ============================================

def reset_user_password(
    user_id: int,
    new_password: str,
    admin_id: int,
    force_logout: bool = True,
    notify_user: bool = True,
) -> Tuple[bool, str]:
    ensure_admin_user_schema()

    if not isinstance(new_password, str):
        return False, 'Password must be a string.'

    new_password = new_password.strip()
    if len(new_password) < 8:
        return False, 'Password must be at least 8 characters.'
    if len(new_password) > 128:
        return False, 'Password is too long (max 128 characters).'

    try:
        user = get_student_by_id(user_id)
        if not user:
            return False, 'User not found.'

        from werkzeug.security import generate_password_hash
        password_hash = generate_password_hash(new_password)

        execute_with_retry(
            "UPDATE students SET password = ? WHERE id = ?",
            (password_hash, user_id), commit=True,
        )

        if force_logout:
            try:
                execute_with_retry(
                    "UPDATE students "
                    "SET session_version = COALESCE(session_version, 0) + 1 "
                    "WHERE id = ?",
                    (user_id,), commit=True,
                )
            except Exception as e:
                logger.warning(f"session_version bump failed for {user_id}: {e}")

        log_admin_user_action(admin_id, user_id, 'reset_password', None, 'custom')

        if notify_user:
            try:
                create_notification(
                    user_id=user_id, type='account',
                    title='🔐 Password Changed',
                    body='An administrator changed your password. Please log in again.',
                    link='/login', icon='🔐',
                )
            except Exception as e:
                logger.warning(f"reset-password notification failed: {e}")

        return True, 'Password updated. User sessions invalidated.'
    except Exception as e:
        logger.error(f"reset_user_password failed: {e}", exc_info=True)
        return False, 'Failed to update password.'


def reset_user_password_to_default(
    user_id: int,
    admin_id: int,
    default_password: str = '12345678',
    force_logout: bool = True,
    notify_user: bool = True,
) -> Tuple[bool, str]:
    return reset_user_password(
        user_id, default_password, admin_id,
        force_logout=force_logout, notify_user=notify_user,
    )


def force_user_logout(user_id: int, admin_id: int) -> bool:
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
    ensure_admin_user_schema()
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

            elif action == 'verify':
                if user.get('is_verified'):
                    failed += 1
                    continue
                execute_with_retry(
                    "UPDATE students SET is_verified = 1 WHERE id = ?",
                    (uid,), commit=True
                )
                log_admin_user_action(admin_id, uid, 'verify', '0', '1')
                succeeded += 1

            elif action == 'unverify':
                if not user.get('is_verified'):
                    failed += 1
                    continue
                execute_with_retry(
                    "UPDATE students SET is_verified = 0 WHERE id = ?",
                    (uid,), commit=True
                )
                log_admin_user_action(admin_id, uid, 'unverify', '1', '0')
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