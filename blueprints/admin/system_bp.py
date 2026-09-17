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
# NOTE: Revenue figures (today / month) are SUPER-ADMIN ONLY. They are
# included in the pulse ONLY when is_super_admin() is true. Regular
# admins receive pulse without revenue keys and the template hides
# those tiles anyway.
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
    - Super admin: full control centre (revenue, alerts, charts).
    - Regular admin: workbench (own actions, no revenue anywhere).
    """
    if 'user_id' not in session:
        return redirect(url_for('auth.login', next=request.url))

    from services.admin.roles import is_any_admin
    if not is_any_admin():
        abort(404)

    is_super = is_super_admin()

    # ---- Pulse (revenue included only for super admin) ----
    pulse = _build_pulse(include_revenue=is_super)

    # ---- Deltas (week-over-week percentages) ----
    deltas = _build_deltas(pulse)

    # ---- Priority queue ----
    priority = _build_priority(pulse)

    # ---- System block ----
    system = _build_system_block()

    # ---- Sparklines ----
    sparklines = _build_sparklines()

    # ---- Chart data ----
    chart_data = _build_chart_data()

    # ---- Recent admin audit ----
    recent_actions = _build_recent_actions() if is_super else []

    # ---- Recent user activity feed ----
    user_activity = _build_user_activity()

    # ---- Greeting context ----
    from utils import get_somali_time
    hour = get_somali_time().hour
    if hour < 12:
        greeting_emoji = '🌅'
        greeting_somali = 'Good morning'
    elif hour < 17:
        greeting_emoji = '☀️'
        greeting_somali = 'Good afternoon'
    else:
        greeting_emoji = '🌙'
        greeting_somali = 'Good evening'

    greeting_title = greeting_somali + ', ' + (session.get('user_name') or 'Admin')

    # ---- Command centre section persistence ----
    open_sections = request.args.getlist('open') or ['content']

    return render_template(
        'dashboard/admin/overview.html',
        is_super_admin_view=is_super,
        pulse=pulse,
        deltas=deltas,
        priority=priority,
        system=system,
        sparklines=sparklines,
        chart_data=chart_data,
        recent_actions=recent_actions,
        user_activity=user_activity,
        greeting_emoji=greeting_emoji,
        greeting_somali=greeting_somali,
        greeting_title=greeting_title,
        open_sections=open_sections,
        alerts=[],           # legacy variable, no longer rendered
        pending={'total': 0, 'items': []},  # legacy variable
        recent_own_actions=[],  # legacy variable
    )


# ============================================================
# GLOBAL SEARCH
# ============================================================

@admin_system_bp.route('/search', methods=['GET'], endpoint='search')
@admin_can('search.use')
def search():
    """
    Global admin search across users, questions, PDFs, and groups.
    Every block runs its own query; failures are non-fatal so a
    partially-broken domain never blocks the whole search.
    """
    q = (request.args.get('q') or '').strip()
    users = []
    questions = []
    pdfs = []
    groups = []

    if q:
        like = f"%{q}%"
        limit = 10

        # ---------- Users ----------
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
        except Exception as e:
            logger.debug(f"search: users failed: {e}")

        # ---------- Questions ----------
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
        except Exception as e:
            logger.debug(f"search: questions failed: {e}")

        # ---------- PDFs ----------
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
        except Exception as e:
            logger.debug(f"search: pdfs failed: {e}")

        # ---------- Groups ----------
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
        except Exception as e:
            logger.debug(f"search: groups failed: {e}")

    return render_template(
        'dashboard/admin/search.html',
        query=q,
        users=users,
        questions=questions,
        pdfs=pdfs,
        groups=groups,
    )


# ============================================================
# PULSE / ALERTS / SYSTEM / ACTIVITY HELPERS
# ============================================================
def _build_deltas(pulse):
    """Week-over-week percentage change for KPI tiles."""
    deltas = {'users': 0, 'quizzes': 0, 'revenue': 0}

    try:
        # Users: this week vs previous week
        this_week = _scalar(
            "SELECT COUNT(*) FROM students "
            "WHERE created_at >= datetime('now', '-7 days')"
        )
        prev_week = _scalar(
            "SELECT COUNT(*) FROM students "
            "WHERE created_at >= datetime('now', '-14 days') "
            "AND created_at < datetime('now', '-7 days')"
        )
        if prev_week > 0:
            deltas['users'] = round(100.0 * (this_week - prev_week) / prev_week, 1)
        elif this_week > 0:
            deltas['users'] = 100.0
    except Exception:
        pass

    try:
        # Quizzes: same pattern
        this_week = _scalar(
            "SELECT COUNT(*) FROM quiz_attempts "
            "WHERE completed_at >= datetime('now', '-7 days')"
        )
        prev_week = _scalar(
            "SELECT COUNT(*) FROM quiz_attempts "
            "WHERE completed_at >= datetime('now', '-14 days') "
            "AND completed_at < datetime('now', '-7 days')"
        )
        if prev_week > 0:
            deltas['quizzes'] = round(100.0 * (this_week - prev_week) / prev_week, 1)
        elif this_week > 0:
            deltas['quizzes'] = 100.0
    except Exception:
        pass

    try:
        # Revenue: this month vs previous month
        this_month = _scalar(
            "SELECT COALESCE(SUM(final_price_cents), 0) FROM upgrade_requests "
            "WHERE status = 'approved' "
            "AND approved_at >= datetime('now', '-30 days')"
        )
        prev_month = _scalar(
            "SELECT COALESCE(SUM(final_price_cents), 0) FROM upgrade_requests "
            "WHERE status = 'approved' "
            "AND approved_at >= datetime('now', '-60 days') "
            "AND approved_at < datetime('now', '-30 days')"
        )
        if prev_month > 0:
            deltas['revenue'] = round(100.0 * (this_month - prev_month) / prev_month, 1)
        elif this_month > 0:
            deltas['revenue'] = 100.0
    except Exception:
        pass

    return deltas


def _build_priority(pulse):
    """
    Priority queue — actions that need the current admin's attention.
    Empty when there is no work; the greeting bar adapts accordingly.
    """
    items = []

    # ---- Pending upgrades (super admin only) ----
    if is_super_admin() and pulse.get('upgrades_pending', 0) > 0:
        items.append({
            'icon': '🚀',
            'count': pulse['upgrades_pending'],
            'label': 'Upgrade requests',
            'description': 'Users waiting for tier approval.',
            'cta': 'Review',
            'link': url_for('upgrade.admin_list', status='pending'),
            'tone': 'green',
        })

    # ---- PDF intake ----
    try:
        from services.admin.capabilities import admin_can as _admin_can
        if _admin_can('pdfs.intake'):
            from bot.db import count_pending_pdfs
            n = count_pending_pdfs()
            if n > 0:
                items.append({
                    'icon': '📥',
                    'count': n,
                    'label': 'PDFs to process',
                    'description': 'Telegram uploads awaiting intake.',
                    'cta': 'Process',
                    'link': url_for('admin_content.pdfs', tab='intake'),
                    'tone': 'blue',
                })
    except Exception:
        pass

    # ---- Unverified PDFs (review queue) ----
    try:
        from services.admin.capabilities import admin_can as _admin_can
        if _admin_can('pdfs.intake'):
            n = _scalar(
                "SELECT COUNT(*) FROM unverified_pdfs WHERE confirmed = 0"
            )
            if n > 0:
                items.append({
                    'icon': '🔍',
                    'count': n,
                    'label': 'PDFs to verify',
                    'description': 'Auto-published metadata awaiting review.',
                    'cta': 'Verify',
                    'link': url_for('admin_content.pdfs', tab='unverified'),
                    'tone': 'purple',
                })
    except Exception:
        pass

    # ---- Staged PDFs to publish (super admin) ----
    try:
        if is_super_admin():
            from bot.db import count_bot_pdfs
            n = count_bot_pdfs(published_filter=False)
            if n > 0:
                items.append({
                    'icon': '☁️',
                    'count': n,
                    'label': 'PDFs to publish',
                    'description': 'Staged uploads ready for release.',
                    'cta': 'Publish',
                    'link': url_for('admin_content.pdfs', tab='staging'),
                    'tone': 'amber',
                })
    except Exception:
        pass

    # ---- Pending reports ----
    try:
        if admin_can('reports.view'):
            n = _scalar(
                "SELECT COUNT(*) FROM question_interactions "
                "WHERE interaction_type = 'report' AND report_status = 'pending'"
            )
            if n > 0:
                items.append({
                    'icon': '🚩',
                    'count': n,
                    'label': 'Reported questions',
                    'description': 'Flagged by users, awaiting resolution.',
                    'cta': 'Resolve',
                    'link': url_for('admin_community.reports'),
                    'tone': 'red',
                })
    except Exception:
        pass

    # ---- Errors (if above threshold) ----
    if pulse.get('errors_open', 0) >= 3:
        items.append({
            'icon': '⚠️',
            'count': pulse['errors_open'],
            'label': 'Unresolved errors',
            'description': 'Application errors awaiting triage.',
            'cta': 'Triage',
            'link': url_for('admin_errors.index', resolved='0'),
            'tone': 'red',
        })

    total = sum(i['count'] for i in items)

    if not items:
        hint = ''
    elif total <= 3:
        hint = 'quick work — should take a few minutes'
    elif total <= 20:
        hint = 'a moderate queue, worth clearing today'
    else:
        hint = 'a substantial backlog — consider prioritising'

    return {'total': total, 'items': items, 'hint': hint}


def _build_sparklines():
    """
    Return 10-point sparkline arrays for each KPI tile.
    Aggregates 30 days of data into 10 buckets of 3 days each.
    """
    def bucket_series(series):
        if not series:
            return [0] * 10
        values = [s['value'] for s in series]
        if len(values) <= 10:
            # Pad front with zeros so the shape is consistent
            return [0] * (10 - len(values)) + values
        # Downsample to 10 buckets
        bucket_size = len(values) / 10
        out = []
        for i in range(10):
            start = int(i * bucket_size)
            end = int((i + 1) * bucket_size)
            chunk = values[start:end] or [0]
            out.append(sum(chunk))
        return out

    sparklines = {
        'signups': [], 'quizzes': [], 'revenue': [],
        'upgrades': [], 'errors': [], 'backups': [],
    }

    try:
        from platform_activity import (
            get_signups_series, get_quizzes_series,
        )
        sparklines['signups'] = bucket_series(get_signups_series(days=30))
        sparklines['quizzes'] = bucket_series(get_quizzes_series(days=30))
    except Exception:
        sparklines['signups'] = [0] * 10
        sparklines['quizzes'] = [0] * 10

    # Simple flat lines for the rest — real series would need more
    # granular queries; a flat line is honest and cheap.
    sparklines['revenue']  = [0] * 10
    sparklines['upgrades'] = [0] * 10
    sparklines['errors']   = [0] * 10
    sparklines['backups']  = [0] * 10

    return sparklines


def _build_chart_data():
    """Assemble chart data for Chart.js."""
    labels = []
    signups = []
    quizzes = []

    try:
        from platform_activity import (
            get_signups_series, get_quizzes_series,
        )
        signups_series = get_signups_series(days=30)
        quizzes_series = get_quizzes_series(days=30)

        # Build a union of all dates across both series
        all_dates = sorted(set(
            [s['date'] for s in signups_series] +
            [s['date'] for s in quizzes_series]
        ))

        signups_map = {s['date']: s['value'] for s in signups_series}
        quizzes_map = {s['date']: s['value'] for s in quizzes_series}

        for d in all_dates:
            # Label: short month-day
            try:
                from datetime import datetime
                dt = datetime.strptime(d, '%Y-%m-%d')
                labels.append(dt.strftime('%b %d'))
            except Exception:
                labels.append(d)
            signups.append(signups_map.get(d, 0))
            quizzes.append(quizzes_map.get(d, 0))
    except Exception as e:
        logger.debug(f"_build_chart_data series failed: {e}")

    tiers = {'free': 0, 'premium': 0, 'pro': 0}
    try:
        from platform_activity import get_tier_distribution
        tiers = get_tier_distribution()
    except Exception:
        pass

    return {
        'series': {'labels': labels, 'signups': signups, 'quizzes': quizzes},
        'tiers': tiers,
    }


def _build_user_activity():
    """Recent platform activity (signups, quizzes, upgrades, PDFs)."""
    try:
        from platform_activity import get_activity_feed
        return get_activity_feed(limit=8, source='all')
    except Exception as e:
        logger.debug(f"_build_user_activity failed: {e}")
        return []

def _build_pulse(include_revenue=False):
    """
    Build the pulse dict.

    Revenue keys (revenue_today, revenue_month) are ONLY added when
    `include_revenue=True`. Callers must pass True only for super admin.
    """
    stats = {}
    try:
        from platform_activity import get_platform_stats
        stats = get_platform_stats()
    except Exception:
        pass

    pulse = {
        'users_total':      stats.get('users_total', 0),
        'users_delta':      0,
        'users_new_today':  stats.get('users_today', 0),
        'quizzes_today':    stats.get('quizzes_today', 0),
        'quizzes_delta':    0,
        'quizzes_week':     stats.get('quizzes_week', 0),
        'upgrades_pending': stats.get('upgrades_pending', 0),
        'errors_open':      stats.get('errors_open', 0),
        'errors_today':     stats.get('errors_today', 0),
    }

    if include_revenue:
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

        pulse['revenue_today'] = round(revenue_today_cents / 100, 2)
        pulse['revenue_month'] = round(revenue_month_cents / 100, 2)

    backup = _backup_health()
    disk = _disk_stats()

    pulse.update({
        'backup_age_days':  backup['age_days'],
        'backup_count':     backup['count'],
        'disk_free_mb':     disk['free_mb'],
        'disk_free_gb':     disk['free_gb'],
        'disk_used_pct':    disk['used_pct'],
    })

    # Upgrades approved this month (used by the KPI tile)
    pulse['upgrades_approved_month'] = stats.get('upgrades_approved_month', 0)
    return pulse


def _build_alerts(pulse):
    """
    Super-admin alert strip at the top of the command centre.
    Ordered by operational urgency: approvals → publishing → errors
    → backup → disk.
    """
    alerts = []

    # ─── Pending upgrade requests ───
    if pulse.get('upgrades_pending', 0) > 0:
        alerts.append({
            'icon': '🚀',
            'title': f"{pulse['upgrades_pending']} pending upgrade request(s)",
            'subtitle': 'Users are waiting for approval.',
            'link': url_for('upgrade.admin_list', status='pending'),
        })

    # ─── Staged PDFs ready to publish (super admin only) ───
    if is_super_admin():
        try:
            from bot.db import count_bot_pdfs
            staged = count_bot_pdfs()
            if staged > 0:
                alerts.append({
                    'icon': '☁️',
                    'title': (
                        f"{staged} staged PDF{'s' if staged != 1 else ''} "
                        f"ready to publish"
                    ),
                    'subtitle': (
                        'Fulfilled Telegram uploads awaiting release '
                        'to the platform.'
                    ),
                    'link': url_for('admin_content.pdfs', tab='staging'),
                })
        except Exception as e:
            logger.debug(f"staged pdf count failed: {e}")

    # ─── Unresolved errors ───
    if pulse.get('errors_open', 0) > 5:
        alerts.append({
            'icon': '⚠️',
            'title': f"{pulse['errors_open']} unresolved errors",
            'subtitle': 'Consider triaging the error log.',
            'link': url_for('admin_errors.index', resolved='0'),
        })

    # ─── Backup freshness ───
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

    # ─── Disk pressure ───
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
    """Recent admin actions across the platform. Super admin only."""
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


def _build_recent_own_actions():
    """
    Recent audit entries authored by the current admin.
    Powers the 'Your recent actions' panel on the regular-admin
    workbench view of the overview page.
    """
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
            'description': r.get('action', '') or '',
            'time': (r.get('created_at') or '')[:16],
        })
    return out


def _build_pending_work():
    """
    Regular-admin workbench: queued items that need attention.
    No revenue. No staging publish — that's super-admin only.
    Ordered by the admin's most common workflow:
        reports → PDF intake → (silent when empty)
    """
    items = []

    # ─── Reported questions ───
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
    except Exception as e:
        logger.debug(f"report count failed: {e}")

    # ─── PDFs waiting for intake ───
    try:
        from services.admin.capabilities import admin_can as _admin_can
        if _admin_can('pdfs.intake'):
            from bot.db import count_pending_pdfs
            n = count_pending_pdfs()
            if n > 0:
                items.append({
                    'icon': '📥',
                    'label': 'PDFs waiting for intake',
                    'description': (
                        'Telegram uploads not yet processed into staging.'
                    ),
                    'count': n,
                    'link': url_for('admin_content.pdfs', tab='intake'),
                })
    except Exception as e:
        logger.debug(f"pending pdf count failed: {e}")

    # ─── Staged PDFs awaiting platform publish (super admin only) ───
    try:
        from services.admin.capabilities import admin_can as _admin_can
        if _admin_can('pdfs.publish'):
            from bot.db import count_bot_pdfs
            n = count_bot_pdfs()
            if n > 0:
                items.append({
                    'icon': '☁️',
                    'label': 'Staged PDFs ready to publish',
                    'description': (
                        'Fulfilled uploads awaiting a super admin to release.'
                    ),
                    'count': n,
                    'link': url_for('admin_content.pdfs', tab='staging'),
                })
    except Exception as e:
        logger.debug(f"staged pdf count failed: {e}")

    total = sum(i['count'] for i in items)
    return {'total': total, 'items': items}


# ============================================================
# MAINTENANCE MODE
# ============================================================

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
# ANNOUNCEMENTS
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
    also_push = request.form.get('also_push') == '1'

    if not title or not body:
        flash('Title and message are required.', 'error')
        return redirect(url_for('admin_system.announcement'))

    try:
        from services.notification_service import broadcast_announcement
        result = broadcast_announcement(
            title=title,
            body=body,
            link=link,
            icon=icon or '📢',
            also_push=also_push,
        )
    except Exception as e:
        logger.error(f"announcement_send failed: {e}", exc_info=True)
        flash('Could not send the announcement.', 'error')
        return redirect(url_for('admin_system.announcement'))

    inapp = result.get('in_app', 0)
    push = result.get('push', {})

    write_audit(
        action='announcement.send',
        target_type='platform',
        before=None,
        after={
            'title': title,
            'recipients': inapp,
            'push_sent': push.get('sent', 0),
            'push_recipients': push.get('recipients', 0),
            'also_push': also_push,
        },
        severity='info',
    )

    if not also_push:
        flash(f'Announcement sent to {inapp} user(s) (in-app only).', 'success')
    elif push.get('skipped') == 'push_disabled':
        flash(
            f'Announcement sent to {inapp} user(s). '
            f'Push is not configured on the server.',
            'warning',
        )
    elif push.get('skipped') == 'push_unavailable':
        flash(
            f'Announcement sent to {inapp} user(s). '
            f'Push service is unavailable.',
            'warning',
        )
    elif push.get('skipped') == 'no_recipients':
        flash(
            f'Announcement sent to {inapp} user(s). '
            f'No users have push enabled for announcements.',
            'success',
        )
    elif push.get('skipped') == 'delivery_error':
        flash(
            f'Announcement sent to {inapp} user(s). '
            f'Push delivery failed — check logs.',
            'warning',
        )
    else:
        msg = (
            f'Announcement sent to {inapp} user(s). '
            f'Push delivered to {push.get("recipients", 0)} device(s).'
        )
        if push.get('capped'):
            msg += ' (capped at 500 recipients)'
        if push.get('pruned'):
            msg += f' Pruned {push["pruned"]} dead subscription(s).'
        flash(msg, 'success')

    return redirect(url_for('admin_system.dashboard'))


# ============================================================
# BROADCAST
# ============================================================

@admin_system_bp.route('/broadcast', methods=['GET'],
                       endpoint='broadcast_compose')
@admin_can('platform.broadcast')
def broadcast_compose():
    return render_template('dashboard/admin/system/admin_broadcast.html')