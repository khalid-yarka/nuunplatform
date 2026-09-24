# blueprints/admin/backup_bp.py
# ============================================================
# Backup management endpoints.
#
# Serves BOTH URL shapes:
#   /admin/backups/...   (canonical — matches static/js/admin/ops.js)
#   /admin/backup/       (legacy singular → redirects to plural)
#
# Blueprint has NO url_prefix — every route declares its own full path.
#
# SECURITY: every route here is guarded by @admin_can(...) which
# routes through the capability system. The previous version used a
# local @admin_required decorator that only checked is_admin() — so
# any admin, even one with zero backup capabilities granted, could
# download the entire database, create backups, and restore. That
# bypass is closed.
# ============================================================

import os
import time

from flask import (
    Blueprint, render_template, request, session, jsonify,
    abort, send_file, redirect, url_for,
)

from db import is_admin, execute_with_retry
from backup import (
    BackupManager,
    is_backup_locked,
    acquire_backup_lock,
    release_backup_lock,
)
from activity_logger import log_backup_event, log_admin_action
from services.admin.guards import admin_can
from services.admin.audit import write_audit
from config import Config


admin_backup_bp = Blueprint('admin_backup', __name__)


# ------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------

def get_manager():
    return BackupManager()


def _get_backup_config():
    cursor = execute_with_retry("SELECT * FROM backup_config WHERE id = 1")
    row = cursor.fetchone()
    if row:
        return dict(row)
    execute_with_retry("INSERT OR IGNORE INTO backup_config (id) VALUES (1)")
    row = execute_with_retry(
        "SELECT * FROM backup_config WHERE id = 1"
    ).fetchone()
    return dict(row) if row else {}


def _update_backup_config(data):
    execute_with_retry("""
        UPDATE backup_config SET
            daily_retention = ?,
            weekly_retention = ?,
            monthly_retention = ?,
            scheduled_enabled = ?,
            scheduled_type = ?,
            scheduled_time = ?,
            last_modified = datetime('now', 'localtime')
        WHERE id = 1
    """, (
        data['daily_retention'],
        data['weekly_retention'],
        data['monthly_retention'],
        data['scheduled_enabled'],
        data['scheduled_type'],
        data['scheduled_time'],
    ), commit=True)


def _enrich_backups(manager, backups):
    for b in backups:
        file_path = os.path.join(manager.backup_dir, b['filename'])
        b['exists'] = os.path.exists(file_path)
        b['size_kb'] = b.get('size_bytes', 0) / 1024
    return backups


# ============================================================
# DASHBOARD
# ============================================================

@admin_backup_bp.route('/admin/backups/')
@admin_backup_bp.route('/admin/backups')
@admin_can('backups.view')
def dashboard():
    manager = get_manager()
    health = manager.get_backup_health_summary()
    config = _get_backup_config()
    backups = _enrich_backups(
        manager,
        manager.get_backup_list(valid_only=False),
    )
    return render_template(
        'dashboard/admin/ops/backups.html',
        health=health,
        config=config,
        backups=backups,
    )


# Legacy singular alias — bookmarks typed by hand
@admin_backup_bp.route('/admin/backup/')
@admin_backup_bp.route('/admin/backup')
def dashboard_singular():
    return redirect(url_for('admin_backup.dashboard'))


# ============================================================
# CREATE
# ============================================================

@admin_backup_bp.route('/admin/backups/create', methods=['POST'])
@admin_can('backups.create')
def create_backup():
    # CSRF check for form POSTs (JSON requests skip it since the
    # capability system already authenticates the caller and the
    # modern JS clients send X-CSRF-Token anyway).
    if not request.is_json:
        token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
        if not token or token != session.get('csrf_token'):
            return jsonify({'success': False, 'message': 'Invalid session'}), 403

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        backup_type = payload.get('type', 'manual')
    else:
        backup_type = request.form.get('type', 'manual')

    if backup_type not in ('daily', 'weekly', 'monthly', 'manual'):
        return jsonify({'success': False, 'message': 'Invalid backup type'}), 400

    if is_backup_locked():
        return jsonify({'success': False, 'message': 'Another backup is already running'}), 409

    manager = get_manager()
    lock_fd = acquire_backup_lock()
    if lock_fd is None:
        return jsonify({'success': False, 'message': 'Could not acquire backup lock'}), 500

    try:
        start = time.time()
        result = manager.create_backup(backup_type)
        duration = time.time() - start

        log_backup_event(
            'create',
            result.get('filename'),
            'success' if result['success'] else 'failed',
            result.get('message'),
        )
        log_admin_action(
            'backup.create',
            f"{backup_type} backup "
            f"{'succeeded' if result['success'] else 'failed'}",
            severity='info' if result['success'] else 'critical',
        )
        write_audit(
            action='backup.create',
            target_type='backup',
            target_id=result.get('filename'),
            before=None,
            after={'type': backup_type, 'success': result['success']},
            severity='info' if result['success'] else 'warning',
        )

        return jsonify({
            'success': result['success'],
            'message': result['message'],
            'filename': result.get('filename'),
            'size_kb': (result.get('size_bytes') or 0) / 1024,
            'duration': duration,
        })
    finally:
        release_backup_lock(lock_fd)


# ============================================================
# VERIFY
# ============================================================

@admin_backup_bp.route('/admin/backups/verify/<filename>', methods=['POST'])
@admin_can('backups.view')
def verify_backup(filename):
    token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    if not token or token != session.get('csrf_token'):
        return jsonify({'success': False, 'message': 'Invalid session'}), 403

    manager = get_manager()
    file_path = os.path.join(manager.backup_dir, filename)

    if not os.path.exists(file_path):
        return jsonify({'success': False, 'message': 'File not found'}), 404

    ver_ok, details = manager.verify_backup(file_path)

    entry = manager._find_backup_entry(filename)
    if entry:
        entry['integrity_status'] = 'valid' if ver_ok else 'invalid'
        entry['integrity_result'] = (
            details.get('integrity_msg') if ver_ok
            else details.get('error')
        )
        manager._save_manifest()

    log_admin_action(
        'backup.verify',
        f"Verification of {filename}: {'OK' if ver_ok else 'FAILED'}",
        severity='info' if ver_ok else 'warning',
    )
    write_audit(
        action='backup.verify',
        target_type='backup',
        target_id=filename,
        before=None,
        after={'success': ver_ok},
        severity='info' if ver_ok else 'warning',
    )
    return jsonify({'success': ver_ok, 'details': details})


# ============================================================
# RESTORE
# ============================================================

@admin_backup_bp.route('/admin/backups/restore/<filename>', methods=['POST'])
@admin_can('backups.restore')
def restore_backup(filename):
    token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    if not token or token != session.get('csrf_token'):
        return jsonify({'success': False, 'message': 'Invalid session'}), 403

    password = request.form.get('confirm_password') or ''
    if not password or password != Config.ADMIN_ERROR_PASSWORD:
        write_audit(
            action='backup.restore.denied',
            target_type='backup',
            target_id=filename,
            before=None, after=None,
            note='Invalid confirmation password',
            severity='critical',
        )
        return jsonify({'success': False, 'message': 'Invalid admin password'}), 403

    manager = get_manager()
    result = manager.restore_web(filename, admin_id=session['user_id'])

    log_backup_event(
        'restore',
        filename,
        'success' if result['success'] else 'failed',
        result['message'],
    )
    log_admin_action(
        'backup.restore',
        f"Restore of {filename} {'succeeded' if result['success'] else 'failed'}",
        severity='critical',
    )
    write_audit(
        action='backup.restore',
        target_type='backup',
        target_id=filename,
        before=None,
        after={'success': result['success']},
        severity='critical',
    )
    return jsonify(result)


# ============================================================
# DELETE
# ============================================================

@admin_backup_bp.route('/admin/backups/delete/<filename>', methods=['POST'])
@admin_can('backups.delete')
def delete_backup(filename):
    token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    if not token or token != session.get('csrf_token'):
        return jsonify({'success': False, 'message': 'Invalid session'}), 403

    manager = get_manager()
    file_path = os.path.join(manager.backup_dir, filename)

    if not os.path.exists(file_path):
        return jsonify({'success': False, 'message': 'File not found'}), 404

    try:
        os.remove(file_path)
        for entry in list(manager.manifest['backups']):
            if entry['filename'] == filename:
                manager.manifest['backups'].remove(entry)
                break
        manager._save_manifest()

        log_admin_action(
            'backup.delete',
            f"Deleted backup {filename}",
            severity='info',
        )
        write_audit(
            action='backup.delete',
            target_type='backup',
            target_id=filename,
            before={'existed': True},
            after={'existed': False},
            severity='warning',
        )
        return jsonify({'success': True, 'message': 'Backup deleted'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ============================================================
# DOWNLOAD
# ============================================================

@admin_backup_bp.route('/admin/backups/download/<filename>')
@admin_can('backups.view')
def download_backup(filename):
    manager = get_manager()
    file_path = os.path.join(manager.backup_dir, filename)

    if not os.path.exists(file_path):
        abort(404)

    real_path = os.path.realpath(file_path)
    base_path = os.path.realpath(manager.backup_dir)
    if not (real_path == base_path or real_path.startswith(base_path + os.sep)):
        abort(403)

    write_audit(
        action='backup.download',
        target_type='backup',
        target_id=filename,
        before=None, after=None,
        severity='warning',
    )

    return send_file(file_path, as_attachment=True, download_name=filename)


# ============================================================
# SETTINGS
# ============================================================

@admin_backup_bp.route('/admin/backups/settings', methods=['GET', 'POST'])
@admin_can('backups.settings')
def settings():
    if request.method == 'POST':
        token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
        if not token or token != session.get('csrf_token'):
            return jsonify({'success': False, 'error': 'Invalid session'}), 403

        try:
            data = {
                'daily_retention': int(request.form.get('daily_retention', 7)),
                'weekly_retention': int(request.form.get('weekly_retention', 4)),
                'monthly_retention': int(request.form.get('monthly_retention', 12)),
                'scheduled_enabled': (
                    1 if request.form.get('scheduled_enabled') == 'on' else 0
                ),
                'scheduled_type': request.form.get('scheduled_type', 'daily'),
                'scheduled_time': request.form.get('scheduled_time', '02:00'),
            }
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Invalid retention values'}), 400

        _update_backup_config(data)
        log_admin_action('backup.settings', "Updated backup settings", severity='info')
        write_audit(
            action='backup.settings',
            target_type='platform',
            before=None, after=data,
            severity='info',
        )
        return jsonify({'success': True, 'message': 'Settings saved'})

    config = _get_backup_config()
    return render_template(
        'dashboard/admin/ops/backup_settings.html',
        config=config,
    )