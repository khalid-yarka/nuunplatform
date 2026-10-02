# ============================================================
# blueprints/admin/users_bp.py
# Advanced user management for super admin.
# ============================================================

from flask import (
    Blueprint, render_template, request, session, flash,
    redirect, url_for, abort, jsonify, Response,
)
import logging
import secrets
import string
from datetime import datetime, timedelta, timezone

from config import Config
from db import (
    get_student_by_id,
    delete_user as db_delete_user,
    get_deleted_users,
    restore_deleted_user as db_restore_user,
    get_user_subject_list,
    is_admin,
    execute_with_retry,
    create_notification,
)
from services.tier_service import (
    get_user_tier, set_user_tier, get_current_user_tier,
)
from services import entitlement_service
from tier_config import normalize_tier
from admin_users_db import (
    ensure_admin_user_schema,
    get_users_admin, get_users_admin_export, get_users_admin_stats,
    users_to_csv, set_user_admin_note, set_user_tier_admin,
    toggle_user_admin_admin, reset_user_password, reset_user_password_to_default,
    force_user_logout,
    set_user_public_id, get_user_admin_history, get_user_recent_quizzes_admin,
    get_user_recent_live_quizzes, bulk_user_action, log_admin_user_action,
    update_user_profile,
    set_user_verified,
)
from utils import validate_csrf, SOMALI_TIMEZONE
from services.admin.guards import admin_can
from services.admin.capabilities import admin_can as has_capability
from services.admin.audit import write_audit
from activity_logger import log_admin_action

logger = logging.getLogger(__name__)

admin_users_bp = Blueprint('admin_users', __name__, url_prefix='/admin')


# ============================================================
# PASSWORD GENERATION
# ============================================================

_PASSWORD_ALPHABET = string.ascii_letters + string.digits
_RANDOM_PASSWORD_LENGTH = 12


def _generate_random_password() -> str:
    return ''.join(
        secrets.choice(_PASSWORD_ALPHABET)
        for _ in range(_RANDOM_PASSWORD_LENGTH)
    )


# ============================================================
# DOWNLOAD QUOTA STATE
# ============================================================
# Reads today's row from user_usage for the resource_downloads
# metric and computes the state panel shown on the Subscription
# tab. The metric_code written by entitlement_service.consume() is
# 'resource_downloads'. period_start is the Somali-local date in
# YYYY-MM-DD format.

DOWNLOAD_QUOTA_METRIC = 'resource_downloads'
DOWNLOAD_QUOTA_MAX_VALUE = 9999


def _get_download_quota_state(user_id: int) -> dict:
    """
    Return the current state of the user's daily download quota.

    Keys:
        metric         — the metric_code stored in user_usage
        period_start   — the Somali-local date string for today
        used           — integer count of downloads consumed today
        limit          — integer cap from the entitlement policy, or None if unlimited
        remaining      — integer remaining, or None if unlimited
        is_exhausted   — True if used >= limit
        is_unlimited   — True if limit is None
    """
    today = datetime.now(SOMALI_TIMEZONE).date().isoformat()

    # Policy limit for this user's effective tier
    limit = None
    try:
        raw = entitlement_service.get_limit(user_id, DOWNLOAD_QUOTA_METRIC)
        # get_limit returns:
        #   None  → unlimited
        #   int   → finite cap (0 means disabled)
        if raw is not None:
            limit = int(raw)
    except Exception as e:
        logger.warning(f"quota limit lookup failed for {user_id}: {e}")
        limit = None

    # Usage count for today
    used = 0
    try:
        row = execute_with_retry(
            "SELECT usage_count FROM user_usage "
            "WHERE user_id = ? AND metric_code = ? AND period_start = ?",
            (user_id, DOWNLOAD_QUOTA_METRIC, today),
        ).fetchone()
        if row:
            used = int(row['usage_count'] or 0)
    except Exception as e:
        logger.warning(f"quota usage lookup failed for {user_id}: {e}")
        used = 0

    is_unlimited = (limit is None)
    if is_unlimited:
        remaining = None
        is_exhausted = False
    else:
        remaining = max(0, limit - used)
        is_exhausted = used >= limit

    return {
        'metric':       DOWNLOAD_QUOTA_METRIC,
        'period_start': today,
        'used':         used,
        'limit':        limit,
        'remaining':    remaining,
        'is_exhausted': is_exhausted,
        'is_unlimited': is_unlimited,
    }


# ============================================================
# TIER DURATION PARSING
# ============================================================

_TIER_DURATION_DAYS = {7, 30, 90, 365}


def _parse_tier_duration(form) -> tuple:
    """
    Return (expires_at_iso_or_None, error_or_None).
    """
    duration = (form.get('duration') or '30').strip()

    if duration == 'permanent':
        return None, None

    if duration == 'custom':
        raw = (form.get('custom_date') or '').strip()
        if not raw:
            return None, 'Please pick a custom expiry date.'
        try:
            dt = datetime.strptime(raw, '%Y-%m-%d')
        except ValueError:
            return None, 'Invalid custom date format.'
        try:
            dt = dt.replace(tzinfo=SOMALI_TIMEZONE,
                            hour=23, minute=59, second=59)
        except Exception:
            pass
        return dt.isoformat(), None

    try:
        days = int(duration)
    except (ValueError, TypeError):
        return None, 'Invalid duration.'
    if days not in _TIER_DURATION_DAYS:
        return None, 'Invalid duration.'
    try:
        target = datetime.now(SOMALI_TIMEZONE) + timedelta(days=days)
    except Exception:
        target = datetime.utcnow() + timedelta(days=days)
    return target.isoformat(), None


# ============================================================
# LIFECYCLE STAGE COMPUTATION
# ============================================================

_TIER_EXPIRING_TOMORROW_WINDOW_HOURS = 48
_TIER_EXPIRING_SOON_WINDOW_DAYS = 7
_TIER_CHURN_WINDOW_DAYS = 30


def _compute_lifecycle_stage(user: dict) -> str:
    if not user:
        return ''

    tier = normalize_tier(user.get('tier') or 'free')

    if tier == 'premium':
        expires_raw = user.get('tier_expires_at')
        if not expires_raw:
            return 'active_premium'
        try:
            s = str(expires_raw).replace('Z', '+00:00')
            target = datetime.fromisoformat(s)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            delta = target - now
            if delta.total_seconds() < 0:
                return 'churned'
            if delta.total_seconds() <= _TIER_EXPIRING_TOMORROW_WINDOW_HOURS * 3600:
                return 'expiring_tomorrow'
            if delta.days <= _TIER_EXPIRING_SOON_WINDOW_DAYS:
                return 'expiring_soon'
            return 'active_premium'
        except Exception:
            return 'active_premium'

    updated_raw = user.get('tier_updated_at')
    if updated_raw:
        try:
            s = str(updated_raw).replace('Z', '+00:00')
            updated = datetime.fromisoformat(s)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if (now - updated).days <= _TIER_CHURN_WINDOW_DAYS:
                return 'churned'
        except Exception:
            pass

    return 'free'


# ============================================================
# LIFECYCLE NOTIFICATION TEMPLATES
# ============================================================

TIER_NOTIFICATION_TEMPLATES = {
    'tier_expiring_soon': {
        'title': 'Premium ends in {days} day{s}',
        'body': ('Your Premium access ends in {days} day{s}. '
                 'Renew for $1.00/month to keep premium PDFs (20/day), '
                 'progress analytics, history search, and personal insights.'),
        'icon': '⏳',
    },
    'tier_expiring_tomorrow': {
        'title': 'Premium ends {when}',
        'body': ('Your Premium access ends {when}. '
                 'Renew now for $1.00/month — your first month is 50% off '
                 'if you haven\'t used the discount yet.'),
        'icon': '🚨',
    },
    'tier_expired': {
        'title': 'Your Premium access has ended',
        'body': ('You are now on the Free tier. Free still includes '
                 'unlimited practice, hosting competitions, every group, '
                 'and 3 PDF downloads per day. To restore premium PDFs '
                 '(20/day), analytics, history search, and insights, '
                 'renew for $1.00/month.'),
        'icon': '⏰',
    },
    'tier_expired_followup': {
        'title': 'Still thinking about Premium?',
        'body': ('We noticed you haven\'t renewed Premium yet. If cost '
                 'is the issue, your first month is 50% off ($0.50). '
                 'If you have questions, reply in the WhatsApp group '
                 'and we\'ll help.'),
        'icon': '💬',
    },
    'tier_winback': {
        'title': 'Come back to Premium',
        'body': ('It\'s been two weeks. Students who use Premium '
                 'progress faster — unlimited study materials, analytics '
                 'to spot weak areas, and insights to study smarter. '
                 'Come back for $1.00/month, cancel anytime.'),
        'icon': '🎁',
    },
}

_TIER_NOTIFICATION_TYPES = tuple(TIER_NOTIFICATION_TEMPLATES.keys())


def _build_tier_notification(type_key: str, user: dict) -> dict:
    tpl = TIER_NOTIFICATION_TEMPLATES.get(type_key)
    if not tpl:
        return None

    title = tpl['title']
    body = tpl['body']
    icon = tpl['icon']

    if type_key == 'tier_expiring_soon':
        days = 3
        expires_raw = user.get('tier_expires_at')
        if expires_raw:
            try:
                s = str(expires_raw).replace('Z', '+00:00')
                target = datetime.fromisoformat(s)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
                delta = target - datetime.now(timezone.utc)
                days = max(0, int(delta.total_seconds() // 86400))
            except Exception:
                pass
        suffix = 's' if days != 1 else ''
        title = title.format(days=days, s=suffix)
        body = body.format(days=days, s=suffix)

    elif type_key == 'tier_expiring_tomorrow':
        when = 'tomorrow'
        expires_raw = user.get('tier_expires_at')
        if expires_raw:
            try:
                s = str(expires_raw).replace('Z', '+00:00')
                target = datetime.fromisoformat(s)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
                delta = target - datetime.now(timezone.utc)
                if delta.total_seconds() <= 0:
                    when = 'today'
                elif delta.days == 1:
                    when = 'tomorrow'
                else:
                    when = f'in {delta.days} days'
            except Exception:
                pass
        title = title.format(when=when)
        body = body.format(when=when)

    return {'title': title, 'body': body, 'icon': icon}


# ============================================================
# LIST
# ============================================================

@admin_users_bp.route('/users', methods=['GET'], endpoint='list_users')
@admin_can('users.view')
def list_users():
    ensure_admin_user_schema()

    search = (request.args.get('search') or '').strip()
    tier_filter = (request.args.get('tier') or '').strip().lower()
    location_filter = (request.args.get('location') or '').strip().upper()
    curriculum_filter = (request.args.get('curriculum') or '').strip().lower()
    verified_filter = (request.args.get('verified') or '').strip()
    only_admins = request.args.get('admins') == '1'
    only_inactive = request.args.get('inactive') == '1'
    sort = (request.args.get('sort') or 'newest').strip()
    page = max(1, int(request.args.get('page') or 1))
    per_page = 25

    users, total = get_users_admin(
        search=search, tier_filter=tier_filter,
        location_filter=location_filter, curriculum_filter=curriculum_filter,
        only_admins=only_admins, only_inactive=only_inactive,
        verified_filter=verified_filter,
        sort=sort, page=page, per_page=per_page,
    )
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    stats = get_users_admin_stats()

    for user in users:
        user['tier'] = user.get('tier') or 'free'
        user['lifecycle_stage'] = _compute_lifecycle_stage(user)

    return render_template(
        'dashboard/admin/access/users.html',
        users=users, total=total, page=page, per_page=per_page,
        total_pages=total_pages, stats=stats, search=search,
        tier_filter=tier_filter, location_filter=location_filter,
        curriculum_filter=curriculum_filter, verified_filter=verified_filter,
        only_admins=only_admins, only_inactive=only_inactive, sort=sort,
    )


# ============================================================
# EXPORT
# ============================================================

@admin_users_bp.route('/users/export', methods=['GET'], endpoint='users_export')
@admin_can('users.export')
def users_export():
    ensure_admin_user_schema()
    rows = get_users_admin_export(
        search=(request.args.get('search') or '').strip(),
        tier_filter=(request.args.get('tier') or '').strip().lower(),
        location_filter=(request.args.get('location') or '').strip().upper(),
        curriculum_filter=(request.args.get('curriculum') or '').strip().lower(),
        only_admins=request.args.get('admins') == '1',
        only_inactive=request.args.get('inactive') == '1',
        verified_filter=(request.args.get('verified') or '').strip(),
        sort=(request.args.get('sort') or 'newest').strip(),
    )

    write_audit(
        action='users.export', target_type='user',
        before=None, after={'count': len(rows)}, severity='info',
    )

    return Response(
        users_to_csv(rows),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=users_export.csv'},
    )


# ============================================================
# DETAIL
# ============================================================

@admin_users_bp.route('/users/<int:user_id>', methods=['GET'],
                      endpoint='user_detail')
@admin_can('users.view')
def user_detail(user_id):
    ensure_admin_user_schema()
    user = get_student_by_id(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin_users.list_users'))

    user['tier'] = user.get('tier') or 'free'
    user['lifecycle_stage'] = _compute_lifecycle_stage(user)

    quizzes = get_user_recent_quizzes_admin(user_id, limit=20)
    live_quizzes = get_user_recent_live_quizzes(user_id, limit=10)
    history = get_user_admin_history(user_id, limit=50)

    avg = 0
    if quizzes:
        avg = round(sum(q['percentage'] for q in quizzes) / len(quizzes), 1)

    session_info = None
    try:
        row = execute_with_retry(
            "SELECT last_login_at, last_login_ip, "
            "COALESCE(session_version, 0) AS sv "
            "FROM students WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row:
            session_info = dict(row)
    except Exception:
        session_info = None

    # Lifecycle notification history — 5 most recent tier_* notifications
    tier_notifications = []
    try:
        cursor = execute_with_retry("""
            SELECT type, title, body, icon, is_read, created_at
            FROM notifications
            WHERE user_id = ?
              AND type LIKE 'tier_%'
            ORDER BY created_at DESC
            LIMIT 5
        """, (user_id,))
        tier_notifications = [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.warning(f"tier notification history failed: {e}")

    # Download quota state (metric: resource_downloads)
    download_quota = _get_download_quota_state(user_id)

    suggested_password = _generate_random_password()

    return render_template(
        'dashboard/admin/access/user_detail.html',
        user=user,
        quizzes=quizzes,
        live_quizzes=live_quizzes,
        history=history,
        total_quizzes=len(quizzes),
        avg_score=avg,
        session_info=session_info,
        suggested_password=suggested_password,
        default_password_preset='',
        tier_notifications=tier_notifications,
        tier_notification_types=_TIER_NOTIFICATION_TYPES,
        download_quota=download_quota,
    )


# ============================================================
# DOWNLOAD QUOTA — RESET TO 0
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/quota/reset',
                      methods=['POST'],
                      endpoint='reset_download_quota')
@admin_can('users.reset_quota')
def reset_download_quota(user_id):
    validate_csrf()

    user = get_student_by_id(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin_users.list_users'))

    before = _get_download_quota_state(user_id)
    today = before['period_start']

    try:
        execute_with_retry("""
            INSERT INTO user_usage
                (user_id, metric_code, period_start, usage_count)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(user_id, metric_code, period_start) DO UPDATE SET
                usage_count = 0,
                updated_at = datetime('now', 'localtime')
        """, (user_id, DOWNLOAD_QUOTA_METRIC, today), commit=True)
    except Exception as e:
        logger.error(f"reset_download_quota failed for user {user_id}: {e}")
        flash('Failed to reset quota.', 'error')
        return redirect(url_for('admin_users.user_detail', user_id=user_id)
                        + '#subscription')

    write_audit(
        action='user.reset_download_quota',
        target_type='user',
        target_id=user_id,
        before={'used': before['used'], 'period_start': today},
        after={'used': 0, 'period_start': today},
        severity='warning',
    )

    name = user.get('first_name') or 'User'
    flash(f"{name}'s download quota reset to 0 for today.", 'success')
    return redirect(url_for('admin_users.user_detail', user_id=user_id)
                    + '#subscription')


# ============================================================
# DOWNLOAD QUOTA — SET TO CUSTOM VALUE
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/quota/set',
                      methods=['POST'],
                      endpoint='set_download_quota')
@admin_can('users.reset_quota')
def set_download_quota(user_id):
    validate_csrf()

    user = get_student_by_id(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin_users.list_users'))

    raw = (request.form.get('usage_count') or '').strip()
    try:
        value = int(raw)
    except (ValueError, TypeError):
        flash('Please enter a whole number between 0 and 9999.', 'error')
        return redirect(url_for('admin_users.user_detail', user_id=user_id)
                        + '#subscription')

    if value < 0 or value > DOWNLOAD_QUOTA_MAX_VALUE:
        flash(f'Value must be between 0 and {DOWNLOAD_QUOTA_MAX_VALUE}.', 'error')
        return redirect(url_for('admin_users.user_detail', user_id=user_id)
                        + '#subscription')

    before = _get_download_quota_state(user_id)
    today = before['period_start']

    try:
        execute_with_retry("""
            INSERT INTO user_usage
                (user_id, metric_code, period_start, usage_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, metric_code, period_start) DO UPDATE SET
                usage_count = ?,
                updated_at = datetime('now', 'localtime')
        """, (user_id, DOWNLOAD_QUOTA_METRIC, today, value, value),
            commit=True)
    except Exception as e:
        logger.error(f"set_download_quota failed for user {user_id}: {e}")
        flash('Failed to set quota.', 'error')
        return redirect(url_for('admin_users.user_detail', user_id=user_id)
                        + '#subscription')

    write_audit(
        action='user.set_download_quota',
        target_type='user',
        target_id=user_id,
        before={'used': before['used'], 'period_start': today},
        after={'used': value, 'period_start': today},
        severity='warning',
    )

    name = user.get('first_name') or 'User'
    flash(f"{name}'s download quota set to {value} for today.", 'success')
    return redirect(url_for('admin_users.user_detail', user_id=user_id)
                    + '#subscription')


# ============================================================
# PROFILE EDIT
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/profile', methods=['POST'],
                      endpoint='edit_profile')
@admin_can('users.view')
def edit_profile(user_id):
    validate_csrf()

    user = get_student_by_id(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin_users.list_users'))

    payload = {
        'first_name': (request.form.get('first_name') or '').strip(),
        'middle_name': (request.form.get('middle_name') or '').strip(),
        'last_name': (request.form.get('last_name') or '').strip(),
        'school': (request.form.get('school') or '').strip(),
        'grade': (request.form.get('grade') or '').strip(),
        'city': (request.form.get('city') or '').strip(),
        'location': (request.form.get('location') or '').strip(),
        'curriculum': (request.form.get('curriculum') or '').strip(),
        'phone_number': (request.form.get('phone_number') or '').strip(),
    }

    ok, msg, changed = update_user_profile(user_id, payload, session['user_id'])

    if ok and changed:
        write_audit(
            action='user.edit_profile',
            target_type='user',
            target_id=user_id,
            before=None,
            after={'changed_fields': list(changed.keys())},
            severity='warning',
        )
        flash(msg, 'success')
    elif ok:
        flash(msg or 'No changes.', 'info')
    else:
        flash(msg or 'Failed to update profile.', 'error')

    return redirect(url_for('admin_users.user_detail', user_id=user_id)
                    + '#profile')


# ============================================================
# VERIFY / UNVERIFY
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/verify', methods=['POST'],
                      endpoint='verify_user')
@admin_can('users.view')
def verify_user(user_id):
    validate_csrf()

    if user_id == session['user_id']:
        flash('You cannot verify your own account.', 'error')
        return redirect(url_for('admin_users.user_detail', user_id=user_id))

    user = get_student_by_id(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin_users.list_users'))

    if set_user_verified(user_id, True, session['user_id']):
        write_audit(
            action='user.verify', target_type='user', target_id=user_id,
            before={'is_verified': 0}, after={'is_verified': 1},
            severity='info',
        )
        try:
            create_notification(
                user_id=user_id,
                type='account',
                title='✅ Account Verified',
                body='Your account has been verified. You can now log in and start learning.',
                link='/login',
                icon='✅',
            )
        except Exception:
            pass
        flash('User verified.', 'success')
    else:
        flash('Failed to verify user.', 'error')

    return redirect(url_for('admin_users.user_detail', user_id=user_id)
                    + '#overview')


@admin_users_bp.route('/users/<int:user_id>/unverify', methods=['POST'],
                      endpoint='unverify_user')
@admin_can('users.view')
def unverify_user(user_id):
    validate_csrf()

    if user_id == session['user_id']:
        flash('You cannot unverify your own account.', 'error')
        return redirect(url_for('admin_users.user_detail', user_id=user_id))

    user = get_student_by_id(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin_users.list_users'))

    if set_user_verified(user_id, False, session['user_id']):
        write_audit(
            action='user.unverify', target_type='user', target_id=user_id,
            before={'is_verified': 1}, after={'is_verified': 0},
            severity='warning',
        )
        flash('User unverified.', 'info')
    else:
        flash('Failed to unverify user.', 'error')

    return redirect(url_for('admin_users.user_detail', user_id=user_id)
                    + '#overview')


# ============================================================
# NOTE
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/note', methods=['POST'],
                      endpoint='set_note')
@admin_can('users.view')
def set_note(user_id):
    validate_csrf()
    note = (request.form.get('note') or '').strip()
    ok = set_user_admin_note(user_id, note, session['user_id'])
    flash('Admin note saved.' if ok else 'Failed to save note.',
          'success' if ok else 'error')
    return redirect(url_for('admin_users.user_detail', user_id=user_id)
                    + '#overview')


# ============================================================
# TIER
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/tier', methods=['POST'],
                      endpoint='set_tier')
@admin_can('users.set_tier')
def set_tier(user_id):
    validate_csrf()
    new_tier = (request.form.get('tier') or '').strip().lower()
    if set_user_tier_admin(user_id, new_tier, session['user_id']):
        write_audit(
            action='user.set_tier', target_type='user', target_id=user_id,
            before=None, after={'tier': new_tier}, severity='warning',
        )
        flash(f'Tier updated to {new_tier.upper()}.', 'success')
    else:
        flash('Failed to update tier.', 'error')
    return redirect(url_for('admin_users.user_detail', user_id=user_id)
                    + '#subscription')


# ============================================================
# TOGGLE ADMIN
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/toggle-admin', methods=['POST'],
                      endpoint='toggle_admin')
@admin_can('users.toggle_admin')
def toggle_admin(user_id):
    validate_csrf()
    if user_id == session['user_id']:
        flash('You cannot change your own admin status.', 'error')
        return redirect(url_for('admin_users.user_detail', user_id=user_id))

    new_state = toggle_user_admin_admin(user_id, session['user_id'])
    if new_state is None:
        flash('Failed to change admin status.', 'error')
    else:
        write_audit(
            action='user.toggle_admin', target_type='user', target_id=user_id,
            before=None, after={'is_admin': new_state}, severity='critical',
        )
        flash('Admin privileges granted.' if new_state
              else 'Admin privileges revoked.', 'success')
    return redirect(url_for('admin_users.user_detail', user_id=user_id)
                    + '#controls')


# ============================================================
# NOTIFY
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/notify', methods=['POST'],
                      endpoint='notify')
@admin_can('users.notify')
def notify(user_id):
    validate_csrf()
    title = (request.form.get('title') or '').strip()
    body = (request.form.get('body') or '').strip()
    if not title or not body:
        flash('Title and message are required.', 'error')
        return redirect(url_for('admin_users.user_detail', user_id=user_id)
                        + '#overview')

    create_notification(user_id, 'admin_direct', title, body, '/dashboard', '📬')
    log_admin_user_action(session['user_id'], user_id, 'notify', None, title[:200])

    write_audit(
        action='user.notify', target_type='user', target_id=user_id,
        before=None, after={'title': title}, severity='info',
    )
    flash('Notification sent.', 'success')
    return redirect(url_for('admin_users.user_detail', user_id=user_id)
                    + '#overview')


# ============================================================
# TRIGGER LIFECYCLE NOTIFICATION
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/trigger-tier-notification/<type_key>',
                      methods=['POST'], endpoint='trigger_tier_notification')
@admin_can('users.notify')
def trigger_tier_notification(user_id, type_key):
    validate_csrf()

    if type_key not in TIER_NOTIFICATION_TEMPLATES:
        flash(f'Unknown notification type: {type_key}', 'error')
        return redirect(url_for('admin_users.user_detail', user_id=user_id)
                        + '#subscription')

    user = get_student_by_id(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin_users.list_users'))

    payload = _build_tier_notification(type_key, user)
    if not payload:
        flash('Could not build notification payload.', 'error')
        return redirect(url_for('admin_users.user_detail', user_id=user_id)
                        + '#subscription')

    try:
        create_notification(
            user_id=user_id,
            type=type_key,
            title=payload['title'],
            body=payload['body'],
            link='/home?show_upgrade=1',
            icon=payload['icon'],
        )
    except Exception as e:
        logger.error(f"trigger_tier_notification failed for user {user_id}: {e}")
        flash('Failed to send notification.', 'error')
        return redirect(url_for('admin_users.user_detail', user_id=user_id)
                        + '#subscription')

    log_admin_user_action(
        session['user_id'], user_id,
        'trigger_tier_notification', None, type_key,
    )

    write_audit(
        action='user.trigger_tier_notification',
        target_type='user',
        target_id=user_id,
        before=None,
        after={'type': type_key, 'title': payload['title']},
        severity='info',
    )

    flash(f'Lifecycle notification sent: {payload["title"]}', 'success')
    return redirect(url_for('admin_users.user_detail', user_id=user_id)
                    + '#subscription')


# ============================================================
# PASSWORD — custom
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/set-password', methods=['POST'],
                      endpoint='set_password')
@admin_can('users.reset_password')
def set_password(user_id):
    validate_csrf()

    if user_id == session['user_id']:
        flash('Use Settings → Security to change your own password.', 'error')
        return redirect(url_for('admin_users.user_detail', user_id=user_id))

    new_pw = (request.form.get('new_password') or '').strip()
    confirm = (request.form.get('confirm_password') or '').strip()
    force_logout = request.form.get('force_logout') == '1'
    notify_user = request.form.get('notify_user') == '1'

    if not new_pw:
        flash('Password is required.', 'error')
        return redirect(url_for('admin_users.user_detail', user_id=user_id)
                        + '#controls')

    if len(new_pw) < 8:
        flash('Password must be at least 8 characters.', 'error')
        return redirect(url_for('admin_users.user_detail', user_id=user_id)
                        + '#controls')

    if new_pw != confirm:
        flash('Passwords do not match.', 'error')
        return redirect(url_for('admin_users.user_detail', user_id=user_id)
                        + '#controls')

    ok, msg = reset_user_password(
        user_id, new_pw, session['user_id'],
        force_logout=force_logout, notify_user=notify_user,
    )

    if ok:
        write_audit(
            action='user.reset_password', target_type='user', target_id=user_id,
            before=None,
            after={'mode': 'custom',
                   'force_logout': force_logout,
                   'notify_user': notify_user},
            severity='critical',
        )
        flash(msg, 'success')
    else:
        flash(msg or 'Failed to update password.', 'error')

    return redirect(url_for('admin_users.user_detail', user_id=user_id)
                    + '#controls')


# ============================================================
# PASSWORD — random reset
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/reset-password-random',
                      methods=['POST'],
                      endpoint='reset_password_random')
@admin_can('users.reset_password')
def reset_password_random(user_id):
    validate_csrf()

    if user_id == session['user_id']:
        flash('Use Settings → Security to change your own password.', 'error')
        return redirect(url_for('admin_users.user_detail', user_id=user_id))

    new_pw = _generate_random_password()
    ok, msg = reset_user_password_to_default(
        user_id,
        session['user_id'],
        default_password=new_pw,
        force_logout=True,
        notify_user=True,
    )

    if ok:
        write_audit(
            action='user.reset_password',
            target_type='user',
            target_id=user_id,
            before=None,
            after={'mode': 'random'},
            severity='critical',
        )
        flash(
            f'Password reset. Give this to the user: {new_pw} '
            f'(they must change it after first login).',
            'success',
        )
    else:
        flash(msg or 'Failed to reset password.', 'error')

    return redirect(url_for('admin_users.user_detail', user_id=user_id)
                    + '#controls')


@admin_users_bp.route('/users/<int:user_id>/reset-password-default',
                      methods=['POST'],
                      endpoint='reset_password_to_default')
@admin_can('users.reset_password')
def reset_password_to_default_legacy(user_id):
    return reset_password_random(user_id)


# ============================================================
# FORCE LOGOUT
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/force-logout', methods=['POST'],
                      endpoint='force_logout')
@admin_can('users.force_logout')
def force_logout(user_id):
    validate_csrf()
    ok = force_user_logout(user_id, session['user_id'])
    if ok:
        write_audit(
            action='user.force_logout', target_type='user', target_id=user_id,
            before=None, after=None, severity='warning',
        )
    flash('User will be logged out on next request.' if ok
          else 'Failed to force logout.',
          'success' if ok else 'error')
    return redirect(url_for('admin_users.user_detail', user_id=user_id)
                    + '#controls')


# ============================================================
# PUBLIC ID
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/public-id', methods=['POST'],
                      endpoint='set_public_id')
@admin_can('users.view')
def set_public_id(user_id):
    validate_csrf()

    new_id = ''
    if request.is_json:
        data = request.get_json(silent=True) or {}
        new_id = (data.get('public_id') or '').strip().upper()
    else:
        new_id = (request.form.get('public_id') or '').strip().upper()

    ok, msg = set_user_public_id(user_id, new_id, session['user_id'])
    if ok:
        write_audit(
            action='user.set_public_id', target_type='user', target_id=user_id,
            before=None, after={'public_id': new_id}, severity='info',
        )
    flash(msg, 'success' if ok else 'error')
    return redirect(url_for('admin_users.user_detail', user_id=user_id)
                    + '#profile')


# ============================================================
# DELETE
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/delete', methods=['POST'],
                      endpoint='delete_user')
@admin_can('users.delete')
def delete_user(user_id):
    validate_csrf()
    if user_id == session['user_id']:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('admin_users.list_users'))

    keep_ratings = request.form.get('keep_ratings', 'on') == 'on'
    delete_attempts = request.form.get('delete_attempts', 'on') == 'on'

    success, message = db_delete_user(user_id, session['user_id'],
                                      keep_ratings, delete_attempts)
    if success:
        try:
            log_admin_user_action(session['user_id'], user_id, 'delete')
        except Exception:
            pass
        write_audit(
            action='user.delete', target_type='user', target_id=user_id,
            before=None, after=None, severity='critical',
        )
        flash('User deleted successfully.', 'success')
        return redirect(url_for('admin_users.list_users'))

    flash(f'Error deleting user: {message}', 'error')
    return redirect(url_for('admin_users.user_detail', user_id=user_id))


# ============================================================
# BULK
# ============================================================

@admin_users_bp.route('/users/bulk', methods=['POST'], endpoint='users_bulk')
@admin_can('users.bulk')
def users_bulk():
    validate_csrf()
    action = (request.form.get('action') or '').strip()
    ids = request.form.getlist('user_ids')

    if not ids:
        flash('No users selected.', 'error')
        return redirect(request.referrer or url_for('admin_users.list_users'))

    try:
        user_ids = [int(x) for x in ids]
    except ValueError:
        flash('Invalid user selection.', 'error')
        return redirect(request.referrer or url_for('admin_users.list_users'))

    user_ids = [u for u in user_ids
                if u != session['user_id'] or action not in ('delete', 'demote_admin')]

    extra = {}
    if action == 'set_tier':
        extra['tier'] = (request.form.get('bulk_tier') or '').strip().lower()
    elif action == 'notify':
        extra['title'] = (request.form.get('bulk_title') or '').strip()
        extra['body'] = (request.form.get('bulk_body') or '').strip()

    if action == 'reset_password_default' or action == 'reset_password_random':
        from werkzeug.security import generate_password_hash
        succeeded = 0
        failed = 0
        batch_password = _generate_random_password()
        pw_hash = generate_password_hash(batch_password)

        for uid in user_ids:
            try:
                execute_with_retry(
                    "UPDATE students SET password = ? WHERE id = ?",
                    (pw_hash, uid), commit=True,
                )
                try:
                    execute_with_retry(
                        "UPDATE students SET session_version = "
                        "COALESCE(session_version, 0) + 1 WHERE id = ?",
                        (uid,), commit=True,
                    )
                except Exception:
                    pass
                log_admin_user_action(session['user_id'], uid,
                                      'reset_password', None, 'random')
                succeeded += 1
            except Exception:
                failed += 1

        write_audit(
            action='users.bulk_reset_password_random',
            target_type='user', before=None,
            after={'succeeded': succeeded, 'failed': failed},
            severity='critical',
        )

        if succeeded:
            flash(
                f'Bulk reset: {succeeded} user(s). '
                f'Shared password for this batch: {batch_password}',
                'success',
            )
        if failed:
            flash(f'Bulk reset: {failed} failed.', 'error')
        return redirect(request.referrer or url_for('admin_users.list_users'))

    succeeded, failed = bulk_user_action(action, user_ids,
                                         session['user_id'], extra)

    write_audit(
        action=f'users.bulk_{action}', target_type='user',
        before=None, after={'succeeded': succeeded, 'failed': failed},
        severity='warning',
    )

    if succeeded:
        flash(f'Bulk {action}: {succeeded} succeeded.', 'success')
    if failed:
        flash(f'Bulk {action}: {failed} skipped or failed.', 'error')

    return redirect(request.referrer or url_for('admin_users.list_users'))


# ============================================================
# TIER MANAGEMENT PAGE
# ============================================================

@admin_users_bp.route('/users/tier/<int:user_id>', methods=['GET', 'POST'],
                      endpoint='manage_user_tier')
@admin_can('users.set_tier')
def manage_user_tier(user_id):
    user = get_student_by_id(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin_users.list_users'))

    current_tier = normalize_tier(user.get('tier') or 'free')
    current_expiry = user.get('tier_expires_at') or None
    admin_tier = get_current_user_tier()

    if request.method == 'POST':
        validate_csrf()
        new_tier = normalize_tier(request.form.get('tier') or '')
        if new_tier not in ('free', 'premium'):
            flash('Invalid tier value.', 'error')
            return redirect(url_for('admin_users.manage_user_tier',
                                    user_id=user_id))

        expires_at = None
        if new_tier == 'premium':
            expires_at, err = _parse_tier_duration(request.form)
            if err:
                flash(err, 'error')
                return redirect(url_for('admin_users.manage_user_tier',
                                        user_id=user_id))

        reason = (request.form.get('reason') or '').strip() or None

        if set_user_tier_admin(
            user_id, new_tier, session['user_id'],
            expires_at=expires_at, reason=reason,
        ):
            log_admin_action(
                'tier.change',
                f"Admin {session['user_id']} set tier of {user_id} "
                f"to {new_tier}"
                + (f" (until {expires_at[:10]})" if expires_at else " (permanent)"),
                'warning',
            )
            write_audit(
                action='user.set_tier', target_type='user', target_id=user_id,
                before={'tier': current_tier, 'expires_at': current_expiry},
                after={'tier': new_tier, 'expires_at': expires_at},
                severity='warning',
            )
            if new_tier == 'premium':
                if expires_at:
                    flash(f"Tier set to Premium until {expires_at[:10]}.", 'success')
                else:
                    flash("Tier set to Premium (permanent).", 'success')
            else:
                flash("Tier set to Free.", 'success')
        else:
            flash('Failed to update tier.', 'error')

        return redirect(url_for('admin_users.user_detail', user_id=user_id)
                        + '#subscription')

    return render_template(
        'dashboard/admin/access/user_tier.html',
        user=user,
        current_tier=current_tier,
        current_expiry=current_expiry,
        admin_tier=admin_tier,
    )


# ============================================================
# DELETED USERS
# ============================================================

@admin_users_bp.route('/deleted-users', methods=['GET'],
                      endpoint='deleted_users')
@admin_can('users.restore')
def deleted_users():
    return render_template(
        'dashboard/admin/access/users_deleted.html',
        deleted=get_deleted_users(),
    )


@admin_users_bp.route('/deleted-users/restore/<int:deleted_id>',
                      methods=['POST'],
                      endpoint='restore_deleted_user')
@admin_can('users.restore')
def restore_deleted_user(deleted_id):
    validate_csrf()
    success, message = db_restore_user(deleted_id)

    if success:
        log_admin_action(
            'user.restore',
            f"Restored user from deleted_id {deleted_id}", 'info',
        )
        write_audit(
            action='user.restore', target_type='user',
            before=None, after={'deleted_id': deleted_id},
            severity='warning',
        )
        flash('User restored successfully!', 'success')
    else:
        flash(f'Error restoring user: {message}', 'error')

    return redirect(url_for('admin_users.deleted_users'))


# ============================================================
# JSON — DRAWER DATA
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/json', methods=['GET'],
                      endpoint='user_json')
@admin_can('users.view')
def user_json(user_id):
    user = get_student_by_id(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    def _count(sql, params):
        try:
            row = execute_with_retry(sql, params).fetchone()
            return int(list(row)[0] or 0) if row else 0
        except Exception:
            return 0

    quiz_attempts = _count(
        "SELECT COUNT(*) FROM quiz_attempts WHERE student_id = ?",
        (user_id,),
    )
    live_attempts = _count(
        "SELECT COUNT(*) FROM live_quiz_participants WHERE student_id = ?",
        (user_id,),
    )
    saved_questions = _count(
        "SELECT COUNT(*) FROM question_interactions "
        "WHERE user_id = ? AND interaction_type = 'save'",
        (user_id,),
    )

    is_self = (user_id == session.get('user_id'))
    can = {
        'verify':       bool(has_capability('users.view'))        and not is_self,
        'set_tier':     bool(has_capability('users.set_tier'))    and not is_self,
        'notify':       bool(has_capability('users.notify')),
        'force_logout': bool(has_capability('users.force_logout')) and not is_self,
        'toggle_admin': bool(has_capability('users.toggle_admin')) and not is_self,
    }

    return jsonify({
        'id': user_id,
        'public_id': user.get('public_id') or '',
        'first_name': user.get('first_name') or '',
        'middle_name': user.get('middle_name') or '',
        'last_name': user.get('last_name') or '',
        'full_name': (f"{user.get('first_name') or ''} {user.get('last_name') or ''}").strip() or 'Unknown',
        'phone': user.get('phone_number') or '',
        'school': user.get('school') or '',
        'city': user.get('city') or '',
        'location': user.get('location') or '',
        'grade': user.get('grade') or '',
        'curriculum': user.get('curriculum') or '',
        'tier': user.get('tier') or 'free',
        'tier_expires_at': user.get('tier_expires_at'),
        'lifecycle_stage': _compute_lifecycle_stage(user),
        'is_verified': bool(user.get('is_verified')),
        'is_admin': bool(user.get('is_admin')),
        'total_points': int(user.get('total_points') or 0),
        'created_at': user.get('created_at'),
        'last_login_at': user.get('last_login_at'),
        'stats': {
            'quiz_attempts': quiz_attempts,
            'live_quiz_attempts': live_attempts,
            'saved_questions': saved_questions,
        },
        'can': can,
        'detail_url': url_for('admin_users.user_detail', user_id=user_id),
    })


# ============================================================
# JSON — DRAWER QUICK ACTIONS
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/quick-action', methods=['POST'],
                      endpoint='user_quick_action')
def user_quick_action(user_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401

    if not validate_csrf():
        return jsonify({'success': False, 'error': 'CSRF token missing'}), 403

    data = request.get_json(silent=True) or {}
    action = (data.get('action') or '').strip()

    user = get_student_by_id(user_id)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    is_self = (user_id == session['user_id'])

    if action == 'verify':
        if not has_capability('users.view'):
            return jsonify({'success': False, 'error': 'Permission denied'}), 403
        if is_self:
            return jsonify({'success': False, 'error': 'You cannot verify your own account'}), 400
        if not set_user_verified(user_id, True, session['user_id']):
            return jsonify({'success': False, 'error': 'Failed to verify user'}), 500
        write_audit(
            action='user.verify', target_type='user', target_id=user_id,
            before={'is_verified': 0}, after={'is_verified': 1}, severity='info',
        )
        try:
            create_notification(
                user_id=user_id, type='account',
                title='✅ Account Verified',
                body='Your account has been verified. You can now log in and start learning.',
                link='/login', icon='✅',
            )
        except Exception:
            pass
        return jsonify({
            'success': True, 'message': 'User verified',
            'row_updates': {'verified': True},
        })

    if action == 'unverify':
        if not has_capability('users.view'):
            return jsonify({'success': False, 'error': 'Permission denied'}), 403
        if is_self:
            return jsonify({'success': False, 'error': 'You cannot unverify your own account'}), 400
        if not set_user_verified(user_id, False, session['user_id']):
            return jsonify({'success': False, 'error': 'Failed to unverify user'}), 500
        write_audit(
            action='user.unverify', target_type='user', target_id=user_id,
            before={'is_verified': 1}, after={'is_verified': 0}, severity='warning',
        )
        return jsonify({
            'success': True, 'message': 'User unverified',
            'row_updates': {'verified': False},
        })

    if action == 'set_tier':
        if not has_capability('users.set_tier'):
            return jsonify({'success': False, 'error': 'Permission denied'}), 403
        if is_self:
            return jsonify({'success': False, 'error': 'You cannot change your own tier'}), 400
        new_tier = (data.get('tier') or '').strip().lower()
        if new_tier not in ('free', 'premium'):
            return jsonify({'success': False, 'error': 'Invalid tier'}), 400

        expires_at = None
        raw_exp = data.get('expires_at')
        if new_tier == 'premium' and raw_exp:
            raw_exp = str(raw_exp).strip()
            if raw_exp == 'permanent':
                expires_at = None
            elif raw_exp.isdigit():
                days = int(raw_exp)
                if days not in _TIER_DURATION_DAYS:
                    return jsonify({'success': False, 'error': 'Invalid duration'}), 400
                try:
                    target = datetime.now(SOMALI_TIMEZONE) + timedelta(days=days)
                except Exception:
                    target = datetime.utcnow() + timedelta(days=days)
                expires_at = target.isoformat()
            else:
                expires_at = raw_exp

        if not set_user_tier_admin(
            user_id, new_tier, session['user_id'], expires_at=expires_at,
        ):
            return jsonify({'success': False, 'error': 'Failed to update tier'}), 500

        write_audit(
            action='user.set_tier', target_type='user', target_id=user_id,
            before={'tier': user.get('tier'), 'expires_at': user.get('tier_expires_at')},
            after={'tier': new_tier, 'expires_at': expires_at},
            severity='warning',
        )

        if new_tier == 'premium':
            if expires_at:
                msg = f'Tier updated to PREMIUM until {expires_at[:10]}'
            else:
                msg = 'Tier updated to PREMIUM (permanent)'
        else:
            msg = 'Tier updated to FREE'

        return jsonify({
            'success': True,
            'message': msg,
            'row_updates': {
                'tier': new_tier,
                'expires_at': expires_at,
            },
        })

    if action == 'notify':
        if not has_capability('users.notify'):
            return jsonify({'success': False, 'error': 'Permission denied'}), 403
        title = (data.get('title') or '').strip()[:100]
        body = (data.get('body') or '').strip()[:500]
        if not title or not body:
            return jsonify({'success': False, 'error': 'Title and message required'}), 400
        create_notification(user_id, 'admin_direct', title, body, '/dashboard', '📬')
        try:
            log_admin_user_action(session['user_id'], user_id, 'notify', None, title[:200])
        except Exception:
            pass
        write_audit(
            action='user.notify', target_type='user', target_id=user_id,
            before=None, after={'title': title}, severity='info',
        )
        return jsonify({'success': True, 'message': 'Notification sent'})

    if action == 'force_logout':
        if not has_capability('users.force_logout'):
            return jsonify({'success': False, 'error': 'Permission denied'}), 403
        if is_self:
            return jsonify({'success': False, 'error': 'You cannot force logout yourself'}), 400
        if not force_user_logout(user_id, session['user_id']):
            return jsonify({'success': False, 'error': 'Failed to force logout'}), 500
        write_audit(
            action='user.force_logout', target_type='user', target_id=user_id,
            before=None, after=None, severity='warning',
        )
        return jsonify({'success': True, 'message': 'User will be logged out on their next request'})

    if action == 'toggle_admin':
        if not has_capability('users.toggle_admin'):
            return jsonify({'success': False, 'error': 'Permission denied'}), 403
        if is_self:
            return jsonify({'success': False, 'error': 'You cannot change your own admin status'}), 400
        new_state = toggle_user_admin_admin(user_id, session['user_id'])
        if new_state is None:
            return jsonify({'success': False, 'error': 'Failed to change admin status'}), 500
        write_audit(
            action='user.toggle_admin', target_type='user', target_id=user_id,
            before=None, after={'is_admin': new_state}, severity='critical',
        )
        return jsonify({
            'success': True,
            'message': 'Admin privileges granted' if new_state else 'Admin privileges revoked',
            'row_updates': {'is_admin': new_state},
        })

    return jsonify({'success': False, 'error': f'Unknown action: {action}'}), 400