# ============================================================
# blueprints/admin/system_bp.py
# System domain — admin landing page, global search, maintenance
# mode, announcements, and admin broadcasts.
#
# Routes:
#   GET  /admin/                                   → landing page (overview)
#   GET  /admin/search                             → global search
#   GET  /admin/maintenance                        → maintenance form
#   POST /admin/maintenance                        → save maintenance
#   GET  /admin/announcement                       → announcement form
#   POST /admin/announcement                       → send announcement
#   GET  /admin/broadcast                          → broadcast compose
#
# NOTE: The POST handler for /admin/broadcast lives in admin_platform_bp.
# This file only serves the compose page (GET).
#
# Maintenance state is stored as JSON in  <project>/instance/maintenance.json
# (user_settings has a FK on students.id, so user_id=0 is not usable as
#  a "platform" sentinel row.)
# ============================================================

import os
import json
import time
import logging

from flask import (
    Blueprint, render_template, request, session, flash,
    redirect, url_for, abort, jsonify,
)

from config import Config
from db import (
    execute_with_retry,
    get_student_by_id,
    create_notification_for_all_users,
)
from utils import validate_csrf
from services.admin.guards import admin_can
from services.admin.roles import is_super_admin
from services.admin.audit import write_audit

logger = logging.getLogger(__name__)

admin_system_bp = Blueprint('admin_system', __name__, url_prefix='/admin')


# ============================================================
# PATHS
# ============================================================

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
)))
_INSTANCE_DIR = os.path.join(_BASE_DIR, 'instance')
_MAINTENANCE_FILE = os.path.join(_INSTANCE_DIR, 'maintenance.json')


# ============================================================
# HELPERS
# ============================================================

def _csrf_ok():
    token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    return bool(token) and token == session.get('csrf_token')


def _scalar(sql, params=()):
    try:
        cursor = execute_with_retry(sql, params)
        row = cursor.fetchone()
        if not row:
            return 0
        return int(list(row)[0] or 0)
    except Exception as e:
        logger.debug(f"scalar failed: {e}")
        return 0


def _rows(sql, params=()):
    try:
        cursor = execute_with_retry(sql, params)
        return [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.debug(f"rows failed: {e}")
        return []


def _human_uptime(seconds):
    seconds = int(seconds)
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _disk_stats():
    try:
        stat = os.statvfs(Config.BACKUP_DIR)
        free_b = stat.f_bavail * stat.f_frsize
        total_b = stat.f_blocks * stat.f_frsize
        return {
            'free_gb': round(free_b / (1024 ** 3), 2),
            'free_mb': round(free_b / (1024 ** 2), 1),
            'used_pct': round(100.0 * (1 - free_b / total_b), 1) if total_b else 0,
        }
    except Exception:
        return {'free_gb': 0, 'free_mb': 0, 'used_pct': 0}


def _backup_health():
    try:
        from backup import BackupManager
        mgr = BackupManager()
        summary = mgr.get_backup_health_summary()
        last = summary.get('last_created')
        age_days = None
        if last:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(str(last).replace('Z', '+00:00'))
                age_days = max(0, int((datetime.now() - dt).days))
            except Exception:
                age_days = None
        return {
            'count': summary.get('total_backups', 0),
            'valid': summary.get('valid_backups', 0),
            'age_days': age_days,
            'last_good': summary.get('last_good'),
        }
    except Exception as e:
        logger.debug(f"backup health failed: {e}")
        return {'count': 0, 'valid': 0, 'age_days': None, 'last_good': None}


# ============================================================
# LANDING PAGE — /admin/
# ============================================================

@admin_system_bp.route('/', methods=['GET'], endpoint='dashboard')
def dashboard():
    """
    Dual-mode admin landing page.
    - Super admin (platform.view): control centre with pulse + alerts.
    - Regular admin: workbench with pending items and own recent activity.
    """
    if 'user_id' not in session:
        return redirect(url_for('auth.login', next=request.url))

    from services.admin.roles import is_any_admin
    if not is_any_admin():
        abort(404)

    is_super = admin_can('platform.view')

    if is_super:
        pulse = _build_pulse()
        alerts = _build_alerts(pulse)
        system = _build_system_block()
        recent_actions = _build_recent_actions()

        return render_template(
            'dashboard/admin/overview.html',
            pulse=pulse,
            alerts=alerts,
            system=system,
            recent_actions=recent_actions,
            pending={'total': 0, 'items': []},
            recent_own_actions=[],
        )
    else:
        pending = _build_pending_work()
        recent_own = _build_recent_own_actions()

        return render_template(
            'dashboard/admin/overview.html',
            pulse={},
            alerts=[],
            system={},
            recent_actions=[],
            pending=pending,
            recent_own_actions=recent_own,
        )


def _build_pulse():
    stats = {}
    try:
        from platform_activity import get_platform_stats
        stats = get_platform_stats()
    except Exception:
        pass

    revenue_today_cents = 0
    revenue_month_cents = 0
    try:
        cursor = execute_with_retry("""
            SELECT COALESCE(SUM(final_price_cents), 0) AS c
            FROM upgrade_requests
            WHERE status = 'approved'
              AND approved_at >= datetime('now', '-1 day')
        """)
        row = cursor.fetchone()
        revenue_today_cents = int(row['c'] or 0) if row else 0

        cursor = execute_with_retry("""
            SELECT COALESCE(SUM(final_price_cents), 0) AS c
            FROM upgrade_requests
            WHERE status = 'approved'
              AND approved_at >= datetime('now', '-30 days')
        """)
        row = cursor.fetchone()
        revenue_month_cents = int(row['c'] or 0) if row else 0
    except Exception:
        pass

    backup = _backup_health()
    disk = _disk_stats()

    return {
        'users_total':      stats.get('users_total', 0),
        'users_delta':      0,
        'users_new_today':  stats.get('users_today', 0),
        'quizzes_today':    stats.get('quizzes_today', 0),
        'quizzes_delta':    0,
        'quizzes_week':     stats.get('quizzes_week', 0),
        'upgrades_pending': stats.get('upgrades_pending', 0),
        'revenue_today':    round(revenue_today_cents / 100, 2),
        'revenue_month':    round(revenue_month_cents / 100, 2),
        'errors_open':      stats.get('errors_open', 0),
        'errors_today':     stats.get('errors_today', 0),
        'backup_age_days':  backup['age_days'],
        'backup_count':     backup['count'],
        'disk_free_mb':     disk['free_mb'],
        'disk_free_gb':     disk['free_gb'],
        'disk_used_pct':    disk['used_pct'],
    }


def _build_alerts(pulse):
    alerts = []

    if pulse.get('upgrades_pending', 0) > 0:
        alerts.append({
            'icon': '🚀',
            'title': f"{pulse['upgrades_pending']} pending upgrade request(s)",
            'subtitle': 'Users are waiting for approval.',
            'link': url_for('upgrade.admin_list', status='pending'),
        })

    if pulse.get('errors_open', 0) > 5:
        alerts.append({
            'icon': '⚠️',
            'title': f"{pulse['errors_open']} unresolved errors",
            'subtitle': 'Consider triaging the error log.',
            'link': url_for('admin_errors.index', resolved='0'),
        })

    age = pulse.get('backup_age_days')
    if age is None or age > 3:
        alerts.append({
            'icon': '💾',
            'title': 'Backup overdue',
            'subtitle': (
                f"Last backup was {age} day(s) ago."
                if age is not None else "No backup found."
            ),
            'link': url_for('admin_backup.dashboard'),
        })

    if pulse.get('disk_used_pct', 0) >= 85:
        alerts.append({
            'icon': '💽',
            'title': f"Disk {pulse['disk_used_pct']}% full",
            'subtitle': f"Only {pulse['disk_free_gb']} GB left.",
            'link': url_for('admin_ops.system_info'),
        })

    return alerts


def _build_system_block():
    db_size_mb = 0
    try:
        db_path = Config.DATABASE_PATH
        if os.path.exists(db_path):
            db_size_mb = os.path.getsize(db_path) / (1024 * 1024)
    except Exception:
        pass

    cache_size = 0
    try:
        from cache import get_cache_manager
        mgr = get_cache_manager()
        metrics = mgr.get_metrics() or {}
        local = metrics.get('local', {}) or {}
        cache_size = int(local.get('size', 0) or 0)
    except Exception:
        pass

    session_count = 0
    try:
        session_count = _scalar(
            "SELECT COUNT(*) FROM students "
            "WHERE last_login_at >= datetime('now', '-1 day')"
        )
    except Exception:
        pass

    uptime = _human_uptime(3600)

    return {
        'uptime': uptime,
        'cache_size': f"{cache_size} entries",
        'session_count': session_count,
        'db_size': f"{db_size_mb:.1f} MB",
    }


def _build_recent_actions():
    try:
        rows = _rows("""
            SELECT a.action, a.target_type, a.created_at, a.severity,
                   s.first_name, s.last_name
            FROM admin_audit_log a
            LEFT JOIN students s ON s.id = a.actor_id
            ORDER BY a.created_at DESC
            LIMIT 10
        """)
    except Exception:
        rows = []

    out = []
    for r in rows:
        actor = f"{r.get('first_name') or 'System'} {r.get('last_name') or ''}".strip()
        out.append({
            'icon': '⚙️' if r.get('severity') == 'info' else '⚠️',
            'actor_name': actor,
            'description': r.get('action', ''),
            'time': (r.get('created_at') or '')[:16],
        })
    return out


def _build_pending_work():
    items = []

    try:
        n = _scalar(
            "SELECT COUNT(*) FROM question_interactions "
            "WHERE interaction_type = 'report' AND report_status = 'pending'"
        )
        if n > 0:
            items.append({
                'icon': '🚩',
                'label': 'Reported questions',
                'description': 'Flagged by users, awaiting resolution.',
                'count': n,
                'link': url_for('admin_community.reports'),
            })
    except Exception:
        pass

    try:
        from bot.db import count_pending_pdfs
        n = count_pending_pdfs()
        if n > 0:
            items.append({
                'icon': '📄',
                'label': 'PDFs waiting for intake',
                'description': 'Telegram uploads not yet processed.',
                'count': n,
                'link': url_for('pdf_admin.pending_list'),
            })
    except Exception:
        pass

    total = sum(i['count'] for i in items)
    return {'total': total, 'items': items}


def _build_recent_own_actions():
    uid = session.get('user_id')
    if not uid:
        return []
    rows = _rows("""
        SELECT action, target_type, created_at, severity
        FROM admin_audit_log
        WHERE actor_id = ?
        ORDER BY created_at DESC
        LIMIT 10
    """, (uid,))
    out = []
    for r in rows:
        out.append({
            'icon': '✅' if r.get('severity') == 'info' else '⚠️',
            'description': f"{r.get('action', '')}",
            'time': (r.get('created_at') or '')[:16],
        })
    return out


# ============================================================
# GLOBAL SEARCH
# ============================================================

@admin_system_bp.route('/search', methods=['GET'], endpoint='search')
@admin_can('search.use')
def search():
    q = (request.args.get('q') or '').strip()
    users = []
    questions = []
    pdfs = []
    groups = []

    if q:
        like = f"%{q}%"
        limit = 10

        try:
            users = _rows("""
                SELECT id, public_id, first_name, middle_name, last_name,
                       phone_number, school, tier, is_admin
                FROM students
                WHERE first_name LIKE ?
                   OR middle_name LIKE ?
                   OR last_name LIKE ?
                   OR phone_number LIKE ?
                   OR public_id LIKE ?
                   OR school LIKE ?
                ORDER BY first_name
                LIMIT ?
            """, (like, like, like, like, like, like, limit))
        except Exception:
            pass

        try:
            questions = _rows("""
                SELECT id, question_text, subject_code, chapter,
                       pdf_code, difficulty
                FROM questions
                WHERE question_text LIKE ?
                   OR chapter LIKE ?
                   OR tags LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (like, like, like, limit))
            from subjects_config import get_subject
            for qst in questions:
                subj = get_subject(qst.get('subject_code'))
                qst['subject_name'] = subj['name'] if subj else qst.get('subject_code')
        except Exception:
            pass

        try:
            pdfs = _rows("""
                SELECT id, code, title, subject, curriculum, class, is_premium
                FROM pdfs
                WHERE title LIKE ?
                   OR code LIKE ?
                   OR subject LIKE ?
                   OR tags LIKE ?
                ORDER BY uploaded_at DESC
                LIMIT ?
            """, (like, like, like, like, limit))
        except Exception:
            pass

        try:
            groups = _rows("""
                SELECT id, name, platform, category, icon,
                       is_featured, is_active
                FROM groups
                WHERE name LIKE ?
                   OR description LIKE ?
                   OR category LIKE ?
                ORDER BY is_featured DESC, click_count DESC
                LIMIT ?
            """, (like, like, like, limit))
        except Exception:
            pass

    return render_template(
        'dashboard/admin/search.html',
        query=q,
        users=users,
        questions=questions,
        pdfs=pdfs,
        groups=groups,
    )


# ============================================================
# MAINTENANCE MODE — super admin only
# ============================================================
# State is a plain JSON file in <project>/instance/maintenance.json
# (see module docstring for why we don't use user_settings).

_DEFAULT_MAINTENANCE = {
    'enabled': False,
    'title': "We'll be back soon",
    'message': "We're performing scheduled maintenance. Please check back shortly.",
    'eta': '',
    'since': None,
}


def _read_maintenance_state():
    try:
        if not os.path.exists(_MAINTENANCE_FILE):
            return dict(_DEFAULT_MAINTENANCE)
        with open(_MAINTENANCE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        state = dict(_DEFAULT_MAINTENANCE)
        state.update({
            'enabled': bool(data.get('enabled', False)),
            'title': data.get('title', state['title']),
            'message': data.get('message', state['message']),
            'eta': data.get('eta', ''),
            'since': data.get('since'),
        })
        return state
    except Exception as e:
        logger.warning(f"_read_maintenance_state failed: {e}")
        return dict(_DEFAULT_MAINTENANCE)


def _write_maintenance_state(state):
    try:
        os.makedirs(_INSTANCE_DIR, exist_ok=True)
        payload = {
            'enabled': bool(state.get('enabled', False)),
            'title': state.get('title', ''),
            'message': state.get('message', ''),
            'eta': state.get('eta', ''),
            'since': state.get('since'),
        }
        tmp = _MAINTENANCE_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _MAINTENANCE_FILE)
        return True
    except Exception as e:
        logger.error(f"_write_maintenance_state failed: {e}", exc_info=True)
        return False


@admin_system_bp.route('/maintenance', methods=['GET'], endpoint='maintenance')
def maintenance():
    if not is_super_admin():
        abort(404)

    state = _read_maintenance_state()
    return render_template(
        'dashboard/admin/system/maintenance.html',
        maintenance=state,
    )


@admin_system_bp.route('/maintenance', methods=['POST'],
                       endpoint='maintenance_save')
def maintenance_save():
    if not is_super_admin():
        abort(404)

    if not validate_csrf():
        abort(403)

    enabled = request.form.get('enabled') == '1'
    title = (request.form.get('title') or '').strip()[:80]
    message = (request.form.get('message') or '').strip()[:400]
    eta = (request.form.get('eta') or '').strip()[:60]

    previous = _read_maintenance_state()

    from utils import get_somali_time_db
    since = previous.get('since')
    if enabled and not previous.get('enabled'):
        since = get_somali_time_db()
    if not enabled:
        since = None

    new_state = {
        'enabled': enabled,
        'title': title or "We'll be back soon",
        'message': message,
        'eta': eta,
        'since': since,
    }

    ok = _write_maintenance_state(new_state)
    if not ok:
        return jsonify({'error': 'Could not save maintenance state.'}), 500

    write_audit(
        action='platform.maintenance_toggle',
        target_type='platform',
        before={'enabled': previous.get('enabled')},
        after={'enabled': enabled},
        note=f"title={title}",
        severity='critical',
    )

    return jsonify({
        'success': True,
        'message': (
            'Maintenance mode enabled.' if enabled
            else 'Platform back online.'
        ),
    })


# ============================================================
# ANNOUNCEMENTS — send to all users
# ============================================================

@admin_system_bp.route('/announcement', methods=['GET'],
                       endpoint='announcement')
@admin_can('announcements.send')
def announcement():
    return render_template('dashboard/admin/system/announcement.html')


@admin_system_bp.route('/announcement', methods=['POST'],
                       endpoint='announcement_send')
@admin_can('announcements.send')
def announcement_send():
    if not validate_csrf():
        abort(403)

    title = (request.form.get('title') or '').strip()[:100]
    body = (request.form.get('body') or '').strip()[:500]
    link = (request.form.get('link') or '/dashboard').strip()[:200]
    icon = (request.form.get('icon') or '📢').strip()[:4]

    if not title or not body:
        flash('Title and message are required.', 'error')
        return redirect(url_for('admin_system.announcement'))

    sent = 0
    try:
        from services.notification_service import send_notification_to_all
        sent = send_notification_to_all(
            notification_type='admin_announcement',
            title=title,
            body=body,
            link=link,
            icon=icon or '📢',
            force=False,
        )
    except Exception as e:
        logger.error(f"announcement_send failed: {e}", exc_info=True)
        flash('Could not send the announcement.', 'error')
        return redirect(url_for('admin_system.announcement'))

    write_audit(
        action='announcement.send',
        target_type='platform',
        before=None,
        after={'title': title, 'recipients': sent},
        severity='info',
    )

    flash(f'Announcement sent to {sent} user(s).', 'success')
    return redirect(url_for('admin_system.dashboard'))


# ============================================================
# BROADCAST — compose page only
# ============================================================
# The actual POST handler lives in `admin_platform_bp.broadcast`.
# This GET just renders the compose form which posts to that endpoint.

@admin_system_bp.route('/broadcast', methods=['GET'],
                       endpoint='broadcast_compose')
@admin_can('platform.broadcast')
def broadcast_compose():
    return render_template('dashboard/admin/system/admin_broadcast.html')