# admin_backup_bp.py
import os, time, json
from flask import Blueprint, render_template, request, session, jsonify, abort, send_file
from functools import wraps
from db import is_admin, execute_with_retry
from backup import BackupManager, is_backup_locked, acquire_backup_lock, release_backup_lock
from activity_logger import log_backup_event, log_admin_action
from config import Config

admin_backup_bp = Blueprint('admin_backup', __name__, url_prefix='/admin/backup')

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session or not is_admin(session['user_id']):
            abort(403)
        return f(*args, **kwargs)
    return decorated

def get_manager():
    return BackupManager()

@admin_backup_bp.route('/')
@admin_required
def dashboard():
    manager = get_manager()
    health = manager.get_backup_health_summary()
    config = _get_backup_config()
    return render_template('dashboard/admin/backup_dashboard.html', health=health, config=config)

@admin_backup_bp.route('/list')
@admin_required
def list_backups():
    manager = get_manager()
    backups = manager.get_backup_list(valid_only=False)
    for b in backups:
        file_path = os.path.join(manager.backup_dir, b['filename'])
        b['exists'] = os.path.exists(file_path)
        b['size_kb'] = b.get('size_bytes', 0) / 1024
    return render_template('dashboard/admin/backup_list.html', backups=backups)

@admin_backup_bp.route('/create', methods=['POST'])
@admin_required
def create_backup():
    backup_type = request.form.get('type', 'daily')
    if backup_type not in ['daily', 'weekly', 'monthly', 'manual']:
        return jsonify({'error': 'Invalid type'}), 400
    if is_backup_locked():
        return jsonify({'error': 'Backup already running'}), 409

    manager = get_manager()
    lock_fd = acquire_backup_lock()
    if lock_fd is None:
        return jsonify({'error': 'Could not acquire lock'}), 500

    try:
        start = time.time()
        result = manager.create_backup(backup_type)
        duration = time.time() - start
        log_backup_event('create', result.get('filename'), 'success' if result['success'] else 'failed', result.get('message'))
        log_admin_action('backup.create', f"{backup_type} backup {'succeeded' if result['success'] else 'failed'}", severity='info' if result['success'] else 'critical')
        return jsonify({
            'success': result['success'],
            'message': result['message'],
            'filename': result.get('filename'),
            'size_kb': result.get('size_bytes', 0) / 1024,
            'duration': duration
        })
    finally:
        release_backup_lock(lock_fd)

@admin_backup_bp.route('/restore/<filename>', methods=['POST'])
@admin_required
def restore_backup(filename):
    password = request.form.get('confirm_password')
    if not password or password != Config.ADMIN_ERROR_PASSWORD:
        return jsonify({'error': 'Invalid admin password'}), 403
    manager = get_manager()
    result = manager.restore_web(filename, admin_id=session['user_id'])
    log_backup_event('restore', filename, 'success' if result['success'] else 'failed', result['message'])
    log_admin_action('backup.restore', f"Restore of {filename} {'succeeded' if result['success'] else 'failed'}", severity='critical')
    return jsonify(result)

@admin_backup_bp.route('/verify/<filename>', methods=['POST'])
@admin_required
def verify_backup(filename):
    manager = get_manager()
    file_path = os.path.join(manager.backup_dir, filename)
    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    ver_ok, details = manager.verify_backup(file_path)
    entry = manager._find_backup_entry(filename)
    if entry:
        entry['integrity_status'] = 'valid' if ver_ok else 'invalid'
        entry['integrity_result'] = details.get('integrity_msg') if ver_ok else details.get('error')
        manager._save_manifest()
    log_admin_action('backup.verify', f"Verification of {filename}: {'OK' if ver_ok else 'FAILED'}", severity='info' if ver_ok else 'warning')
    return jsonify({'success': ver_ok, 'details': details})

@admin_backup_bp.route('/delete/<filename>', methods=['POST'])
@admin_required
def delete_backup(filename):
    manager = get_manager()
    file_path = os.path.join(manager.backup_dir, filename)
    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    try:
        os.remove(file_path)
        for entry in manager.manifest['backups']:
            if entry['filename'] == filename:
                manager.manifest['backups'].remove(entry)
                break
        manager._save_manifest()
        log_admin_action('backup.delete', f"Deleted backup {filename}", severity='info')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@admin_backup_bp.route('/download/<filename>')
@admin_required
def download_backup(filename):
    manager = get_manager()
    file_path = os.path.join(manager.backup_dir, filename)
    if not os.path.exists(file_path):
        abort(404)
    real_path = os.path.realpath(file_path)
    if not real_path.startswith(os.path.realpath(manager.backup_dir)):
        abort(403)
    return send_file(file_path, as_attachment=True, download_name=filename)

@admin_backup_bp.route('/settings', methods=['GET', 'POST'])
@admin_required
def settings():
    if request.method == 'POST':
        data = {
            'daily_retention': int(request.form.get('daily_retention', 7)),
            'weekly_retention': int(request.form.get('weekly_retention', 4)),
            'monthly_retention': int(request.form.get('monthly_retention', 12)),
            'scheduled_enabled': 1 if request.form.get('scheduled_enabled') == 'on' else 0,
            'scheduled_type': request.form.get('scheduled_type', 'daily'),
            'scheduled_time': request.form.get('scheduled_time', '02:00')
        }
        _update_backup_config(data)
        log_admin_action('backup.settings', "Updated backup settings", severity='info', settings=data)
        return jsonify({'success': True})
    config = _get_backup_config()
    return render_template('dashboard/admin/backup_settings.html', config=config)

def _get_backup_config():
    cursor = execute_with_retry("SELECT * FROM backup_config WHERE id = 1")
    row = cursor.fetchone()
    if row:
        return dict(row)
    execute_with_retry("INSERT OR IGNORE INTO backup_config (id) VALUES (1)")
    return dict(execute_with_retry("SELECT * FROM backup_config WHERE id = 1").fetchone())

def _update_backup_config(data):
    execute_with_retry("""
        UPDATE backup_config SET
            daily_retention = ?, weekly_retention = ?, monthly_retention = ?,
            scheduled_enabled = ?, scheduled_type = ?, scheduled_time = ?,
            last_modified = datetime('now', 'localtime')
        WHERE id = 1
    """, (data['daily_retention'], data['weekly_retention'], data['monthly_retention'],
          data['scheduled_enabled'], data['scheduled_type'], data['scheduled_time']), commit=True)