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

from config import Config
from db import (
    get_student_by_id,
    delete_user as db_delete_user,
    get_deleted_users,
    restore_deleted_user as db_restore_user,
    get_user_subject_list,
    is_admin,
    execute_with_retry,
)
from services.tier_service import (
    get_user_tier, set_user_tier, get_current_user_tier,
)
from admin_users_db import (
    ensure_admin_user_schema,
    get_users_admin, get_users_admin_export, get_users_admin_stats,
    users_to_csv, set_user_admin_note, set_user_tier_admin,
    toggle_user_admin_admin, reset_user_password, reset_user_password_to_default,
    force_user_logout,
    set_user_public_id, get_user_admin_history, get_user_recent_quizzes_admin,
    get_user_recent_live_quizzes, bulk_user_action, log_admin_user_action,
)
from utils import validate_csrf
from services.admin.guards import admin_can
from services.admin.audit import write_audit
from activity_logger import log_admin_action

logger = logging.getLogger(__name__)

admin_users_bp = Blueprint('admin_users', __name__, url_prefix='/admin')

DEFAULT_PASSWORD_PRESET = '12345678'


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
    only_admins = request.args.get('admins') == '1'
    only_inactive = request.args.get('inactive') == '1'
    sort = (request.args.get('sort') or 'newest').strip()
    page = max(1, int(request.args.get('page') or 1))
    per_page = 25

    users, total = get_users_admin(
        search=search, tier_filter=tier_filter,
        location_filter=location_filter, curriculum_filter=curriculum_filter,
        only_admins=only_admins, only_inactive=only_inactive,
        sort=sort, page=page, per_page=per_page,
    )
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    stats = get_users_admin_stats()

    for user in users:
        user['tier'] = user.get('tier') or 'free'

    return render_template(
        'dashboard/admin/access/users.html',
        users=users, total=total, page=page, per_page=per_page,
        total_pages=total_pages, stats=stats, search=search,
        tier_filter=tier_filter, location_filter=location_filter,
        curriculum_filter=curriculum_filter, only_admins=only_admins,
        only_inactive=only_inactive, sort=sort,
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

    quizzes = get_user_recent_quizzes_admin(user_id, limit=20)
    live_quizzes = get_user_recent_live_quizzes(user_id, limit=10)
    history = get_user_admin_history(user_id, limit=50)

    avg = 0
    if quizzes:
        avg = round(sum(q['percentage'] for q in quizzes) / len(quizzes), 1)

    # Active session info (best-effort)
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

    # Generate a fresh random password for the "suggest" button
    alphabet = string.ascii_letters + string.digits
    suggested_password = ''.join(secrets.choice(alphabet) for _ in range(12))

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
        default_password_preset=DEFAULT_PASSWORD_PRESET,
    )


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
    return redirect(url_for('admin_users.user_detail', user_id=user_id))


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
    return redirect(url_for('admin_users.user_detail', user_id=user_id))


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
    return redirect(url_for('admin_users.user_detail', user_id=user_id))


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
        return redirect(url_for('admin_users.user_detail', user_id=user_id))

    from db import create_notification
    create_notification(user_id, 'admin_direct', title, body, '/dashboard', '📬')
    log_admin_user_action(session['user_id'], user_id, 'notify', None, title[:200])

    write_audit(
        action='user.notify', target_type='user', target_id=user_id,
        before=None, after={'title': title}, severity='info',
    )
    flash('Notification sent.', 'success')
    return redirect(url_for('admin_users.user_detail', user_id=user_id))


# ============================================================
# PASSWORD — custom value
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
        return redirect(url_for('admin_users.user_detail', user_id=user_id))

    if new_pw != confirm:
        flash('Passwords do not match.', 'error')
        return redirect(url_for('admin_users.user_detail', user_id=user_id))

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

    return redirect(url_for('admin_users.user_detail', user_id=user_id))


# ============================================================
# PASSWORD — reset to 12345678 (one click)
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/reset-password-default',
                      methods=['POST'],
                      endpoint='reset_password_to_default')
@admin_can('users.reset_password')
def reset_password_to_default(user_id):
    validate_csrf()

    if user_id == session['user_id']:
        flash('Use Settings → Security to change your own password.', 'error')
        return redirect(url_for('admin_users.user_detail', user_id=user_id))

    ok, msg = reset_user_password_to_default(
        user_id,
        session['user_id'],
        default_password=DEFAULT_PASSWORD_PRESET,
        force_logout=True,
        notify_user=True,
    )

    if ok:
        write_audit(
            action='user.reset_password', target_type='user', target_id=user_id,
            before=None,
            after={'mode': 'preset_12345678'},
            severity='critical',
        )
        flash(f'Password reset to {DEFAULT_PASSWORD_PRESET}. '
              f'User must log in again.', 'success')
    else:
        flash(msg or 'Failed to reset password.', 'error')

    return redirect(url_for('admin_users.user_detail', user_id=user_id))


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
    return redirect(url_for('admin_users.user_detail', user_id=user_id))


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
    return redirect(url_for('admin_users.user_detail', user_id=user_id))


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
    elif action == 'reset_password_default':
        extra['password'] = DEFAULT_PASSWORD_PRESET

    # Bulk password reset — special handling since it needs werkzeug
    if action == 'reset_password_default':
        from werkzeug.security import generate_password_hash
        succeeded = 0
        failed = 0
        pw_hash = generate_password_hash(DEFAULT_PASSWORD_PRESET)
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
                                      'reset_password', None, 'preset_12345678')
                succeeded += 1
            except Exception:
                failed += 1

        write_audit(
            action='users.bulk_reset_password_default',
            target_type='user', before=None,
            after={'succeeded': succeeded, 'failed': failed},
            severity='critical',
        )

        if succeeded:
            flash(f'Bulk reset: {succeeded} user(s) set to '
                  f'{DEFAULT_PASSWORD_PRESET}.', 'success')
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

    current_tier = get_user_tier(user_id)
    admin_tier = get_current_user_tier()

    if request.method == 'POST':
        validate_csrf()
        new_tier = request.form.get('tier')
        if new_tier not in ('free', 'premium', 'pro'):
            flash('Invalid tier value.', 'error')
            return redirect(url_for('admin_users.manage_user_tier',
                                    user_id=user_id))

        if set_user_tier(user_id, new_tier, session['user_id']):
            log_admin_user_action(session['user_id'], user_id, 'set_tier',
                                  current_tier, new_tier)
            log_admin_action(
                'tier.change',
                f"Admin {session['user_id']} changed tier of {user_id} "
                f"from {current_tier} to {new_tier}", 'warning',
            )
            write_audit(
                action='user.set_tier', target_type='user', target_id=user_id,
                before={'tier': current_tier}, after={'tier': new_tier},
                severity='warning',
            )
            flash(f"User tier updated to {new_tier.capitalize()}.", 'success')
        else:
            flash('Failed to update tier.', 'error')

        return redirect(url_for('admin_users.user_detail', user_id=user_id))

    return render_template(
        'dashboard/admin/access/user_tier.html',
        user=user, current_tier=current_tier, admin_tier=admin_tier,
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