# ============================================================
# blueprints/admin/ops_bp.py
# Operations domain — logs viewer, cache manager, sessions,
# system info, config viewer, and task runner.
#
# Routes:
#   GET  /admin/logs                              → logs viewer
#   GET  /admin/logs/read                         → AJAX tail + filter
#   GET  /admin/logs/download/<filename>          → download a log file
#   GET  /admin/logs/stats/<filename>             → line count + size
#   GET  /admin/cache                             → cache dashboard
#   POST /admin/cache/clear                       → clear cache
#   GET  /admin/sessions                          → sessions viewer
#   POST /admin/sessions/revoke-user/<user_id>    → force logout one user
#   POST /admin/sessions/revoke-all               → force logout everyone
#   GET  /admin/system-info                       → environment snapshot
#   GET  /admin/config                            → masked config viewer
#   GET  /admin/tasks                             → task runner history
#   POST /admin/tasks/trigger/<task_name>         → run a task now
#
# NOTE: Errors live in admin_errors_bp, activity in admin_activity_bp,
# platform monitor in admin_platform_bp, and backups in admin_backup_bp.
# This blueprint serves the surfaces that did not have a home.
# ============================================================

from flask import (
    Blueprint, render_template, request, session, flash,
    redirect, url_for, abort, jsonify, send_file,
)
import os
import sys
import time
import platform
import logging
from datetime import datetime
from pathlib import Path

from config import Config
from db import execute_with_retry
from utils import validate_csrf
from services.admin.guards import admin_can
from services.admin.audit import write_audit

logger = logging.getLogger(__name__)

admin_ops_bp = Blueprint('admin_ops', __name__, url_prefix='/admin')


# ============================================================
# HELPERS
# ============================================================

def _csrf_ok():
    token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    return bool(token) and token == session.get('csrf_token')


# Allowed log files — defence against path traversal.
_ALLOWED_LOG_FILES = {
    'app.log',
    'workers.log',
    'error.log',
    'backup_runner.log',
    'daily_tasks.log',
}


def _resolve_log_path(name):
    """Return an absolute path if the name is allowed, else None."""
    if name not in _ALLOWED_LOG_FILES:
        return None
    base = os.path.realpath(Config.LOG_DIR)
    candidate = os.path.realpath(os.path.join(base, name))
    # Ensure the resolved path is inside the log directory
    if not candidate.startswith(base + os.sep):
        return None
    return candidate


def _list_log_files():
    out = []
    for name in sorted(_ALLOWED_LOG_FILES):
        path = _resolve_log_path(name)
        if path and os.path.exists(path):
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            out.append({
                'name': name,
                'size_kb': round(size / 1024, 1),
                'exists': True,
            })
        else:
            out.append({
                'name': name,
                'size_kb': 0,
                'exists': False,
            })
    return out


def _human_duration(seconds):
    seconds = int(seconds)
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _mask_config_value(key, value):
    """
    Mask sensitive values. Returns (display, is_masked, is_boolean).
    """
    always_masked = {
        'SECRET_KEY',
        'ADMIN_ERROR_PASSWORD',
        'SMTP_PASSWORD',
        'TELEGRAM_BOT_TOKEN',
        'PDF_ADMIN_PASSWORD',
        'PDF_SUPER_ADMIN_PASSWORD',
        'BACKUP_TRIGGER_TOKEN',
    }
    if any(k in key.upper() for k in ('PASSWORD', 'TOKEN', 'SECRET')):
        return '••••••••', True, False

    if key in always_masked:
        return '••••••••', True, False

    if isinstance(value, bool):
        return ('True' if value else 'False'), False, True

    if value is None:
        return '—', False, False

    s = str(value)
    if len(s) == 0:
        return '—', False, False
    if len(s) > 200:
        s = s[:200] + '…'
    return s, False, False


# ============================================================
# LOGS — VIEWER (rewritten)
# ============================================================

@admin_ops_bp.route('/logs', methods=['GET'], endpoint='logs')
@admin_can('logs.view')
def logs():
    log_files = _list_log_files()
    return render_template(
        'dashboard/admin/ops/logs.html',
        log_files=log_files,
    )


@admin_ops_bp.route('/logs/read', methods=['GET'], endpoint='logs_read')
@admin_can('logs.view')
def logs_read():
    """
    Return the last N lines of a log file, optionally filtered by level
    and/or a search substring (case-insensitive).

    Query params:
        file    : filename (must be in _ALLOWED_LOG_FILES)
        level   : ERROR | WARNING | INFO | DEBUG | CRITICAL (optional)
        search  : substring (case-insensitive) (optional)
        lines   : how many matching lines to return (default 300, max 2000)
    """
    name = (request.args.get('file') or '').strip()
    level = (request.args.get('level') or '').strip().upper()
    search = (request.args.get('search') or '').strip()
    try:
        tail = int(request.args.get('lines', 300))
    except ValueError:
        tail = 300
    tail = max(10, min(tail, 2000))

    path = _resolve_log_path(name)
    if not path or not os.path.exists(path):
        return jsonify({'error': 'Log file not found.'}), 404

    lines = []
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            # Read at most ~512KB from the end
            chunk = min(size, 512 * 1024)
            f.seek(size - chunk)
            data = f.read()
            lines = data.splitlines()
    except Exception as e:
        logger.error(f"logs_read failed for {name}: {e}")
        return jsonify({'error': 'Could not read log file.'}), 500

    if level:
        lines = [l for l in lines if f' {level} ' in l or f'[{level}]' in l]

    if search:
        s = search.lower()
        lines = [l for l in lines if s in l.lower()]

    total_matched = len(lines)
    shown = lines[-tail:]

    return jsonify({
        'lines': shown,
        'total_matched': total_matched,
        'shown': len(shown),
        'file': name,
    })


@admin_ops_bp.route('/logs/download/<path:filename>', methods=['GET'],
                    endpoint='logs_download')
@admin_can('logs.view')
def logs_download(filename):
    """Download a whole log file (path-safe)."""
    path = _resolve_log_path(filename)
    if not path or not os.path.exists(path):
        abort(404)
    return send_file(
        path,
        as_attachment=True,
        download_name=filename,
        mimetype='text/plain',
    )


@admin_ops_bp.route('/logs/stats/<path:filename>', methods=['GET'],
                    endpoint='logs_stats')
@admin_can('logs.view')
def logs_stats(filename):
    """Line count + size for the sidebar. Cheap: counts newlines only."""
    path = _resolve_log_path(filename)
    if not path or not os.path.exists(path):
        return jsonify({'error': 'not found'}), 404

    try:
        size = os.path.getsize(path)
        line_count = 0
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for _ in f:
                line_count += 1
        return jsonify({
            'file': filename,
            'size_bytes': size,
            'line_count': line_count,
        })
    except Exception as e:
        logger.warning(f"logs_stats failed for {filename}: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================
# CACHE — DASHBOARD
# ============================================================

def _collect_cache_stats():
    """Return safe cache stats even if the cache manager is unavailable."""
    out = {
        'size': 0,
        'max_size': 0,
        'hit_ratio_pct': 0,
        'hits': 0,
        'misses': 0,
        'used_pct': 0,
        'namespaces': [],
    }
    try:
        from cache import get_cache_manager
        mgr = get_cache_manager()
        metrics = mgr.get_metrics() or {}
        local = metrics.get('local', {}) or {}
        glob = metrics.get('global', {}) or {}

        out['size'] = int(local.get('size', 0) or 0)
        out['max_size'] = int(local.get('max_size', 0) or 0)
        out['used_pct'] = round(float(local.get('used_percent', 0) or 0), 1)
        out['hits'] = int(glob.get('hits', 0) or 0)
        out['misses'] = int(glob.get('misses', 0) or 0)
        ratio = float(glob.get('hit_ratio', 0) or 0)
        out['hit_ratio_pct'] = round(ratio * 100, 1)

        # Namespaces: introspect the underlying local cache dict.
        try:
            local_backend = getattr(mgr, '_local', None)
            cache_dict = getattr(local_backend, '_cache', None)
            if cache_dict is not None:
                counts = {}
                for k in list(cache_dict.keys()):
                    ns = str(k).split(':', 1)[0]
                    counts[ns] = counts.get(ns, 0) + 1
                out['namespaces'] = [
                    {'name': k, 'count': v}
                    for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
                ][:20]
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"_collect_cache_stats failed: {e}")
    return out


@admin_ops_bp.route('/cache', methods=['GET'], endpoint='cache')
@admin_can('cache.view')
def cache():
    stats = _collect_cache_stats()
    return render_template(
        'dashboard/admin/ops/cache.html',
        cache=stats,
        namespaces=stats['namespaces'],
    )


@admin_ops_bp.route('/cache/clear', methods=['POST'], endpoint='cache_clear')
@admin_can('cache.clear')
def cache_clear():
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403

    try:
        from cache import get_cache_manager
        mgr = get_cache_manager()
        local_backend = getattr(mgr, '_local', None)
        if local_backend is not None and hasattr(local_backend, '_cache'):
            with local_backend._lock:
                local_backend._cache.clear()
        try:
            from cache import cache_metrics
            cache_metrics.reset()
        except Exception:
            pass

        write_audit(
            action='cache.clear',
            target_type='cache',
            before=None,
            after={'cleared': True},
            severity='warning',
        )
        return jsonify({'success': True, 'message': 'Cache cleared.'})
    except Exception as e:
        logger.error(f"cache_clear failed: {e}", exc_info=True)
        return jsonify({'error': 'Could not clear cache.'}), 500


# ============================================================
# SESSIONS — VIEWER
# ============================================================

def _collect_session_stats():
    """
    Flask filesystem sessions live under a directory.
    We approximate by counting files and cross-referencing recent logins.
    """
    out = {
        'count': 0,
        'disk_usage_mb': 0,
        'sessions': [],
    }

    session_dir = None
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'flask_session'),
        os.path.join(os.getcwd(), 'flask_session'),
        '/tmp/flask_session',
    ]
    for c in candidates:
        p = os.path.realpath(c)
        if os.path.isdir(p):
            session_dir = p
            break

    if session_dir:
        total = 0
        for root, _, files in os.walk(session_dir):
            for fn in files:
                try:
                    total += os.path.getsize(os.path.join(root, fn))
                except OSError:
                    pass
        out['disk_usage_mb'] = round(total / (1024 * 1024), 2)

    try:
        cursor = execute_with_retry("""
            SELECT id, public_id, first_name, last_name, last_login_at
            FROM students
            WHERE last_login_at IS NOT NULL
              AND last_login_at >= datetime('now', '-1 day')
            ORDER BY last_login_at DESC
            LIMIT 200
        """)
        rows = [dict(r) for r in cursor.fetchall()]
        out['sessions'] = rows
        out['count'] = len(rows)
    except Exception as e:
        logger.warning(f"session query failed: {e}")

    return out


@admin_ops_bp.route('/sessions', methods=['GET'], endpoint='sessions')
@admin_can('sessions.view')
def sessions():
    stats = _collect_session_stats()
    return render_template(
        'dashboard/admin/ops/sessions.html',
        count=stats['count'],
        disk_usage_mb=stats['disk_usage_mb'],
        sessions=stats['sessions'],
    )


@admin_ops_bp.route('/sessions/revoke-user/<int:user_id>',
                    methods=['POST'], endpoint='sessions_revoke_user')
@admin_can('sessions.manage')
def sessions_revoke_user(user_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403

    try:
        execute_with_retry("""
            UPDATE students
            SET session_version = COALESCE(session_version, 0) + 1
            WHERE id = ?
        """, (user_id,), commit=True)

        write_audit(
            action='session.revoke_user',
            target_type='user',
            target_id=user_id,
            before=None,
            after={'revoked': True},
            severity='warning',
        )
        return jsonify({'success': True, 'message': 'User will be logged out.'})
    except Exception as e:
        logger.error(f"sessions_revoke_user failed: {e}")
        return jsonify({'error': 'Could not revoke session.'}), 500


@admin_ops_bp.route('/sessions/revoke-all', methods=['POST'],
                    endpoint='sessions_revoke_all')
@admin_can('sessions.manage')
def sessions_revoke_all():
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403

    try:
        execute_with_retry("""
            UPDATE students
            SET session_version = COALESCE(session_version, 0) + 1
        """, commit=True)

        write_audit(
            action='session.revoke_all',
            target_type='platform',
            before=None,
            after={'revoked': True},
            severity='critical',
        )
        return jsonify({'success': True, 'message': 'All sessions revoked.'})
    except Exception as e:
        logger.error(f"sessions_revoke_all failed: {e}")
        return jsonify({'error': 'Could not revoke sessions.'}), 500


# ============================================================
# SYSTEM INFO
# ============================================================

@admin_ops_bp.route('/system-info', methods=['GET'], endpoint='system_info')
@admin_can('system.info')
def system_info():
    info = {
        'python_version': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        'platform': f"{platform.system()} {platform.release()}",
        'flask_version': '—',
        'debug': bool(Config.DEBUG),
        'uptime': '—',
        'cpu_count': os.cpu_count() or 0,
        'memory_used_mb': 0,
        'disk_free_gb': 0,
        'disk_used_pct': 0,
        'db_size_mb': 0,
        'wal_size_mb': 0,
        'session_count': 0,
        'cache_size': 0,
        'cache_hit_pct': 0,
    }

    try:
        import flask
        info['flask_version'] = getattr(flask, '__version__', '—')
    except Exception:
        pass

    try:
        from app import app as _app
        started = getattr(_app, '_started_at', None)
        if started:
            info['uptime'] = _human_duration(time.time() - started)
    except Exception:
        pass

    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        info['memory_used_mb'] = round(usage.ru_maxrss / 1024, 1)
    except Exception:
        pass

    try:
        stat = os.statvfs(Config.BACKUP_DIR)
        free_b = stat.f_bavail * stat.f_frsize
        total_b = stat.f_blocks * stat.f_frsize
        info['disk_free_gb'] = round(free_b / (1024 ** 3), 2)
        info['disk_used_pct'] = round(100.0 * (1 - free_b / total_b), 1) if total_b else 0
    except Exception:
        pass

    try:
        db_path = Config.DATABASE_PATH
        if os.path.exists(db_path):
            info['db_size_mb'] = round(os.path.getsize(db_path) / (1024 * 1024), 2)
        wal_path = db_path + '-wal'
        if os.path.exists(wal_path):
            info['wal_size_mb'] = round(os.path.getsize(wal_path) / (1024 * 1024), 2)
    except Exception:
        pass

    try:
        stats = _collect_session_stats()
        info['session_count'] = stats['count']
    except Exception:
        pass

    try:
        c = _collect_cache_stats()
        info['cache_size'] = c['size']
        info['cache_hit_pct'] = c['hit_ratio_pct']
    except Exception:
        pass

    return render_template(
        'dashboard/admin/ops/system_info.html',
        info=info,
    )


# ============================================================
# CONFIG VIEWER
# ============================================================

@admin_ops_bp.route('/config', methods=['GET'], endpoint='config')
@admin_can('system.config')
def config():
    config_items = []
    for key in sorted(dir(Config)):
        if key.startswith('_'):
            continue
        try:
            raw = getattr(Config, key)
        except Exception:
            continue
        if callable(raw):
            continue
        display, is_masked, is_boolean = _mask_config_value(key, raw)
        config_items.append({
            'key': key,
            'value': raw,
            'display': display,
            'masked': is_masked,
            'type': 'boolean' if is_boolean else 'other',
        })

    return render_template(
        'dashboard/admin/ops/config.html',
        config_items=config_items,
    )


# ============================================================
# TASKS — VIEWER
# ============================================================

@admin_ops_bp.route('/tasks', methods=['GET'], endpoint='tasks')
@admin_can('tasks.view')
def tasks():
    runs = []
    try:
        cursor = execute_with_retry("""
            SELECT task_name, category, status, items_affected,
                   duration_ms, error_message, started_at
            FROM daily_task_runs
            ORDER BY started_at DESC
            LIMIT 50
        """)
        runs = [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.warning(f"tasks query failed: {e}")

    return render_template(
        'dashboard/admin/ops/scheduled_tasks.html',
        runs=runs,
    )


@admin_ops_bp.route('/tasks/trigger/<task_name>', methods=['POST'],
                    endpoint='tasks_trigger')
@admin_can('tasks.trigger')
def tasks_trigger(task_name):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403

    if not task_name or not all(c.isalnum() or c in '._-' for c in task_name):
        return jsonify({'error': 'Invalid task name.'}), 400

    try:
        from daily_tasks import _TASKS, TaskContext, run_one_task
        from utils import get_somali_time, get_somali_time_db

        target = next((t for t in _TASKS if t['name'] == task_name), None)
        if not target:
            return jsonify({'error': f'Task "{task_name}" not found.'}), 404

        ctx = TaskContext(dry_run=False)
        run_date = datetime.now().strftime('%Y-%m-%d')

        start = time.time()
        result = run_one_task(target, ctx, run_date)
        duration_ms = int((time.time() - start) * 1000)

        write_audit(
            action='task.trigger',
            target_type='task',
            before=None,
            after={
                'task_name': task_name,
                'status': result.get('status'),
                'items': result.get('items', 0),
                'duration_ms': duration_ms,
            },
            severity='warning',
        )

        return jsonify({
            'success': result.get('status') == 'success',
            'status': result.get('status'),
            'items': result.get('items', 0),
            'duration_ms': duration_ms,
            'error': result.get('error'),
        })
    except Exception as e:
        logger.error(f"tasks_trigger failed for {task_name}: {e}", exc_info=True)
        return jsonify({'error': f'Task failed: {e}'}), 500