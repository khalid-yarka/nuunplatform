# blueprints/admin_bp.py
# Advanced admin: question management (list/edit/add), bulk import with live preview,
# users, groups, PDFs, reports, announcements.

from flask import (
    Blueprint, render_template, request, session, flash, redirect, url_for,
    jsonify, abort, Response,
)
from db import (
    is_admin, get_all_students, get_all_questions, get_question_by_id,
    toggle_admin, delete_user as db_delete_user, get_deleted_users,
    restore_deleted_user as db_restore_user, create_question, update_question,
    delete_question, create_group, delete_group, get_all_groups,
    bulk_create_questions, check_question_exists,
    create_notification_for_all_users,
    execute_with_retry,
    get_user_subject_list, get_student_by_id, get_group_by_id,
    get_group_categories_with_count,
    # Focus / question management helpers
    get_questions_paginated, get_question_stats, get_questions_filter_options,
    check_pdf_codes_exist,
)
from error_models import get_error_stats
from functools import wraps
import json
import re
from subjects_config import get_all_subjects, get_subject
from services.tier_service import get_user_tier, set_user_tier, get_current_user_tier
from services.notification_service import send_notification_to_all
from services.group_service import (
    get_admin_group_list, create_group as svc_create_group,
    update_group, delete_group as svc_delete_group,
    toggle_active, toggle_featured, get_group_stats, get_group_audit_log,
)
from activity_logger import log_admin_action

from db import (
    get_all_pdfs, get_pdf_by_id, get_pdf_by_code, create_main_pdf,
    delete_main_pdf,
)

from services.interaction_service import (
    get_pending_reports, get_all_reports, count_reports,
    resolve_report, dismiss_report, get_report_by_id,
)

from admin_users_db import (
    ensure_admin_user_schema,
    get_users_admin, get_users_admin_export, get_users_admin_stats,
    users_to_csv, set_user_admin_note, set_user_tier_admin,
    toggle_user_admin_admin, reset_user_password, force_user_logout,
    set_user_public_id, get_user_admin_history, get_user_recent_quizzes_admin,
    get_user_recent_live_quizzes, bulk_user_action, log_admin_user_action,
)

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


# ============================================
# DECORATORS / GUARDS
# ============================================

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login first.', 'error')
            return redirect(url_for('auth.login'))
        if not is_admin(session['user_id']):
            flash('Access denied. Admin only.', 'error')
            return redirect(url_for('dashboard.home'))
        return f(*args, **kwargs)
    return decorated


def validate_csrf():
    token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    if not token or token != session.get('csrf_token'):
        abort(403, 'CSRF token validation failed')


# ============================================
# PDF HELPERS
# ============================================

_PDF_CODE_RE = re.compile(r'^[A-Z0-9]{4}-[A-Z0-9]{4}$')


def _normalize_pdf_code(raw):
    if raw is None:
        return None
    code = str(raw).strip().upper()
    return code or None


def _validate_pdf_code_format(code):
    return bool(code) and bool(_PDF_CODE_RE.match(code))


def _normalize_pdf_page(raw):
    if raw is None or raw == '':
        return None
    try:
        page = int(raw)
    except (ValueError, TypeError):
        return None
    return page if page > 0 else None


def _resolve_pdf_for_import(q, default_code):
    """
    Returns (code, page, orphan_page_dropped, invalid_format).
    Precedence: explicit override > inherit default.
    An explicit empty string clears inheritance.
    """
    page = _normalize_pdf_page(q.get('pdf_page'))

    if 'pdf_code' in q:
        raw = q.get('pdf_code')
        code = None if (raw is None or raw == '') else _normalize_pdf_code(raw)
    else:
        code = default_code

    invalid = False
    if code and not _validate_pdf_code_format(code):
        invalid = True
        code = None

    orphan = False
    if page and not code:
        orphan = True
        page = None

    return code, page, orphan, invalid


# ============================================
# ADMIN DASHBOARD
# ============================================

@admin_bp.route('/')
@admin_required
def dashboard():
    users = get_all_students()
    groups = get_all_groups()
    pdfs = get_all_pdfs()
    error_stats = get_error_stats()
    q_stats = get_question_stats()

    try:
        from backup import BackupManager
        manager = BackupManager()
        backup_health = manager.get_backup_health_summary()
    except Exception as e:
        backup_health = {'status': 'error', 'issues': [str(e)]}

    try:
        cursor = execute_with_retry(
            "SELECT * FROM activity_logs ORDER BY created_at DESC LIMIT 10"
        )
        recent_activity = [dict(row) for row in cursor.fetchall()]
    except Exception:
        recent_activity = []

    return render_template('dashboard/admin/dashboard.html',
                         users_count=len(users),
                         groups_count=len(groups),
                         pdfs_count=len(pdfs),
                         subjects_count=len(get_all_subjects()),
                         questions_count=q_stats['total'],
                         question_stats=q_stats,
                         quiz_attempts=0,
                         error_stats=error_stats,
                         backup_health=backup_health,
                         recent_activity=recent_activity)


# ============================================
# USERS (unchanged — abbreviated for brevity in this answer)
# ============================================

@admin_bp.route('/users')
@admin_required
def admin_users():
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
        search=search, tier_filter=tier_filter, location_filter=location_filter,
        curriculum_filter=curriculum_filter, only_admins=only_admins,
        only_inactive=only_inactive, sort=sort, page=page, per_page=per_page,
    )
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    stats = get_users_admin_stats()
    for user in users:
        user['tier'] = user.get('tier') or 'free'

    return render_template(
        'dashboard/admin/users.html',
        users=users, total=total, page=page, per_page=per_page,
        total_pages=total_pages, stats=stats, search=search,
        tier_filter=tier_filter, location_filter=location_filter,
        curriculum_filter=curriculum_filter, only_admins=only_admins,
        only_inactive=only_inactive, sort=sort,
    )


@admin_bp.route('/users/export')
@admin_required
def admin_users_export():
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
    return Response(
        users_to_csv(rows),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=users_export.csv'}
    )


@admin_bp.route('/users/<int:user_id>')
@admin_required
def admin_user_detail(user_id):
    ensure_admin_user_schema()
    user = get_student_by_id(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin.admin_users'))
    user['tier'] = user.get('tier') or 'free'
    quizzes = get_user_recent_quizzes_admin(user_id, limit=20)
    live_quizzes = get_user_recent_live_quizzes(user_id, limit=10)
    history = get_user_admin_history(user_id, limit=50)
    avg = 0
    if quizzes:
        avg = round(sum(q['percentage'] for q in quizzes) / len(quizzes), 1)
    return render_template(
        'dashboard/admin/user_detail.html',
        user=user, quizzes=quizzes, live_quizzes=live_quizzes,
        history=history, total_quizzes=len(quizzes), avg_score=avg,
    )


@admin_bp.route('/users/<int:user_id>/note', methods=['POST'])
@admin_required
def admin_user_set_note(user_id):
    validate_csrf()
    note = (request.form.get('note') or '').strip()
    flash('Admin note saved.' if set_user_admin_note(user_id, note, session['user_id'])
          else 'Failed to save note.', 'success' if set_user_admin_note(user_id, note, session['user_id']) else 'error')
    return redirect(url_for('admin.admin_user_detail', user_id=user_id))


@admin_bp.route('/users/<int:user_id>/tier', methods=['POST'])
@admin_required
def admin_user_set_tier(user_id):
    validate_csrf()
    new_tier = (request.form.get('tier') or '').strip().lower()
    if set_user_tier_admin(user_id, new_tier, session['user_id']):
        flash(f'Tier updated to {new_tier.upper()}.', 'success')
    else:
        flash('Failed to update tier.', 'error')
    return redirect(url_for('admin.admin_user_detail', user_id=user_id))


@admin_bp.route('/users/<int:user_id>/toggle-admin', methods=['POST'])
@admin_required
def admin_user_toggle_admin(user_id):
    validate_csrf()
    if user_id == session['user_id']:
        flash('You cannot change your own admin status.', 'error')
        return redirect(url_for('admin.admin_user_detail', user_id=user_id))
    new_state = toggle_user_admin_admin(user_id, session['user_id'])
    flash('Admin privileges granted.' if new_state else 'Admin privileges revoked.'
          if new_state is not None else 'Failed to change admin status.',
          'success' if new_state is not None else 'error')
    return redirect(url_for('admin.admin_user_detail', user_id=user_id))


@admin_bp.route('/users/<int:user_id>/notify', methods=['POST'])
@admin_required
def admin_user_notify(user_id):
    validate_csrf()
    title = (request.form.get('title') or '').strip()
    body = (request.form.get('body') or '').strip()
    if not title or not body:
        flash('Title and message are required.', 'error')
        return redirect(url_for('admin.admin_user_detail', user_id=user_id))
    from db import create_notification
    create_notification(user_id, 'admin_direct', title, body, '/dashboard', '📬')
    log_admin_user_action(session['user_id'], user_id, 'notify', None, title[:200])
    flash('Notification sent.', 'success')
    return redirect(url_for('admin.admin_user_detail', user_id=user_id))


@admin_bp.route('/users/<int:user_id>/reset-password', methods=['POST'])
@admin_required
def admin_user_reset_password(user_id):
    validate_csrf()
    new_pw = (request.form.get('new_password') or '').strip()
    if len(new_pw) < 8:
        flash('Password must be at least 8 characters.', 'error')
    elif reset_user_password(user_id, new_pw, session['user_id']):
        flash('Password reset successfully.', 'success')
    else:
        flash('Failed to reset password.', 'error')
    return redirect(url_for('admin.admin_user_detail', user_id=user_id))


@admin_bp.route('/users/<int:user_id>/force-logout', methods=['POST'])
@admin_required
def admin_user_force_logout(user_id):
    validate_csrf()
    flash('User will be logged out on next request.' if force_user_logout(user_id, session['user_id'])
          else 'Failed to force logout.', 'success' if force_user_logout(user_id, session['user_id']) else 'error')
    return redirect(url_for('admin.admin_user_detail', user_id=user_id))


@admin_bp.route('/users/<int:user_id>/public-id', methods=['POST'])
@admin_required
def admin_user_set_public_id(user_id):
    validate_csrf()
    new_id = (request.form.get('public_id') or '').strip().upper()
    ok, msg = set_user_public_id(user_id, new_id, session['user_id'])
    flash(msg, 'success' if ok else 'error')
    return redirect(url_for('admin.admin_user_detail', user_id=user_id))


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def admin_user_delete(user_id):
    validate_csrf()
    if user_id == session['user_id']:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('admin.admin_users'))
    keep_ratings = request.form.get('keep_ratings', 'on') == 'on'
    delete_attempts = request.form.get('delete_attempts', 'on') == 'on'
    success, message = db_delete_user(user_id, session['user_id'], keep_ratings, delete_attempts)
    if success:
        try:
            log_admin_user_action(session['user_id'], user_id, 'delete')
        except Exception:
            pass
        flash('User deleted successfully.', 'success')
        return redirect(url_for('admin.admin_users'))
    flash(f'Error deleting user: {message}', 'error')
    return redirect(url_for('admin.admin_user_detail', user_id=user_id))


@admin_bp.route('/users/bulk', methods=['POST'])
@admin_required
def admin_users_bulk():
    validate_csrf()
    action = (request.form.get('action') or '').strip()
    ids = request.form.getlist('user_ids')
    if not ids:
        flash('No users selected.', 'error')
        return redirect(request.referrer or url_for('admin.admin_users'))
    try:
        user_ids = [int(x) for x in ids]
    except ValueError:
        flash('Invalid user selection.', 'error')
        return redirect(request.referrer or url_for('admin.admin_users'))
    user_ids = [u for u in user_ids
                if u != session['user_id'] or action not in ('delete', 'demote_admin')]
    extra = {}
    if action == 'set_tier':
        extra['tier'] = (request.form.get('bulk_tier') or '').strip().lower()
    elif action == 'notify':
        extra['title'] = (request.form.get('bulk_title') or '').strip()
        extra['body'] = (request.form.get('bulk_body') or '').strip()
    succeeded, failed = bulk_user_action(action, user_ids, session['user_id'], extra)
    if succeeded:
        flash(f'Bulk {action}: {succeeded} succeeded.', 'success')
    if failed:
        flash(f'Bulk {action}: {failed} skipped or failed.', 'error')
    return redirect(request.referrer or url_for('admin.admin_users'))


@admin_bp.route('/users/toggle_admin/<user_id>', methods=['POST'])
@admin_required
def toggle_user_admin(user_id):
    validate_csrf()
    if user_id == session['user_id']:
        flash('You cannot change your own admin status.', 'error')
        return redirect(url_for('admin.admin_users'))
    result = toggle_admin(user_id)
    flash('Admin status updated.' if result else 'Error updating admin status.',
          'success' if result else 'error')
    if result:
        log_admin_action('admin.toggle', f"Toggled admin for user {user_id}", 'info')
    return redirect(url_for('admin.admin_users'))


@admin_bp.route('/users/tier/<int:user_id>', methods=['GET', 'POST'])
@admin_required
def manage_user_tier(user_id):
    user = get_student_by_id(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin.admin_users'))
    current_tier = get_user_tier(user_id)
    admin_tier = get_current_user_tier()
    if request.method == 'POST':
        validate_csrf()
        new_tier = request.form.get('tier')
        if new_tier not in ['free', 'premium', 'pro']:
            flash('Invalid tier value.', 'error')
            return redirect(url_for('admin.manage_user_tier', user_id=user_id))
        if set_user_tier(user_id, new_tier, session['user_id']):
            log_admin_user_action(session['user_id'], user_id, 'set_tier', current_tier, new_tier)
            log_admin_action('tier.change',
                             f"Admin {session['user_id']} changed tier of {user_id} "
                             f"from {current_tier} to {new_tier}", 'warning')
            flash(f"User tier updated to {new_tier.capitalize()}.", 'success')
        else:
            flash('Failed to update tier.', 'error')
        return redirect(url_for('admin.admin_users'))
    return render_template('dashboard/admin/manage_tier.html',
                           user=user, current_tier=current_tier, admin_tier=admin_tier)


@admin_bp.route('/deleted-users')
@admin_required
def deleted_users():
    return render_template('dashboard/admin/deleted_users.html', deleted=get_deleted_users())


@admin_bp.route('/deleted-users/restore/<deleted_id>', methods=['POST'])
@admin_required
def restore_deleted_user(deleted_id):
    validate_csrf()
    success, message = db_restore_user(deleted_id)
    flash('User restored successfully!' if success else f'Error restoring user: {message}',
          'success' if success else 'error')
    if success:
        log_admin_action('user.restore', f"Restored user from deleted_id {deleted_id}", 'info')
    return redirect(url_for('admin.deleted_users'))


# ============================================
# QUESTIONS — MAIN LIST
# ============================================

@admin_bp.route('/questions')
@admin_required
def admin_questions():
    search = (request.args.get('search') or '').strip()
    subject_code = (request.args.get('subject') or '').strip()
    pdf_filter = (request.args.get('pdf') or '').strip()      # linked | unlinked
    status_filter = (request.args.get('status') or '').strip()  # active | archived
    sort = (request.args.get('sort') or 'newest').strip()
    page = max(1, int(request.args.get('page') or 1))
    per_page = 20

    questions, total = get_questions_paginated(
        search=search, subject_code=subject_code, pdf_filter=pdf_filter,
        status_filter=status_filter, sort=sort, page=page, per_page=per_page,
    )
    stats = get_question_stats()
    filter_options = get_questions_filter_options()
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1

    # Resolve PDF titles for the visible questions (single batch lookup)
    codes_in_page = [q['pdf_code'] for q in questions if q.get('pdf_code')]
    pdf_map = check_pdf_codes_exist(codes_in_page) if codes_in_page else {}

    return render_template(
        'dashboard/admin/questions.html',
        questions=questions, total=total, page=page, per_page=per_page,
        total_pages=total_pages, stats=stats, filter_options=filter_options,
        subjects=get_all_subjects(), pdf_map=pdf_map,
        search=search, subject_code=subject_code, pdf_filter=pdf_filter,
        status_filter=status_filter, sort=sort,
    )


# ============================================
# QUESTIONS — ADD
# ============================================

@admin_bp.route('/questions/new', methods=['GET', 'POST'])
@admin_required
def admin_question_new():
    if request.method == 'GET':
        return render_template(
            'dashboard/admin/question_edit.html',
            question=None,
            subjects=get_all_subjects(),
        )

    validate_csrf()

    subject_code = (request.form.get('subject_code') or '').strip()
    question_text = (request.form.get('question_text') or '').strip()
    option_a = (request.form.get('option_a') or '').strip()
    option_b = (request.form.get('option_b') or '').strip()
    option_c = (request.form.get('option_c') or '').strip()
    option_d = (request.form.get('option_d') or '').strip()
    option_e = (request.form.get('option_e') or '').strip()
    option_f = (request.form.get('option_f') or '').strip()
    correct_answer = (request.form.get('correct_answer') or '').strip().upper()
    difficulty = request.form.get('difficulty') or 1
    chapter = (request.form.get('chapter') or '').strip()
    tags = (request.form.get('tags') or '').strip()
    explanation = (request.form.get('explanation') or '').strip()
    status = (request.form.get('status') or 'active').strip()

    pdf_code = _normalize_pdf_code(request.form.get('pdf_code'))
    pdf_page = _normalize_pdf_page(request.form.get('pdf_page'))

    # Validation
    errors = []
    if not subject_code or not get_subject(subject_code):
        errors.append('Invalid or missing subject.')
    if not question_text:
        errors.append('Question text is required.')
    if not option_a or not option_b or not option_c:
        errors.append('Options A, B, and C are required.')
    if correct_answer not in ('A', 'B', 'C', 'D', 'E', 'F'):
        errors.append('Please select the correct answer.')

    if pdf_code and not _validate_pdf_code_format(pdf_code):
        errors.append('Invalid PDF code format. Expected XXXX-XXXX (e.g. A3B9-X7K2).')
    if pdf_page and not pdf_code:
        pdf_page = None
        flash('Page number was ignored because no PDF code was provided.', 'warning')

    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect(url_for('admin.admin_question_new'))

    options = {'A': option_a, 'B': option_b, 'C': option_c}
    if option_d: options['D'] = option_d
    if option_e: options['E'] = option_e
    if option_f: options['F'] = option_f

    data = {
        'subject_code': subject_code,
        'question_text': question_text,
        'options': options,
        'correct_answer': correct_answer,
        'difficulty': int(difficulty) if difficulty else 1,
        'chapter': chapter,
        'tags': tags,
        'explanation': explanation,
        'pdf_code': pdf_code,
        'pdf_page': pdf_page,
        'status': status,
        'created_by': session['user_id'],
        'updated_by': session['user_id'],
    }

    if create_question(data):
        flash('Question added successfully.', 'success')
        log_admin_action('question.create', f"Added question for {subject_code}", 'info')
        return redirect(url_for('admin.admin_questions'))

    flash('Error adding question.', 'error')
    return redirect(url_for('admin.admin_question_new'))


# ============================================
# QUESTIONS — EDIT
# ============================================

@admin_bp.route('/questions/<int:question_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_question_edit(question_id):
    question = get_question_by_id(question_id)
    if not question:
        flash('Question not found.', 'error')
        return redirect(url_for('admin.admin_questions'))

    if request.method == 'GET':
        pdf_info = None
        if question.get('pdf_code'):
            lookup = check_pdf_codes_exist([question['pdf_code']])
            pdf_info = lookup.get(question['pdf_code'])
        return render_template(
            'dashboard/admin/question_edit.html',
            question=question,
            subjects=get_all_subjects(),
            pdf_info=pdf_info,
        )

    validate_csrf()

    subject_code = (request.form.get('subject_code') or '').strip()
    question_text = (request.form.get('question_text') or '').strip()
    option_a = (request.form.get('option_a') or '').strip()
    option_b = (request.form.get('option_b') or '').strip()
    option_c = (request.form.get('option_c') or '').strip()
    option_d = (request.form.get('option_d') or '').strip()
    option_e = (request.form.get('option_e') or '').strip()
    option_f = (request.form.get('option_f') or '').strip()
    correct_answer = (request.form.get('correct_answer') or '').strip().upper()
    difficulty = request.form.get('difficulty') or 1
    chapter = (request.form.get('chapter') or '').strip()
    tags = (request.form.get('tags') or '').strip()
    explanation = (request.form.get('explanation') or '').strip()
    status = (request.form.get('status') or 'active').strip()

    pdf_code = _normalize_pdf_code(request.form.get('pdf_code'))
    pdf_page = _normalize_pdf_page(request.form.get('pdf_page'))

    errors = []
    if not subject_code or not get_subject(subject_code):
        errors.append('Invalid or missing subject.')
    if not question_text:
        errors.append('Question text is required.')
    if not option_a or not option_b or not option_c:
        errors.append('Options A, B, and C are required.')
    if correct_answer not in ('A', 'B', 'C', 'D', 'E', 'F'):
        errors.append('Please select the correct answer.')
    if pdf_code and not _validate_pdf_code_format(pdf_code):
        errors.append('Invalid PDF code format. Expected XXXX-XXXX.')
    if pdf_page and not pdf_code:
        pdf_page = None
        flash('Page number was ignored because no PDF code was provided.', 'warning')

    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect(url_for('admin.admin_question_edit', question_id=question_id))

    options = {'A': option_a, 'B': option_b, 'C': option_c}
    if option_d: options['D'] = option_d
    if option_e: options['E'] = option_e
    if option_f: options['F'] = option_f

    data = {
        'subject_code': subject_code,
        'question_text': question_text,
        'options': options,
        'correct_answer': correct_answer,
        'difficulty': int(difficulty) if difficulty else 1,
        'chapter': chapter,
        'tags': tags,
        'explanation': explanation,
        'pdf_code': pdf_code,
        'pdf_page': pdf_page,
        'status': status,
        'updated_by': session['user_id'],
    }

    if update_question(question_id, data):
        flash('Question updated.', 'success')
        log_admin_action('question.update', f"Updated question #{question_id}", 'info')
        return redirect(url_for('admin.admin_questions'))

    flash('Error updating question.', 'error')
    return redirect(url_for('admin.admin_question_edit', question_id=question_id))


# ============================================
# QUESTIONS — DELETE / ARCHIVE
# ============================================

@admin_bp.route('/questions/<int:question_id>/delete', methods=['POST'])
@admin_required
def delete_question_route(question_id):
    validate_csrf()
    if delete_question(question_id):
        flash('Question archived.', 'success')
        log_admin_action('question.archive', f"Archived question #{question_id}", 'info')
    else:
        flash('Error archiving question.', 'error')
    return redirect(url_for('admin.admin_questions'))


# ============================================
# PDF CODE LOOKUP (AJAX — for edit + bulk)
# ============================================

@admin_bp.route('/questions/pdf-lookup', methods=['POST'])
@admin_required
def pdf_lookup():
    validate_csrf()
    data = request.get_json(silent=True) or {}
    codes = data.get('codes') or []
    if not isinstance(codes, list):
        codes = [codes]

    normalized = [_normalize_pdf_code(c) for c in codes]
    normalized = [c for c in normalized if c]

    if not normalized:
        return jsonify({'results': {}})

    results = check_pdf_codes_exist(normalized)
    return jsonify({'results': results})


# ============================================
# BULK IMPORT
# ============================================

@admin_bp.route('/bulk-import', methods=['GET', 'POST'])
@admin_required
def bulk_import():
    from subjects_config import get_all_subject_codes
    all_subject_codes = get_all_subject_codes()

    if request.method == 'POST':
        validate_csrf()

        json_data = request.form.get('json_data', '').strip()
        file_data = request.files.get('json_file')

        if file_data and file_data.filename:
            try:
                data = json.loads(file_data.read().decode('utf-8'))
            except Exception as e:
                flash(f'Error reading file: {str(e)}', 'error')
                return render_template('dashboard/admin/bulk_import.html')
        elif json_data:
            try:
                data = json.loads(json_data)
            except json.JSONDecodeError as e:
                flash(f'Invalid JSON format: {str(e)}', 'error')
                return render_template('dashboard/admin/bulk_import.html')
        else:
            flash('Please paste JSON or upload a file.', 'error')
            return render_template('dashboard/admin/bulk_import.html')

        if 'metadata' not in data:
            flash('Missing "metadata" section.', 'error')
            return render_template('dashboard/admin/bulk_import.html')
        if 'questions' not in data or not data['questions']:
            flash('Missing or empty "questions" array.', 'error')
            return render_template('dashboard/admin/bulk_import.html')

        subject_code = data['metadata'].get('subject_code', '').strip()
        if not subject_code:
            flash('subject_code is required.', 'error')
            return render_template('dashboard/admin/bulk_import.html')
        if subject_code not in all_subject_codes:
            available = ', '.join(all_subject_codes)
            flash(f'Subject code "{subject_code}" not found. Available: {available}', 'error')
            return render_template('dashboard/admin/bulk_import.html')

        chapter = data['metadata'].get('chapter', '').strip()
        default_pdf_code = _normalize_pdf_code(data['metadata'].get('pdf_code'))

        if default_pdf_code and not _validate_pdf_code_format(default_pdf_code):
            flash(f'Invalid PDF code format in metadata: "{default_pdf_code}".', 'error')
            return render_template('dashboard/admin/bulk_import.html')

        questions_to_import = []
        errors = []
        duplicates = []
        warnings = []

        for idx, q in enumerate(data['questions'], 1):
            if not q.get('question', '').strip():
                errors.append({'index': idx, 'question': 'Unknown', 'error': 'Question text is required'})
                continue
            if not q.get('options') or len(q['options']) < 3:
                errors.append({'index': idx, 'question': q.get('question', 'Unknown'), 'error': 'Minimum 3 options required'})
                continue
            if len(q['options']) > 6:
                errors.append({'index': idx, 'question': q.get('question', 'Unknown'), 'error': 'Maximum 6 options allowed'})
                continue
            if not q.get('correct') or q['correct'] < 1 or q['correct'] > len(q['options']):
                errors.append({'index': idx, 'question': q.get('question', 'Unknown'), 'error': 'Invalid correct answer index'})
                continue
            difficulty = q.get('difficulty', 1)
            if difficulty < 1 or difficulty > 5:
                errors.append({'index': idx, 'question': q.get('question', 'Unknown'), 'error': 'Difficulty must be 1-5'})
                continue

            question_text = q['question'].strip()
            if check_question_exists(question_text, subject_code):
                duplicates.append({'index': idx, 'question': question_text, 'error': 'Duplicate question'})
                continue

            pdf_code, pdf_page, orphan, invalid = _resolve_pdf_for_import(q, default_pdf_code)
            if invalid:
                errors.append({
                    'index': idx,
                    'question': question_text[:60],
                    'error': 'Invalid PDF code format (expected XXXX-XXXX)'
                })
                continue
            if orphan:
                warnings.append({'index': idx, 'message': f'Q{idx}: pdf_page set without pdf_code — page dropped'})

            option_labels = ['A', 'B', 'C', 'D', 'E', 'F']
            options_dict = {option_labels[i]: opt.strip()
                            for i, opt in enumerate(q['options']) if i < len(option_labels)}

            questions_to_import.append({
                'subject_code': subject_code,
                'question_text': question_text,
                'options': options_dict,
                'correct_answer': option_labels[q['correct'] - 1],
                'difficulty': difficulty,
                'chapter': chapter,
                'tags': ','.join(q.get('tags', [])),
                'explanation': q.get('explanation', '').strip(),
                'pdf_code': pdf_code,
                'pdf_page': pdf_page,
                'created_by': session['user_id'],
                'updated_by': session['user_id'],
            })

        if errors or duplicates:
            return render_template('dashboard/admin/bulk_import.html',
                                 preview=True,
                                 valid_questions=questions_to_import,
                                 errors=errors, duplicates=duplicates,
                                 warnings=warnings, subject_code=subject_code,
                                 chapter=chapter, default_pdf_code=default_pdf_code,
                                 total_questions=len(data['questions']))

        if questions_to_import:
            result = bulk_create_questions(questions_to_import, session['user_id'])
            if result['imported'] > 0:
                flash(f'✅ {result["imported"]} questions imported!', 'success')
            if result['errors']:
                flash(f'⚠️ {len(result["errors"])} questions failed.', 'error')
            if warnings:
                flash(f'⚠️ {len(warnings)} warning(s).', 'warning')
            return redirect(url_for('admin.admin_questions'))
        else:
            flash('No valid questions to import.', 'error')

    return render_template('dashboard/admin/bulk_import.html')


@admin_bp.route('/bulk-preview', methods=['POST'])
@admin_required
def bulk_preview():
    """
    Lightweight preview endpoint used by the live preview panel.
    Returns per-question enrichment including PDF existence checks.
    """
    from subjects_config import get_all_subject_codes
    all_subject_codes = get_all_subject_codes()

    json_data = request.form.get('json_data', '').strip()
    if not json_data:
        return jsonify({'error': 'No JSON data provided'}), 400

    try:
        data = json.loads(json_data)
    except json.JSONDecodeError as e:
        return jsonify({'error': f'Invalid JSON: {str(e)}'}), 400

    if 'metadata' not in data:
        return jsonify({'error': 'Missing metadata section'}), 400
    if 'questions' not in data or not data['questions']:
        return jsonify({'error': 'Missing or empty questions array'}), 400

    subject_code = data['metadata'].get('subject_code', '').strip()
    if not subject_code:
        return jsonify({'error': 'subject_code is required'}), 400
    if subject_code not in all_subject_codes:
        return jsonify({'error': f'Subject code "{subject_code}" not found.'}), 400

    default_pdf_code = _normalize_pdf_code(data['metadata'].get('pdf_code'))
    default_valid = (not default_pdf_code) or _validate_pdf_code_format(default_pdf_code)

    # Collect PDF codes used in questions and check them all at once
    used_codes = set()
    if default_pdf_code:
        used_codes.add(default_pdf_code)
    for q in data['questions']:
        code = _normalize_pdf_code(q.get('pdf_code'))
        if code:
            used_codes.add(code)
    pdf_lookup = check_pdf_codes_exist(list(used_codes)) if used_codes else {}

    preview = []
    linked_count = 0
    unknown_codes = set()

    for idx, q in enumerate(data['questions'], 1):
        code, page, orphan, invalid = _resolve_pdf_for_import(q, default_pdf_code)
        q_text = q.get('question', '') or ''
        q_short = q_text[:70] + ('…' if len(q_text) > 70 else '')

        entry = {
            'index': idx,
            'question': q_short,
            'difficulty': q.get('difficulty', 1),
            'options_count': len(q.get('options', [])),
            'has_explanation': bool(q.get('explanation', '').strip()),
            'tags': ', '.join(q.get('tags', []))[:40] if q.get('tags') else '',
            'pdf_code': code or '',
            'pdf_page': page,
            'pdf_exists': False,
            'pdf_title': '',
            'pdf_source': None,
            'orphan': orphan,
            'invalid_code': invalid,
        }

        if code:
            info = pdf_lookup.get(code)
            if info and info['exists']:
                entry['pdf_exists'] = True
                entry['pdf_title'] = info['title']
                entry['pdf_source'] = info['source']
            else:
                unknown_codes.add(code)
            linked_count += 1

        preview.append(entry)

    return jsonify({
        'subject_code': subject_code,
        'chapter': data['metadata'].get('chapter', ''),
        'default_pdf_code': default_pdf_code or '',
        'default_valid': default_valid,
        'total': len(data['questions']),
        'linked_count': linked_count,
        'unknown_codes': sorted(unknown_codes),
        'preview': preview[:30],
        'truncated': len(preview) > 30,
    })


@admin_bp.route('/bulk-template')
@admin_required
def bulk_template():
    template = {
        "metadata": {
            "subject_code": "geography",
            "chapter": "Chapter 1: Introduction",
            "pdf_code": "GEO1-CH01"
        },
        "questions": [
            {"tags": ["geography", "capitals"], "difficulty": 2,
             "question": "What is the capital of Somalia?",
             "options": ["Mogadishu", "Hargeisa", "Kismayo", "Garowe"],
             "correct": 1,
             "explanation": "Mogadishu has been the capital since 1960.",
             "pdf_page": 12},
            {"tags": ["geography", "rivers"], "difficulty": 3,
             "question": "Which river flows through Mogadishu?",
             "options": ["Shabelle", "Jubba", "Nile", "Tana"],
             "correct": 1, "pdf_page": 14},
            {"tags": ["geography"], "difficulty": 1,
             "question": "How many regions does Somalia have?",
             "options": ["18", "15", "20", "12"],
             "correct": 0},
        ]
    }
    response = jsonify(template)
    response.headers['Content-Disposition'] = 'attachment; filename=bulk_import_template.json'
    response.headers['Content-Type'] = 'application/json'
    return response


# ============================================
# GROUPS (unchanged)
# ============================================

@admin_bp.route('/groups/api', methods=['POST'])
@admin_required
def api_create_group():
    validate_csrf()
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    for field in ['name', 'platform', 'invite_link']:
        if not data.get(field):
            return jsonify({'error': f'{field} is required'}), 400
    data['created_by'] = session['user_id']
    success, group_id = svc_create_group(session['user_id'], data)
    if success:
        return jsonify({'success': True, 'message': 'Group created', 'group_id': group_id})
    return jsonify({'error': 'Failed to create group'}), 500


@admin_bp.route('/groups/api/<int:group_id>', methods=['PUT'])
@admin_required
def api_update_group(group_id):
    validate_csrf()
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    if update_group(session['user_id'], group_id, data):
        return jsonify({'success': True, 'message': 'Group updated'})
    return jsonify({'error': 'Failed to update group'}), 500


@admin_bp.route('/groups/api/<int:group_id>', methods=['DELETE'])
@admin_required
def api_delete_group(group_id):
    validate_csrf()
    if svc_delete_group(session['user_id'], group_id):
        return jsonify({'success': True, 'message': 'Group deleted'})
    return jsonify({'error': 'Failed to delete group'}), 500


@admin_bp.route('/groups/api/<int:group_id>', methods=['GET'])
@admin_required
def api_get_group(group_id):
    group = get_group_by_id(group_id)
    return jsonify(group) if group else (jsonify({'error': 'Group not found'}), 404)


@admin_bp.route('/groups/api/<int:group_id>/toggle-active', methods=['POST'])
@admin_required
def api_toggle_active(group_id):
    validate_csrf()
    if toggle_active(session['user_id'], group_id):
        return jsonify({'success': True, 'message': 'Status toggled'})
    return jsonify({'error': 'Failed to toggle status'}), 500


@admin_bp.route('/groups/api/<int:group_id>/toggle-featured', methods=['POST'])
@admin_required
def api_toggle_featured(group_id):
    validate_csrf()
    if toggle_featured(session['user_id'], group_id):
        return jsonify({'success': True, 'message': 'Featured toggled'})
    return jsonify({'error': 'Failed to toggle featured'}), 500


@admin_bp.route('/groups/api/bulk', methods=['POST'])
@admin_required
def api_bulk_action():
    validate_csrf()
    data = request.get_json()
    action = data.get('action')
    group_ids = data.get('group_ids', [])
    if not action or not group_ids:
        return jsonify({'error': 'Missing action or group IDs'}), 400
    results = {'success': 0, 'failed': 0}
    for gid in group_ids:
        try:
            if action == 'activate':
                ok = update_group(session['user_id'], gid, {'is_active': 1})
            elif action == 'deactivate':
                ok = update_group(session['user_id'], gid, {'is_active': 0})
            elif action == 'feature':
                ok = update_group(session['user_id'], gid, {'is_featured': 1})
            elif action == 'delete':
                ok = svc_delete_group(session['user_id'], gid)
            else:
                return jsonify({'error': 'Invalid action'}), 400
            results['success' if ok else 'failed'] += 1
        except Exception:
            results['failed'] += 1
    return jsonify({'success': True,
                    'message': f'Completed: {results["success"]} succeeded, {results["failed"]} failed'})


@admin_bp.route('/groups')
@admin_required
def admin_groups():
    search = request.args.get('search', '')
    platform = request.args.get('platform', '')
    category = request.args.get('category', '')
    status = request.args.get('status', '')
    page = int(request.args.get('page', 1))
    groups, total = get_admin_group_list(
        search=search, platform=platform, category=category,
        status=status, page=page, per_page=20
    )
    stats = get_group_stats()
    categories = get_group_categories_with_count()
    total_pages = (total + 20 - 1) // 20 if total > 0 else 1
    return render_template('dashboard/admin/groups.html',
                         groups=groups, stats=stats, categories=categories,
                         search=search, platform=platform, category=category,
                         status=status, page=page, total_pages=total_pages)


@admin_bp.route('/groups/analytics')
@admin_required
def groups_analytics():
    stats = get_group_stats()
    groups, _ = get_admin_group_list(per_page=10)
    top_groups = sorted(groups, key=lambda x: x.get('click_count', 0), reverse=True)[:10]
    from db import get_group_platforms_with_count
    platforms = get_group_platforms_with_count()
    return render_template('dashboard/admin/groups_analytics.html',
                         stats=stats, top_groups=top_groups, platforms=platforms)


@admin_bp.route('/groups/audit')
@admin_required
def groups_audit():
    group_id = request.args.get('group_id', type=int)
    admin_id = request.args.get('admin_id', type=int)
    logs = get_group_audit_log(group_id=group_id, admin_id=admin_id, limit=100)
    return render_template('dashboard/admin/groups_audit.html',
                         logs=logs, group_id=group_id, admin_id=admin_id)


# ============================================
# PDFS (unchanged)
# ============================================

@admin_bp.route('/pdfs')
@admin_required
def admin_pdfs():
    return render_template('dashboard/admin/pdfs.html', pdfs=get_all_pdfs())


@admin_bp.route('/pdfs/add', methods=['POST'])
@admin_required
def add_pdf():
    validate_csrf()
    code = request.form.get('code', '').strip()
    if not code:
        flash('Code is required.', 'error')
        return redirect(url_for('admin.admin_pdfs'))
    if get_pdf_by_code(code):
        flash('This code already exists.', 'error')
        return redirect(url_for('admin.admin_pdfs'))
    data = {
        'code': code,
        'title': request.form.get('title', '').strip(),
        'description': request.form.get('description', '').strip(),
        'curriculum': request.form.get('curriculum', 'PL'),
        'class': request.form.get('class', ''),
        'subject': request.form.get('subject', '').strip(),
        'chapter': request.form.get('chapter', '').strip(),
        'tags': request.form.get('tags', '').strip(),
        'is_premium': 1 if request.form.get('is_premium') == 'on' else 0,
        'file_url': request.form.get('file_url', '').strip() or None,
        'uploaded_by': request.form.get('uploaded_by', 'NUUN'),
    }
    if not data['title'] or not data['subject']:
        flash('Title and Subject are required.', 'error')
    elif create_main_pdf(data):
        flash('PDF added successfully!', 'success')
        log_admin_action('pdf.create', f"Added PDF {data['title']}", 'info')
    else:
        flash('Error adding PDF.', 'error')
    return redirect(url_for('admin.admin_pdfs'))


@admin_bp.route('/pdfs/delete/<pdf_id>', methods=['POST'])
@admin_required
def delete_pdf(pdf_id):
    validate_csrf()
    if delete_main_pdf(pdf_id):
        flash('PDF deleted.', 'success')
        log_admin_action('pdf.delete', f"Deleted PDF {pdf_id}", 'info')
    else:
        flash('Error deleting PDF.', 'error')
    return redirect(url_for('admin.admin_pdfs'))


# ============================================
# ANNOUNCEMENT
# ============================================

@admin_bp.route('/announcement', methods=['GET', 'POST'])
@admin_required
def admin_announcement():
    if request.method == 'POST':
        validate_csrf()
        title = request.form.get('title', '').strip()
        body = request.form.get('body', '').strip()
        link = request.form.get('link', '').strip()
        if not title or not body:
            flash('Title and body are required.', 'error')
            return render_template('dashboard/admin/announcement.html')
        send_notification_to_all('admin', title, body, link or '/dashboard', '📢', force=True)
        flash('✅ Announcement sent to all users!', 'success')
        log_admin_action('announcement.send', f"Sent announcement: {title}", 'info')
        return redirect(url_for('admin.dashboard'))
    return render_template('dashboard/admin/announcement.html')


# ============================================
# REPORTS
# ============================================

@admin_bp.route('/reports')
@admin_required
def reports():
    status = request.args.get('status', 'pending')
    page = int(request.args.get('page', 1))
    per_page = 20
    offset = (page - 1) * per_page
    if status == 'pending':
        reports_list = get_pending_reports(limit=per_page, offset=offset)
        total = count_reports('pending')
    else:
        reports_list = get_all_reports(limit=per_page, offset=offset,
                                       status=status if status != 'all' else None)
        total = count_reports(status if status != 'all' else None)
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    return render_template('dashboard/admin/reports.html',
                         reports=reports_list, status=status, page=page,
                         total_pages=total_pages, total=total)


@admin_bp.route('/reports/<int:report_id>/resolve', methods=['POST'])
@admin_required
def resolve_report_route(report_id):
    validate_csrf()
    reply = request.form.get('reply', '').strip()
    if resolve_report(report_id, session['user_id'], reply):
        flash('Report resolved.', 'success')
        report = get_report_by_id(report_id)
        if report and reply:
            from db import create_notification
            create_notification(report['user_id'], 'admin_reply', 'Report Update',
                                f'Admin replied: {reply[:100]}', '/quiz', '📬')
    else:
        flash('Failed to resolve report.', 'error')
    return redirect(url_for('admin.reports', status='pending'))


@admin_bp.route('/reports/<int:report_id>/dismiss', methods=['POST'])
@admin_required
def dismiss_report_route(report_id):
    validate_csrf()
    reply = request.form.get('reply', '').strip()
    flash('Report dismissed.' if dismiss_report(report_id, session['user_id'], reply)
          else 'Failed to dismiss report.',
          'success' if dismiss_report(report_id, session['user_id'], reply) else 'error')
    return redirect(url_for('admin.reports', status='pending'))