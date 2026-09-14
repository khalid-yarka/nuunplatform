# ============================================================
# blueprints/admin/users_bp.py
# User management — list, detail, tier, admin toggle, notify,
# password reset, force logout, public ID, delete, restore.
#
# Routes:
#   GET  /admin/users
#   GET  /admin/users/export
#   GET  /admin/users/<int:user_id>
#   POST /admin/users/<int:user_id>/note
#   POST /admin/users/<int:user_id>/tier
#   POST /admin/users/<int:user_id>/toggle-admin
#   POST /admin/users/<int:user_id>/notify
#   POST /admin/users/<int:user_id>/reset-password
#   POST /admin/users/<int:user_id>/force-logout
#   POST /admin/users/<int:user_id>/public-id
#   POST /admin/users/<int:user_id>/delete
#   POST /admin/users/bulk
#   GET  /admin/users/tier/<int:user_id>
#   POST /admin/users/tier/<int:user_id>
#   GET  /admin/deleted-users
#   POST /admin/deleted-users/restore/<int:deleted_id>
# ============================================================

from flask import (
    Blueprint, render_template, request, session, flash,
    redirect, url_for, abort, jsonify, Response,
)
import logging

from db import (
    get_student_by_id,
    delete_user as db_delete_user,
    get_deleted_users,
    restore_deleted_user as db_restore_user,
    get_user_subject_list,
    is_admin,
)
from services.tier_service import (
    get_user_tier, set_user_tier, get_current_user_tier,
)
from admin_users_db import (
    ensure_admin_user_schema,
    get_users_admin, get_users_admin_export, get_users_admin_stats,
    users_to_csv, set_user_admin_note, set_user_tier_admin,
    toggle_user_admin_admin, reset_user_password, force_user_logout,
    set_user_public_id, get_user_admin_history, get_user_recent_quizzes_admin,
    get_user_recent_live_quizzes, bulk_user_action, log_admin_user_action,
)
from utils import validate_csrf
from services.admin.guards import admin_can
from services.admin.audit import write_audit
from activity_logger import log_admin_action

logger = logging.getLogger(__name__)

admin_users_bp = Blueprint('admin_users', __name__, url_prefix='/admin')


# ============================================================
# LIST — /admin/users
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
        search=search,
        tier_filter=tier_filter,
        location_filter=location_filter,
        curriculum_filter=curriculum_filter,
        only_admins=only_admins,
        only_inactive=only_inactive,
        sort=sort,
        page=page,
        per_page=per_page,
    )
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    stats = get_users_admin_stats()

    for user in users:
        user['tier'] = user.get('tier') or 'free'

    return render_template(
        'dashboard/admin/access/users.html',
        users=users,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        stats=stats,
        search=search,
        tier_filter=tier_filter,
        location_filter=location_filter,
        curriculum_filter=curriculum_filter,
        only_admins=only_admins,
        only_inactive=only_inactive,
        sort=sort,
    )


# ============================================================
# EXPORT — /admin/users/export
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
        action='users.export',
        target_type='user',
        before=None,
        after={'count': len(rows)},
        severity='info',
    )

    return Response(
        users_to_csv(rows),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=users_export.csv'},
    )


# ============================================================
# DETAIL — /admin/users/<id>
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

    return render_template(
        'dashboard/admin/access/user_detail.html',
        user=user,
        quizzes=quizzes,
        live_quizzes=live_quizzes,
        history=history,
        total_quizzes=len(quizzes),
        avg_score=avg,
    )


# ============================================================
# NOTE — /admin/users/<id>/note
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
# TIER — /admin/users/<id>/tier
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/tier', methods=['POST'],
                      endpoint='set_tier')
@admin_can('users.set_tier')
def set_tier(user_id):
    validate_csrf()
    new_tier = (request.form.get('tier') or '').strip().lower()
    if set_user_tier_admin(user_id, new_tier, session['user_id']):
        write_audit(
            action='user.set_tier',
            target_type='user',
            target_id=user_id,
            before=None,
            after={'tier': new_tier},
            severity='warning',
        )
        flash(f'Tier updated to {new_tier.upper()}.', 'success')
    else:
        flash('Failed to update tier.', 'error')
    return redirect(url_for('admin_users.user_detail', user_id=user_id))


# ============================================================
# TOGGLE ADMIN — /admin/users/<id>/toggle-admin
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
            action='user.toggle_admin',
            target_type='user',
            target_id=user_id,
            before=None,
            after={'is_admin': new_state},
            severity='critical',
        )
        flash('Admin privileges granted.' if new_state
              else 'Admin privileges revoked.', 'success')
    return redirect(url_for('admin_users.user_detail', user_id=user_id))


# ============================================================
# NOTIFY — /admin/users/<id>/notify
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
        action='user.notify',
        target_type='user',
        target_id=user_id,
        before=None,
        after={'title': title},
        severity='info',
    )
    flash('Notification sent.', 'success')
    return redirect(url_for('admin_users.user_detail', user_id=user_id))


# ============================================================
# RESET PASSWORD — /admin/users/<id>/reset-password
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/reset-password', methods=['POST'],
                      endpoint='reset_password')
@admin_can('users.reset_password')
def reset_password(user_id):
    validate_csrf()
    new_pw = (request.form.get('new_password') or '').strip()
    if len(new_pw) < 8:
        flash('Password must be at least 8 characters.', 'error')
    elif reset_user_password(user_id, new_pw, session['user_id']):
        write_audit(
            action='user.reset_password',
            target_type='user',
            target_id=user_id,
            before=None,
            after=None,
            severity='critical',
        )
        flash('Password reset successfully.', 'success')
    else:
        flash('Failed to reset password.', 'error')
    return redirect(url_for('admin_users.user_detail', user_id=user_id))


# ============================================================
# FORCE LOGOUT — /admin/users/<id>/force-logout
# ============================================================

@admin_users_bp.route('/users/<int:user_id>/force-logout', methods=['POST'],
                      endpoint='force_logout')
@admin_can('users.force_logout')
def force_logout(user_id):
    validate_csrf()
    ok = force_user_logout(user_id, session['user_id'])
    if ok:
        write_audit(
            action='user.force_logout',
            target_type='user',
            target_id=user_id,
            before=None,
            after=None,
            severity='warning',
        )
    flash('User will be logged out on next request.' if ok
          else 'Failed to force logout.',
          'success' if ok else 'error')
    return redirect(url_for('admin_users.user_detail', user_id=user_id))


# ============================================================
# PUBLIC ID — /admin/users/<id>/public-id
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
            action='user.set_public_id',
            target_type='user',
            target_id=user_id,
            before=None,
            after={'public_id': new_id},
            severity='info',
        )
    flash(msg, 'success' if ok else 'error')
    return redirect(url_for('admin_users.user_detail', user_id=user_id))


# ============================================================
# DELETE — /admin/users/<id>/delete
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
            action='user.delete',
            target_type='user',
            target_id=user_id,
            before=None,
            after=None,
            severity='critical',
        )
        flash('User deleted successfully.', 'success')
        return redirect(url_for('admin_users.list_users'))

    flash(f'Error deleting user: {message}', 'error')
    return redirect(url_for('admin_users.user_detail', user_id=user_id))


# ============================================================
# BULK — /admin/users/bulk
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

    succeeded, failed = bulk_user_action(action, user_ids,
                                         session['user_id'], extra)

    write_audit(
        action=f'users.bulk_{action}',
        target_type='user',
        before=None,
        after={'succeeded': succeeded, 'failed': failed},
        severity='warning',
    )

    if succeeded:
        flash(f'Bulk {action}: {succeeded} succeeded.', 'success')
    if failed:
        flash(f'Bulk {action}: {failed} skipped or failed.', 'error')

    return redirect(request.referrer or url_for('admin_users.list_users'))


# ============================================================
# TIER MANAGEMENT PAGE — /admin/users/tier/<id>
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
                f"from {current_tier} to {new_tier}",
                'warning',
            )
            write_audit(
                action='user.set_tier',
                target_type='user',
                target_id=user_id,
                before={'tier': current_tier},
                after={'tier': new_tier},
                severity='warning',
            )
            flash(f"User tier updated to {new_tier.capitalize()}.", 'success')
        else:
            flash('Failed to update tier.', 'error')

        return redirect(url_for('admin_users.user_detail', user_id=user_id))

    return render_template(
        'dashboard/admin/access/user_tier.html',
        user=user,
        current_tier=current_tier,
        admin_tier=admin_tier,
    )


# ============================================================
# DELETED USERS — /admin/deleted-users
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
            f"Restored user from deleted_id {deleted_id}",
            'info',
        )
        write_audit(
            action='user.restore',
            target_type='user',
            before=None,
            after={'deleted_id': deleted_id},
            severity='warning',
        )
        flash('User restored successfully!', 'success')
    else:
        flash(f'Error restoring user: {message}', 'error')

    return redirect(url_for('admin_users.deleted_users'))